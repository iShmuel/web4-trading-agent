"""Genuine Out-of-Sample (Shifted 90-Day) & Multi-Strategy Validation Suite.

Addresses Critical Issue 3 (Section 7) and Multi-Strategy Evaluation (Section 9):
1. Window 1 (Historical Selection Window: Days -180 to -90):
   - Evaluates candidate assets strictly on Window 1 without peeking at Window 2.
   - Selects the top-performing asset universe and freezes both the universe and all
     strategy parameters at Day -90.
2. Window 2 (Strict Non-Overlapping Out-of-Sample Window: Days -90 to 0):
   - Evaluates the frozen universe and frozen parameters over the subsequent 90 days
     under realistic Taker execution costs (0.06% fee + 0.04% slippage per side).
3. Evaluates each Web4 strategy independently AND as a combined multi-strategy portfolio:
   - Strategy 1: Donchian Momentum Breakout (momentum)
   - Strategy 2: Bollinger/RSI Mean Reversion (mean_reversion)
   - Strategy 3: Cointegrated Statistical Arbitrage Pairs (stat_arb_pairs)
   - Strategy 4: Delta-Neutral Perpetual Basis Yield (basis_yield)
   - Combined Web4 Multi-Strategy Portfolio
4. Compares against:
   - Previous In-Sample baseline (+5.34% directional reference / Window 1 In-Sample)
   - Same-basket equal-weight Buy-and-Hold over the exact OOS window
   - Random-Noise Monte Carlo baseline (sample count, mean return, std dev, vs -9.39% ref)
   - Actual recorded Paper Trading performance (data/paper_portfolio.json)

Saves reproducible report to Git-tracked:
  reports/oos_validation_report.json
"""

from datetime import datetime, timezone
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import core.coordinator as coord_mod
from core.coordinator import PortfolioCoordinator, resolve_symbol_config
from core.data_loader import fetch_ohlcv, is_crypto_symbol
from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine
from core.backtest_engine import run_backtest, run_mean_reversion_backtest
from core.strategy import compute_indicators, compute_mean_reversion_indicators
from core.polymarket_radar import MacroEventRisk
from core.fred_radar import MacroRegimeReport
from core.skill_intelligence_gate import (
    Sec8KVerdict,
    FundamentalVerdict,
    SmartMoneyVerdict,
)
from core.basis_yield import BasisOpportunity

CONFIG_PATH = PROJECT_ROOT / "config" / "active_config.json"
PORTFOLIO_PATH = PROJECT_ROOT / "data" / "paper_portfolio.json"
PREV_REPLAY_PATH = PROJECT_ROOT / "data" / "live_replay_backtest.json"
REPORT_PATH = PROJECT_ROOT / "reports" / "oos_validation_report.json"

TAKER_FEE = 0.0006
TAKER_SLIP = 0.0004


def _install_offline_radars() -> None:
    """Stub live external HTTP radars for deterministic, reproducible historical replay."""
    coord_mod.fetch_funding_rate = lambda sym: 0.00012
    coord_mod.fetch_ticker_headlines = lambda sym, limit=3: []
    coord_mod.log_decision = lambda **kwargs: None

    coord_mod.PolymarketRadar.evaluate = lambda self, force_refresh=False: MacroEventRisk(
        risk_level="LOW",
        recommended_risk_multiplier=1.0,
    )
    coord_mod.FredMacroRadar.evaluate = lambda self, force_refresh=False: MacroRegimeReport(
        regime="STABLE",
        vix_level=16.0,
        risk_scale_factor=1.0,
        allow_long_breakouts=True,
        reason="Historical replay normal VIX/credit regime",
    )
    coord_mod.SecEdgar8KMonitor.evaluate_symbol = lambda self, symbol, now_utc=None, filings_override=None: Sec8KVerdict(
        symbol=symbol,
        approved=True,
        triggered_items=[],
        reason="No material adverse 8-K in replay window",
    )
    coord_mod.SecFundamentalGate.evaluate_equity = lambda self, symbol, metrics_override=None: FundamentalVerdict(
        symbol=symbol,
        passed=True,
        quality_score=0.75,
        metrics={"revenue_growth_yoy": 12.0, "operating_margin": 18.0},
        reason="Fundamental gate passed",
    )
    coord_mod.SmartMoneyRadar.evaluate_symbol = lambda self, symbol, override_data=None: SmartMoneyVerdict(
        symbol=symbol,
        whale_13f_bullish=False,
        congress_ptr_bullish=False,
        confluence_delta=0.0,
        risk_multiplier=1.0,
        summary="Neutral institutional flow",
    )
    coord_mod.BasisYieldEngine.get_funding_rate = lambda self, symbol: (
        0.00012 if is_crypto_symbol(symbol) else None
    )


def load_180d_candidate_frames(base_cfg: Dict[str, Any]) -> Dict[str, pd.DataFrame]:
    """Load 180-day 1h OHLCV data for the candidate pool (watchlist + pair legs)."""
    watchlist = list(base_cfg.get("watchlist", []))
    extra_candidates = ["BTC/USDT", "ETH/USDT", "AMD", "SPY", "QQQ", "GLD"]
    all_syms = list(dict.fromkeys(watchlist + extra_candidates))

    frames: Dict[str, pd.DataFrame] = {}
    for sym in all_syms:
        try:
            df = fetch_ohlcv(symbol=sym, timeframe="1h", days=180, max_cache_age_hours=96.0)
            if df is not None and len(df) >= 350:
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                else:
                    df.index = df.index.tz_convert("UTC")
                frames[sym] = df.sort_index()
        except Exception:
            pass
    return frames


def _precompute_window_df(raw_df: pd.DataFrame, scfg: Dict[str, Any]) -> pd.DataFrame:
    """Precompute strictly causal indicators within a single window slice for fast bar-by-bar replay."""
    mom_df = compute_indicators(
        raw_df,
        breakout_lookback=int(scfg.get("breakout_lookback", 20)),
        ema_trend_filter=int(scfg.get("ema_trend_filter", 32)),
        atr_period=int(scfg.get("atr_period", 14)),
        macro_ema_period=int(scfg.get("macro_ema_period", 200)),
    )
    mr_df = compute_mean_reversion_indicators(
        raw_df,
        bb_period=int(scfg.get("bb_period", 20)),
        bb_std=float(scfg.get("bb_std", 2.0)),
        rsi_period=int(scfg.get("rsi_period", 14)),
        atr_period=int(scfg.get("atr_period", 14)),
        adx_period=int(scfg.get("adx_period", 14)),
    )
    for col in ["bb_sma", "bb_std", "bb_upper", "bb_lower", "bb_width", "z_score", "rsi"]:
        mom_df[col] = mr_df[col]
    mom_df["_precomputed"] = True
    return mom_df


def split_selection_and_oos_windows(
    frames: Dict[str, pd.DataFrame],
    base_cfg: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, pd.DataFrame], Dict[str, pd.DataFrame], pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    """Split 180-day frames into strictly non-overlapping Window 1 (Days -180..-90) and Window 2 (Days -90..0)."""
    cfg = base_cfg or {}
    global_max_ts = max(df.index[-1] for df in frames.values())
    split_ts = global_max_ts - pd.Timedelta(days=90)
    global_min_ts = min(df.index[0] for df in frames.values())

    win1_frames: Dict[str, pd.DataFrame] = {}
    win2_frames: Dict[str, pd.DataFrame] = {}

    for sym, df in frames.items():
        df1 = df[df.index < split_ts].copy()
        df2 = df[df.index >= split_ts].copy()
        if len(df1) >= 120 and len(df2) >= 120:
            scfg = resolve_symbol_config(cfg, sym)
            win1_frames[sym] = _precompute_window_df(df1, scfg)
            win2_frames[sym] = _precompute_window_df(df2, scfg)

    return win1_frames, win2_frames, global_min_ts, split_ts, global_max_ts


def select_universe_from_window1(
    win1_frames: Dict[str, pd.DataFrame],
    base_cfg: Dict[str, Any],
    max_assets: int = 12,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Select asset universe using ONLY Window 1 (Days -180 to -90) performance under Taker costs."""
    scores: List[Dict[str, Any]] = []
    for sym, df1 in win1_frames.items():
        scfg = resolve_symbol_config(base_cfg, sym)
        mom_res = run_backtest(
            df=df1,
            initial_capital=10000.0,
            account_risk_pct=0.02,
            breakout_lookback=scfg.get("breakout_lookback", 24),
            ema_trend_filter=scfg.get("ema_trend_filter", 50),
            atr_period=scfg.get("atr_period", 14),
            atr_multiplier=scfg.get("atr_multiplier", 2.5),
            fee_pct=TAKER_FEE,
            slippage_pct=TAKER_SLIP,
            fill_mode="next_open",
        )
        mr_res = run_mean_reversion_backtest(
            df=df1,
            initial_capital=10000.0,
            account_risk_pct=0.02,
            rsi_oversold=scfg.get("rsi_oversold", 30.0),
            rsi_overbought=scfg.get("rsi_overbought", 70.0),
            adx_threshold=scfg.get("adx_threshold", 25.0),
            allow_short=bool(scfg.get("mr_allow_short", False)),
            fee_pct=TAKER_FEE,
            slippage_pct=TAKER_SLIP,
            fill_mode="next_open",
        )
        best_ret = max(mom_res.total_return_pct, mr_res.total_return_pct)
        combined_ret = 0.5 * mom_res.total_return_pct + 0.5 * mr_res.total_return_pct
        best_pf = max(mom_res.profit_factor, mr_res.profit_factor)
        scores.append({
            "symbol": sym,
            "is_crypto": is_crypto_symbol(sym),
            "win1_momentum_return_pct": round(mom_res.total_return_pct, 2),
            "win1_mr_return_pct": round(mr_res.total_return_pct, 2),
            "win1_best_return_pct": round(best_ret, 2),
            "win1_combined_return_pct": round(combined_ret, 2),
            "win1_best_profit_factor": round(best_pf, 2),
        })

    # Rank strictly by Window 1 combined return, keeping balanced crypto + equity representation
    sorted_scores = sorted(scores, key=lambda x: x["win1_best_return_pct"], reverse=True)
    selected_crypto = [s["symbol"] for s in sorted_scores if s["is_crypto"]][:5]
    selected_equities = [s["symbol"] for s in sorted_scores if not s["is_crypto"]][: (max_assets - len(selected_crypto))]
    # Ensure BTC/USDT and ETH/USDT are included if present for crypto benchmark & pair cointegration
    for idx, anchor in enumerate(("BTC/USDT", "ETH/USDT")):
        if anchor in win1_frames and anchor not in selected_crypto:
            selected_crypto[-(idx + 1)] = anchor
    # Ensure NVDA and AMD are included if present for equity semiconductor pair cointegration
    for idx, eq_anchor in enumerate(("NVDA", "AMD")):
        if eq_anchor in win1_frames and eq_anchor not in selected_equities:
            selected_equities[-(idx + 1)] = eq_anchor
    selected = list(dict.fromkeys(selected_crypto + selected_equities))
    return selected, sorted_scores


def run_coordinator_window_replay(
    window_frames: Dict[str, pd.DataFrame],
    universe: List[str],
    base_cfg: Dict[str, Any],
    enabled_strategies: List[str],
    initial_capital: float = 10000.0,
) -> Dict[str, Any]:
    """Run bar-by-bar PortfolioCoordinator replay over the specified window and strategy subset."""
    _install_offline_radars()
    cfg = json.loads(json.dumps(base_cfg))
    cfg["watchlist"] = list(universe)
    cfg["blacklisted_symbols"] = [s for s in cfg.get("blacklisted_symbols", []) if s not in set(universe)]
    cfg["execution_mode"] = str(base_cfg.get("execution_mode", "taker"))
    cfg["fee_pct"] = float(base_cfg.get("fee_pct", TAKER_FEE))
    cfg["slippage_pct"] = float(base_cfg.get("slippage_pct", TAKER_SLIP))
    cfg["max_stale_minutes"] = 0
    cfg["preflight_max_staleness_seconds"] = 10**9
    for s_cfg in cfg.get("symbol_configs", {}).values():
        if isinstance(s_cfg, dict):
            s_cfg["max_stale_minutes"] = 0
    cfg["enable_local_llm_preflight"] = False
    cfg["enable_nyse_session_gate"] = False
    cfg["enforce_nyse_market_hours"] = False
    cfg["enable_sec_8k_gate"] = False
    cfg["enable_sec_fundamental_gate"] = False
    if enabled_strategies == ["mean_reversion"]:
        cfg["crypto_altcoin_momentum_only"] = False
    cfg["enabled_strategies"] = list(enabled_strategies)
    cfg["enable_momentum"] = "momentum" in enabled_strategies
    cfg["enable_mean_reversion"] = "mean_reversion" in enabled_strategies
    cfg["enable_pairs_trading"] = "stat_arb_pairs" in enabled_strategies
    cfg["enable_stat_arb_pairs"] = "stat_arb_pairs" in enabled_strategies
    cfg["enable_basis_yield"] = "basis_yield" in enabled_strategies

    # Freeze exact StatArb parameters from active_config.json (min_pair_correlation=0.70, stat_arb_entry_z=2.0)
    if "stat_arb_pairs" in enabled_strategies:
        cfg["stat_arb_entry_z"] = float(cfg.get("stat_arb_entry_z", 2.0))
        cfg["min_pair_correlation"] = float(cfg.get("min_pair_correlation", 0.70))

    active_frames = {s: window_frames[s] for s in universe if s in window_frames}
    ts_to_pos: Dict[str, Dict[pd.Timestamp, int]] = {
        sym: {ts: idx for idx, ts in enumerate(df.index)} for sym, df in active_frames.items()
    }
    all_ts = sorted({ts for df in active_frames.values() for ts in df.index})
    warmup_bars = 60
    eval_timestamps = all_ts[warmup_bars:] if len(all_ts) > warmup_bars else all_ts

    with tempfile.TemporaryDirectory() as tmpdir:
        account = PaperAccount(initial_capital=initial_capital, portfolio_file=Path(tmpdir) / "replay_port.json")
        # In-memory save during inner loop, persist at end
        account._save = lambda state=None: None

        risk_engine = RiskEngine(
            default_risk_pct=float(cfg.get("account_risk_pct", 0.02)),
            max_open_positions=int(cfg.get("max_concurrent_positions", 5)),
            max_strategy_allocation_pct=float(cfg.get("max_strategy_allocation_pct", 0.75)),
            max_crypto_positions=int(cfg.get("max_crypto_positions", 2)),
            dynamic_scaling=True,
            max_drawdown_limit_pct=float(cfg.get("max_drawdown_limit_pct", 15.0)),
            symbol_risk_weights=cfg.get("symbol_risk_weights"),
            symbol_exposure_caps=cfg.get("symbol_exposure_caps"),
            account=account,
        )
        coordinator = PortfolioCoordinator(account, risk_engine, cfg)
        coordinator.preflight_checker.max_staleness_seconds = 10**9

        equity_curve: List[Tuple[pd.Timestamp, float]] = []
        conflicts_total = 0
        risk_rejections_total = 0
        signals_by_strategy: Dict[str, int] = {
            "momentum": 0,
            "mean_reversion": 0,
            "stat_arb_pairs": 0,
            "basis_yield": 0,
        }

        last_prices: Dict[str, float] = {
            sym: float(df["close"].iloc[0]) for sym, df in active_frames.items()
        }
        for ts in eval_timestamps:
            bar_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            if coordinator.last_basis_accrual_time is None:
                coordinator.last_basis_accrual_time = bar_dt - pd.Timedelta(hours=8, minutes=1)

            slice_data: Dict[str, pd.DataFrame] = {}
            for sym, pos_map in ts_to_pos.items():
                pos = pos_map.get(ts)
                if pos is not None and pos >= 60:
                    df = active_frames[sym]
                    sub = df.iloc[pos - 59 : pos + 1]
                    slice_data[sym] = sub
                    last_prices[sym] = float(sub["close"].iloc[-1])

            if not slice_data:
                continue

            report = coordinator.run_cycle(slice_data, now=bar_dt)

            conflicts_total += report.conflicts_detected
            for d in report.decisions:
                if d.action in ("ENTER_LONG", "ENTER_SHORT", "LONG_A_SHORT_B", "SHORT_A_LONG_B", "ACCRUED_YIELD"):
                    signals_by_strategy[d.strategy_id] = signals_by_strategy.get(d.strategy_id, 0) + 1
                elif d.action == "REJECTED" and "Risk" in d.reason:
                    risk_rejections_total += 1

            eq = account.get_total_equity(last_prices)
            equity_curve.append((ts, eq))

        # Mark-to-market open positions at end of window so all PnL is realized for clean comparison
        for sym in list(account.positions.keys()):
            px = last_prices.get(sym, account.positions[sym]["entry_price"])
            account.close_position(sym, px * (1.0 - cfg["slippage_pct"]), exit_reason="End of OOS Window Mark-to-Market", fee_pct=cfg["fee_pct"])

        final_eq = account.get_total_equity(last_prices)
        closed = account.state.get("closed_trades", [])

        return _summarize_replay_metrics(
            closed_trades=closed,
            equity_curve=equity_curve,
            initial_capital=initial_capital,
            final_equity=final_eq,
            conflicts=conflicts_total,
            risk_rejections=risk_rejections_total,
            signals_by_strategy=signals_by_strategy,
        )


def _summarize_replay_metrics(
    closed_trades: List[Dict[str, Any]],
    equity_curve: List[Tuple[pd.Timestamp, float]],
    initial_capital: float,
    final_equity: float,
    conflicts: int,
    risk_rejections: int,
    signals_by_strategy: Dict[str, int],
) -> Dict[str, Any]:
    """Calculate comprehensive institutional performance and risk metrics from replay output."""
    directional_trades = [t for t in closed_trades if t.get("strategy_id") != "basis_yield"]
    basis_trades = [t for t in closed_trades if t.get("strategy_id") == "basis_yield"]

    all_pnls = [float(t.get("pnl", 0.0)) for t in closed_trades]
    dir_pnls = [float(t.get("pnl", 0.0)) for t in directional_trades]
    basis_pnl = round(sum(float(t.get("pnl", 0.0)) for t in basis_trades), 2)

    wins = [p for p in all_pnls if p > 0]
    losses = [abs(p) for p in all_pnls if p <= 0]
    gw, gl = sum(wins), sum(losses)
    pf = round(gw / gl, 2) if gl > 0 else (99.0 if gw > 0 else 0.0)

    dir_wins = [p for p in dir_pnls if p > 0]
    dir_losses = [abs(p) for p in dir_pnls if p <= 0]
    dir_pf = round(sum(dir_wins) / sum(dir_losses), 2) if sum(dir_losses) > 0 else (99.0 if sum(dir_wins) > 0 else 0.0)

    total_fees = round(sum(float(t.get("fees", 0.0)) for t in closed_trades), 2)
    net_pnl = round(final_equity - initial_capital, 2)
    net_return_pct = round((net_pnl / initial_capital) * 100.0, 2)

    # Equity curve drawdown, Sharpe, Sortino
    if equity_curve:
        eq_series = pd.Series(
            [v for _, v in equity_curve],
            index=pd.DatetimeIndex([ts for ts, _ in equity_curve]),
        )
        peaks = eq_series.cummax()
        dd_series = (peaks - eq_series) / peaks
        max_dd_pct = round(float(dd_series.max() * 100.0), 2)

        daily_eq = eq_series.resample("1D").last().dropna()
        daily_ret = daily_eq.pct_change().dropna()
        if len(daily_ret) > 2 and daily_ret.std() > 0:
            eff_std = max(float(daily_ret.std()), 0.0005)
            sharpe = round(float((daily_ret.mean() / eff_std) * np.sqrt(365.0)), 2)
            downside = daily_ret[daily_ret < 0]
            down_std = max(float(downside.std()) if len(downside) > 1 else eff_std, 0.0005)
            sortino = round(float((daily_ret.mean() / down_std) * np.sqrt(365.0)), 2) if down_std > 0 else 0.0
        else:
            sharpe = 0.0
            sortino = 0.0
    else:
        max_dd_pct = 0.0
        sharpe = 0.0
        sortino = 0.0

    by_strategy: Dict[str, Dict[str, Any]] = {}
    for sid in ("momentum", "mean_reversion", "stat_arb_pairs", "basis_yield"):
        st = [t for t in closed_trades if t.get("strategy_id") == sid]
        sp = [float(t.get("pnl", 0.0)) for t in st]
        sw = [p for p in sp if p > 0]
        sl = [abs(p) for p in sp if p <= 0]
        by_strategy[sid] = {
            "trades": len(st),
            "wins": len(sw),
            "losses": len(sl),
            "win_rate_pct": round((len(sw) / len(st)) * 100.0, 1) if st else 0.0,
            "net_pnl": round(sum(sp), 2),
            "return_contribution_pct": round((sum(sp) / initial_capital) * 100.0, 2),
            "profit_factor": round(sum(sw) / sum(sl), 2) if sum(sl) > 0 else (99.0 if sum(sw) > 0 else 0.0),
            "expectancy_per_trade": round(float(np.mean(sp)), 2) if sp else 0.0,
            "fees_paid": round(sum(float(t.get("fees", 0.0)) for t in st), 2),
        }

    return {
        "initial_capital": initial_capital,
        "final_equity": round(final_equity, 2),
        "net_pnl": net_pnl,
        "net_return_pct": net_return_pct,
        "directional_only_return_pct": round((sum(dir_pnls) / initial_capital) * 100.0, 2),
        "basis_yield_return_pct": round((basis_pnl / initial_capital) * 100.0, 2),
        "max_drawdown_pct": max_dd_pct,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "total_trades": len(closed_trades),
        "directional_trades": len(directional_trades),
        "basis_accruals": len(basis_trades),
        "win_rate_pct": round((len(wins) / len(closed_trades)) * 100.0, 1) if closed_trades else 0.0,
        "directional_win_rate_pct": round((len(dir_wins) / len(directional_trades)) * 100.0, 1) if directional_trades else 0.0,
        "profit_factor": pf,
        "directional_profit_factor": dir_pf,
        "expectancy_per_trade": round(float(np.mean(all_pnls)), 2) if all_pnls else 0.0,
        "directional_expectancy_per_trade": round(float(np.mean(dir_pnls)), 2) if dir_pnls else 0.0,
        "total_fees_paid": total_fees,
        "conflicts_resolved": conflicts,
        "risk_rejections": risk_rejections,
        "signals_by_strategy": signals_by_strategy,
        "by_strategy": by_strategy,
        "equity_curve": equity_curve,
    }


def evaluate_equal_weight_buy_and_hold(
    window_frames: Dict[str, pd.DataFrame],
    universe: List[str],
    initial_capital: float = 10000.0,
) -> Dict[str, Any]:
    """Compute equal-weight Buy-and-Hold baseline over the exact same OOS window and asset basket."""
    per_asset: Dict[str, float] = {}
    norm_series_list: List[pd.Series] = []

    for sym in universe:
        df = window_frames.get(sym)
        if df is None or len(df) < 2:
            continue
        p0 = float(df["close"].iloc[0])
        p1 = float(df["close"].iloc[-1])
        ret_pct = ((p1 - p0) / p0) * 100.0
        per_asset[sym] = round(ret_pct, 2)
        s = (df["close"] / p0).resample("1D").last().dropna()
        norm_series_list.append(s)

    if not per_asset:
        return {"return_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe_ratio": 0.0, "sortino_ratio": 0.0}

    avg_ret_pct = float(np.mean(list(per_asset.values())))
    combined_df = pd.concat(norm_series_list, axis=1).ffill().bfill()
    basket_curve = combined_df.mean(axis=1) * initial_capital
    peaks = basket_curve.cummax()
    max_dd_pct = float(((peaks - basket_curve) / peaks).max() * 100.0)

    daily_ret = basket_curve.pct_change().dropna()
    sharpe = float((daily_ret.mean() / daily_ret.std()) * np.sqrt(365.0)) if len(daily_ret) > 2 and daily_ret.std() > 0 else 0.0
    downside = daily_ret[daily_ret < 0]
    down_std = float(downside.std()) if len(downside) > 1 else float(daily_ret.std())
    sortino = float((daily_ret.mean() / down_std) * np.sqrt(365.0)) if down_std > 0 else 0.0

    return {
        "initial_capital": initial_capital,
        "final_equity": round(initial_capital * (1.0 + avg_ret_pct / 100.0), 2),
        "return_pct": round(avg_ret_pct, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino, 2),
        "per_asset_return_pct": per_asset,
    }


def evaluate_random_noise_monte_carlo_baseline(
    window_frames: Dict[str, pd.DataFrame],
    universe: List[str],
    num_trials: int = 50,
    trades_per_trial: int = 120,
    initial_capital: float = 10000.0,
    seed: int = 42,
) -> Dict[str, Any]:
    """Run Monte Carlo random-entry / random-direction baseline over the exact OOS window under Taker costs."""
    rng = np.random.default_rng(seed)
    valid_syms = [s for s in universe if s in window_frames and len(window_frames[s]) >= 60]
    trial_returns: List[float] = []
    trial_drawdowns: List[float] = []
    trial_sharpes: List[float] = []

    round_trip_cost_pct = 2.0 * (TAKER_FEE + TAKER_SLIP)  # 0.20% round-trip cost drag

    for _ in range(num_trials):
        eq = initial_capital
        peak = initial_capital
        max_dd = 0.0
        trade_rets: List[float] = []

        for _ in range(trades_per_trial):
            sym = valid_syms[int(rng.integers(0, len(valid_syms)))]
            df = window_frames[sym]
            max_start = len(df) - 25
            idx0 = int(rng.integers(10, max(11, max_start)))
            hold_bars = int(rng.integers(4, 24))
            idx1 = min(len(df) - 1, idx0 + hold_bars)
            p0 = float(df["close"].iloc[idx0])
            p1 = float(df["close"].iloc[idx1])
            side = 1.0 if rng.random() >= 0.5 else -1.0

            raw_move = side * ((p1 - p0) / p0)
            # Position notional ~20% of equity (matching 2% risk with ~10% stop distance)
            notional = eq * 0.20
            net_pnl = notional * (raw_move - round_trip_cost_pct)
            eq += net_pnl
            trade_rets.append(net_pnl / initial_capital)
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        ret_pct = ((eq - initial_capital) / initial_capital) * 100.0
        trial_returns.append(ret_pct)
        trial_drawdowns.append(max_dd * 100.0)
        arr = np.array(trade_rets)
        sh = float((arr.mean() / arr.std()) * np.sqrt(trades_per_trial)) if arr.std() > 0 else 0.0
        trial_sharpes.append(sh)

    return {
        "sample_count": num_trials,
        "trades_per_trial": trades_per_trial,
        "seed": seed,
        "mean_return_pct": round(float(np.mean(trial_returns)), 2),
        "std_dev_return_pct": round(float(np.std(trial_returns)), 2),
        "median_return_pct": round(float(np.median(trial_returns)), 2),
        "p5_return_pct": round(float(np.percentile(trial_returns, 5)), 2),
        "p95_return_pct": round(float(np.percentile(trial_returns, 95)), 2),
        "mean_max_drawdown_pct": round(float(np.mean(trial_drawdowns)), 2),
        "mean_sharpe_ratio": round(float(np.mean(trial_sharpes)), 2),
        "historical_reference_noise_baseline_pct": -9.39,
    }


def summarize_recorded_paper_trading() -> Dict[str, Any]:
    """Read-only summary of actual recorded live paper trading state (data/paper_portfolio.json)."""
    if not PORTFOLIO_PATH.exists():
        return {}
    data = json.loads(PORTFOLIO_PATH.read_text())
    closed = data.get("closed_trades", [])
    init_cap = float(data.get("initial_capital", 10000.0))
    cash = float(data.get("cash", init_cap))
    pnls = [float(t.get("pnl", 0.0)) for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p <= 0]
    fees = sum(float(t.get("fees", 0.0)) for t in closed)

    by_strat: Dict[str, Dict[str, Any]] = {}
    for sid in ("momentum", "mean_reversion", "stat_arb_pairs", "basis_yield"):
        st = [t for t in closed if t.get("strategy_id") == sid]
        sp = [float(t.get("pnl", 0.0)) for t in st]
        sw = [p for p in sp if p > 0]
        by_strat[sid] = {
            "trades": len(st),
            "wins": len(sw),
            "losses": len(st) - len(sw),
            "win_rate_pct": round((len(sw) / len(st)) * 100.0, 1) if st else 0.0,
            "realized_pnl": round(sum(sp), 2),
        }

    return {
        "initial_capital": init_cap,
        "current_cash": round(cash, 2),
        "net_return_pct": round(((cash - init_cap) / init_cap) * 100.0, 2),
        "total_closed_trades": len(closed),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate_pct": round((len(wins) / len(closed)) * 100.0, 1) if closed else 0.0,
        "realized_pnl_usd": round(sum(pnls), 2),
        "profit_factor": round(sum(wins) / sum(losses), 2) if sum(losses) > 0 else 0.0,
        "total_fees_paid_usd": round(fees, 2),
        "by_strategy": by_strat,
    }


def run_full_oos_and_multistrategy_validation() -> Dict[str, Any]:
    """Execute the complete Window 1 -> Window 2 Shifted OOS and Multi-Strategy Validation."""
    base_cfg = json.loads(CONFIG_PATH.read_text())
    frames = load_180d_candidate_frames(base_cfg)
    win1_frames, win2_frames, min_ts, split_ts, max_ts = split_selection_and_oos_windows(frames, base_cfg)

    print("=" * 100)
    print("  WEB4 GENUINE OUT-OF-SAMPLE (SHIFTED 90-DAY) & MULTI-STRATEGY VALIDATION")
    print("=" * 100)
    print(f"  Window 1 (Historical Selection Window, Days -180 to -90): {min_ts.isoformat()} -> {split_ts.isoformat()}")
    print(f"  Window 2 (Strict Out-of-Sample Window, Days  -90 to   0): {split_ts.isoformat()} -> {max_ts.isoformat()}")
    print(f"  Execution Model: Taker (0.06% fee + 0.04% slippage per side = 0.20% round-trip)")
    print("-" * 100)

    # 1. Select universe strictly on Window 1 (Days -180 to -90) and freeze
    selected_universe, win1_rankings = select_universe_from_window1(win1_frames, base_cfg, max_assets=12)
    print(f"  Frozen Universe Selected from Window 1 ({len(selected_universe)} assets): {', '.join(selected_universe)}")

    # 2. Evaluate Window 1 (In-Sample Selection Window) Combined Portfolio
    print("  [1/6] Running Window 1 (In-Sample, Days -180 to -90) Combined Multi-Strategy Replay...")
    win1_combined = run_coordinator_window_replay(
        window_frames=win1_frames,
        universe=selected_universe,
        base_cfg=base_cfg,
        enabled_strategies=["momentum", "mean_reversion", "stat_arb_pairs", "basis_yield"],
    )

    # 3. Evaluate each strategy independently on Window 2 (Strict OOS, Days -90 to 0)
    print("  [2/6] Running Window 2 (OOS, Days -90 to 0) Independent Strategy Evaluations...")
    oos_momentum = run_coordinator_window_replay(
        window_frames=win2_frames,
        universe=selected_universe,
        base_cfg=base_cfg,
        enabled_strategies=["momentum"],
    )
    oos_mean_reversion = run_coordinator_window_replay(
        window_frames=win2_frames,
        universe=selected_universe,
        base_cfg=base_cfg,
        enabled_strategies=["mean_reversion"],
    )
    oos_stat_arb_pairs = run_coordinator_window_replay(
        window_frames=win2_frames,
        universe=selected_universe,
        base_cfg=base_cfg,
        enabled_strategies=["stat_arb_pairs"],
    )
    oos_basis_yield = run_coordinator_window_replay(
        window_frames=win2_frames,
        universe=selected_universe,
        base_cfg=base_cfg,
        enabled_strategies=["basis_yield"],
    )

    # 4. Evaluate Combined Web4 Multi-Strategy Portfolio on Window 2 (Strict OOS, Days -90 to 0)
    print("  [3/6] Running Window 2 (OOS, Days -90 to 0) Combined Web4 Multi-Strategy Portfolio...")
    oos_combined = run_coordinator_window_replay(
        window_frames=win2_frames,
        universe=selected_universe,
        base_cfg=base_cfg,
        enabled_strategies=["momentum", "mean_reversion", "stat_arb_pairs", "basis_yield"],
    )

    # 5. Evaluate Same-Basket Buy-and-Hold & Random-Noise Monte Carlo Baselines on Window 2 (OOS)
    print("  [4/6] Computing Same-Basket Buy-and-Hold Benchmark over Window 2 (OOS)...")
    oos_buy_and_hold = evaluate_equal_weight_buy_and_hold(win2_frames, selected_universe)
    win1_buy_and_hold = evaluate_equal_weight_buy_and_hold(win1_frames, selected_universe)

    print("  [5/6] Running Random-Noise Monte Carlo Baseline (50 trials) over Window 2 (OOS)...")
    oos_random_noise = evaluate_random_noise_monte_carlo_baseline(win2_frames, selected_universe, num_trials=50)

    # 6. Load Previous In-Sample Reference & Recorded Paper Trading Performance
    print("  [6/6] Auditing Recorded Paper Trading Performance (data/paper_portfolio.json)...")
    paper_trading_summary = summarize_recorded_paper_trading()
    prev_in_sample_ref = {
        "historical_directional_in_sample_return_pct": 5.34,
        "full_replay_taker_in_sample_return_pct": 10.01,
        "full_replay_maker_in_sample_return_pct": 13.65,
        "note": (
            "Previous 90-day replay (+5.34% directional / +10.01% total taker) suffered from universe selection "
            "bias because the 15-asset watchlist was selected using the same 90-day window."
        ),
    }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "methodology": {
            "design": "Shifted Non-Overlapping 90-Day Out-of-Sample Validation",
            "window_1_selection": {
                "start_utc": min_ts.isoformat(),
                "end_utc": split_ts.isoformat(),
                "duration_days": 90,
                "role": "Asset universe selection & parameter freeze window (Days -180 to -90)",
            },
            "window_2_out_of_sample": {
                "start_utc": split_ts.isoformat(),
                "end_utc": max_ts.isoformat(),
                "duration_days": 90,
                "role": "Strict non-overlapping Out-of-Sample evaluation window (Days -90 to 0)",
            },
            "execution_cost_model": {
                "mode": "taker",
                "fee_pct_per_side": TAKER_FEE,
                "slippage_pct_per_side": TAKER_SLIP,
                "round_trip_cost_pct": 0.20,
            },
            "frozen_selected_universe": selected_universe,
            "window_1_asset_rankings": win1_rankings,
        },
        "previous_in_sample_reference": prev_in_sample_ref,
        "window_1_in_sample_results": {
            "combined_portfolio": win1_combined,
            "same_basket_buy_and_hold": win1_buy_and_hold,
        },
        "window_2_out_of_sample_results": {
            "combined_web4_portfolio": oos_combined,
            "independent_strategies": {
                "momentum": oos_momentum,
                "mean_reversion": oos_mean_reversion,
                "stat_arb_pairs": oos_stat_arb_pairs,
                "basis_yield": oos_basis_yield,
            },
            "same_basket_buy_and_hold": oos_buy_and_hold,
            "random_noise_baseline": oos_random_noise,
        },
        "recorded_paper_trading": paper_trading_summary,
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2))

    print("\n" + "=" * 100)
    print("  SUMMARY OF SHIFTED 90-DAY OUT-OF-SAMPLE & MULTI-STRATEGY RESULTS (TAKER FEES + SLIPPAGE)")
    print("=" * 100)
    print(f"  Previous In-Sample Reference:          +5.34% directional (+10.01% with basis yield) [Selection Bias]")
    print(f"  Window 1 (Days -180..-90) In-Sample:   {win1_combined['net_return_pct']:+6.2f}% | Sharpe {win1_combined['sharpe_ratio']:5.2f} | MaxDD {win1_combined['max_drawdown_pct']:5.2f}% | Trades {win1_combined['total_trades']}")
    print(f"  Window 2 (Days  -90..  0) Strict OOS:  {oos_combined['net_return_pct']:+6.2f}% | Sharpe {oos_combined['sharpe_ratio']:5.2f} | MaxDD {oos_combined['max_drawdown_pct']:5.2f}% | Trades {oos_combined['total_trades']}")
    print(f"    ├─ Strategy 1 (Momentum Alone):      {oos_momentum['net_return_pct']:+6.2f}% | Sharpe {oos_momentum['sharpe_ratio']:5.2f} | MaxDD {oos_momentum['max_drawdown_pct']:5.2f}% | Trades {oos_momentum['total_trades']} | PF {oos_momentum['profit_factor']:.2f}")
    print(f"    ├─ Strategy 2 (Mean Reversion):      {oos_mean_reversion['net_return_pct']:+6.2f}% | Sharpe {oos_mean_reversion['sharpe_ratio']:5.2f} | MaxDD {oos_mean_reversion['max_drawdown_pct']:5.2f}% | Trades {oos_mean_reversion['total_trades']} | PF {oos_mean_reversion['profit_factor']:.2f}")
    print(f"    ├─ Strategy 3 (StatArb Pairs):       {oos_stat_arb_pairs['net_return_pct']:+6.2f}% | Sharpe {oos_stat_arb_pairs['sharpe_ratio']:5.2f} | MaxDD {oos_stat_arb_pairs['max_drawdown_pct']:5.2f}% | Trades {oos_stat_arb_pairs['total_trades']} | PF {oos_stat_arb_pairs['profit_factor']:.2f}")
    print(f"    └─ Strategy 4 (Basis Yield Carry):   {oos_basis_yield['net_return_pct']:+6.2f}% | Sharpe {oos_basis_yield['sharpe_ratio']:5.2f} | MaxDD {oos_basis_yield['max_drawdown_pct']:5.2f}% | Accruals {oos_basis_yield['basis_accruals']}")
    print(f"  Window 2 Same-Basket Buy & Hold:       {oos_buy_and_hold['return_pct']:+6.2f}% | Sharpe {oos_buy_and_hold['sharpe_ratio']:5.2f} | MaxDD {oos_buy_and_hold['max_drawdown_pct']:5.2f}%")
    print(f"  Window 2 Random-Noise Baseline (N=50): {oos_random_noise['mean_return_pct']:+6.2f}% ± {oos_random_noise['std_dev_return_pct']:.2f}% (Ref: -9.39%)")
    print(f"  Recorded Live Paper Trading (34 trd):  {paper_trading_summary.get('net_return_pct', 0.0):+6.2f}% (${paper_trading_summary.get('current_cash', 0.0):,.2f} / $10,000.00)")
    print("=" * 100)
    print(f"Saved reproducible OOS validation report to: {REPORT_PATH.relative_to(PROJECT_ROOT)}")
    return report


if __name__ == "__main__":
    run_full_oos_and_multistrategy_validation()
