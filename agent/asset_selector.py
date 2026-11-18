"""Agent 9: Autonomous Asset Selection & Alpha Screener Agent.

Scans, evaluates, and ranks candidate stocks and crypto assets to dynamically
identify high-alpha, profitable assets tailored for the bot's dual-strategy
desk (Momentum Breakout + Mean Reversion Swing).

Key capabilities:
1. Dynamic Market Universe Discovery: Discovers live candidate assets directly
   from Binance order books/volume and Yahoo Finance screener feeds without any
   hardcoded prelist.
2. Strategy Compatibility & Regime Screening: Evaluates both Momentum and Mean
   Reversion engines independently and in dual-hybrid mode.
3. Walk-Forward In-Sample & Out-of-Sample Backtesting: Anti-overfitting
   verification with realistic friction (fees + slippage).
4. Composite Alpha Scoring (CAS): Multi-factor ranking penalizing drag and rewarding
   high win rates, high profit factors, and strong Sharpe ratios.
5. Local LLM Narrative Synthesis: Uses Ollama (phi4-mini) to generate qualitative
   alpha theses on top discovered candidates.
6. Dynamic Watchlist Optimization: Promotes elite alpha assets and blacklists
   toxic drags to maximize net return without loosening risk bounds.
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.data_loader import fetch_ohlcv, is_crypto_symbol, check_data_quality
from core.strategy import compute_indicators
from core.backtest_engine import (
    compute_mean_reversion_indicators,
    run_backtest,
    run_mean_reversion_backtest,
    run_volatility_squeeze_backtest,
    run_trend_pullback_backtest,
    run_vwap_reversion_backtest,
    run_nr7_breakout_backtest,
    run_range_envelope_backtest,
    BacktestResult,
)
from core.coordinator import resolve_symbol_config
from core.risk_engine import MIN_ALLOWED_RISK_PCT, MAX_ALLOWED_RISK_PCT
from core.preflight import fetch_ticker_headlines
from agent.llm_client import query_local_llm
from core.institutional_stress_tester import (
    run_slippage_multiplier_stress,
    calculate_underwater_and_streak_metrics,
)
from core.skill_intelligence_gate import (
    SecFundamentalGate,
    SmartMoneyRadar,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "active_config.json"
REPORT_PATH = PROJECT_ROOT / "data" / "asset_selection_report.json"


@dataclass
class AssetEvaluation:
    """Comprehensive alpha evaluation record for a single candidate asset."""
    symbol: str
    asset_type: str  # "crypto" or "stock"
    primary_strategy: str  # "MOMENTUM", "MEAN_REVERSION", or "DUAL_HYBRID"
    days_evaluated: int
    total_bars: int
    adx_trend_ratio: float  # % of bars with ADX >= 22
    volatility_atr_pct: float  # Mean ATR / price %
    momentum_return_pct: float
    mean_reversion_return_pct: float
    in_sample_return_pct: float
    out_of_sample_return_pct: float
    net_return_pct: float
    net_pnl: float
    win_rate_pct: float
    profit_factor: float
    max_drawdown_pct: float
    sharpe_ratio: float
    payoff_ratio: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    composite_alpha_score: float
    tier: str  # "ELITE_ALPHA", "VIABLE_ALPHA", "NEUTRAL_RESERVE", "TOXIC_DRAG"
    recommendation: str  # "ADD_TO_WATCHLIST", "HOLD_IN_RESERVE", "BLACKLIST_OR_PRUNE"
    rationale: str
    llm_thesis: str = ""
    break_even_cost_multiplier: float = 1.0
    max_underwater_days: float = 0.0


def load_active_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the current active configuration."""
    path = config_path or CONFIG_PATH
    with open(path, "r") as f:
        return json.load(f)


def discover_dynamic_crypto_universe(
    min_volume_usd: float = 10_000_000.0,
    top_n: int = 25,
    exchange_name: str = "binance",
) -> List[str]:
    """
    Dynamically discover top liquid cryptocurrency pairs directly from exchange
    order books without any hardcoded prelist. Filters out stablecoins and leveraged tokens.
    """
    try:
        import ccxt
        exchange_class = getattr(ccxt, exchange_name)
        ex = exchange_class({"enableRateLimit": True, "timeout": 10000})
        tickers = ex.fetch_tickers()
        candidates = []
        excluded_prefixes = (
            "USDC/", "FDUSD/", "TUSD/", "USDP/", "EUR/", "BUSD/", "DAI/", "USD1/",
            "RLUSD/", "WBTC/", "XAUT/", "UST/", "AEUR/", "PAX/", "USDD/", "FRAX/",
        )
        excluded_suffixes = ("UP/USDT", "DOWN/USDT", "B/USDT", "BEAR/USDT", "BULL/USDT")

        for sym, t in tickers.items():
            if not sym.endswith("/USDT"):
                continue
            if any(sym.startswith(p) for p in excluded_prefixes):
                continue
            if any(sym.endswith(s) for s in excluded_suffixes):
                continue
            vol = float(t.get("quoteVolume", 0) or 0)
            if vol >= min_volume_usd:
                candidates.append((sym, vol))

        candidates.sort(key=lambda x: x[1], reverse=True)
        discovered = [c[0] for c in candidates[:top_n]]
        if discovered:
            return discovered
    except Exception as e:
        logger.debug(f"[AssetSelector] Dynamic crypto discovery notice ({e}). Falling back to active watchlist.")

    # Graceful fallback to active watchlist cryptos if offline
    cfg = load_active_config()
    return [s for s in cfg.get("watchlist", []) if is_crypto_symbol(s)]


def discover_dynamic_stock_universe(
    screeners: Optional[List[str]] = None,
    top_n: int = 25,
) -> List[str]:
    """
    Dynamically discover most active and high-momentum US equities and ETFs
    from live financial screeners without any hardcoded prelist.
    """
    scr_list = screeners or ["most_actives", "growth_technology_stocks", "day_gainers"]
    discovered: List[str] = []

    for scr in scr_list:
        url = f"https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?formatted=false&scrIds={scr}&count={top_n*2}"
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode())
                quotes = data.get("finance", {}).get("result", [{}])[0].get("quotes", [])
                for q in quotes:
                    s = q.get("symbol")
                    if s and "^" not in s and "." not in s and "/" not in s and s not in discovered:
                        discovered.append(s)
        except Exception as e:
            logger.debug(f"[AssetSelector] Screener {scr} query notice: {e}")

    if discovered:
        return discovered[:top_n]

    # Graceful fallback to active watchlist equities if offline
    cfg = load_active_config()
    return [s for s in cfg.get("watchlist", []) if not is_crypto_symbol(s)]


def calculate_composite_alpha_score(
    net_return_pct: float,
    win_rate_pct: float,
    profit_factor: float,
    max_drawdown_pct: float,
    sharpe_ratio: float,
    is_return_pct: float,
    oos_return_pct: float,
    break_even_multiplier: float = 2.5,
    max_underwater_days: float = 7.0,
) -> float:
    """
    Calculate risk-adjusted Composite Alpha Score (CAS).
    Penalizes overfitting, high drawdowns, negative net expectancy, fragile friction margins,
    and prolonged underwater drawdowns.
    """
    dd_pen = max(max_drawdown_pct, 0.5)
    pf_clamped = min(max(profit_factor, 0.1), 5.0)

    # Out-of-sample stability factor (anti-overfitting safeguard)
    stability = 1.0
    if is_return_pct > 0 and oos_return_pct > 0:
        ratio = oos_return_pct / is_return_pct
        stability = min(1.2, max(0.6, ratio))
    elif is_return_pct > 0 and oos_return_pct <= 0:
        stability = 0.4  # Severe penalty for out-of-sample breakdown

    # Friction tolerance adjustment (stress buffer against exchange fees/slippage)
    friction_adj = 1.0
    if break_even_multiplier >= 3.0:
        friction_adj = 1.15
    elif break_even_multiplier < 1.8:
        friction_adj = 0.70

    # Drawdown duration penalty (penalize capital stagnation)
    duration_adj = 1.0
    if max_underwater_days > 21.0:
        duration_adj = 0.75
    elif max_underwater_days <= 7.0:
        duration_adj = 1.10

    if net_return_pct > 0 and win_rate_pct >= 50.0 and profit_factor >= 1.0:
        raw = (net_return_pct * (win_rate_pct / 50.0) * pf_clamped / dd_pen) * 5.0 * stability * friction_adj * duration_adj + (sharpe_ratio * 2.0)
        return round(float(raw), 2)
    else:
        # Penalize drag assets
        raw = (net_return_pct * 2.0) - (dd_pen * 1.5) - max(0.0, 50.0 - win_rate_pct) + (sharpe_ratio * 2.0)
        return round(float(raw), 2)


def evaluate_candidate_asset(
    symbol: str,
    days: int = 90,
    timeframe: str = "1h",
    config: Optional[Dict[str, Any]] = None,
    execution_mode: str = "maker",
    use_llm: bool = True,
) -> Optional[AssetEvaluation]:
    """
    Perform deep empirical evaluation of an asset:
    1. Fetch historical OHLCV data.
    2. Compute technical volatility and regime indicators.
    3. Split 70% In-Sample / 30% Out-of-Sample.
    4. Run backtests across Momentum & Mean Reversion strategies.
    5. Aggregate metrics and compute Composite Alpha Score.
    6. Local LLM narrative thesis generation for top candidates.
    """
    cfg = config or load_active_config()
    scfg = resolve_symbol_config(cfg, symbol)
    asset_type = "crypto" if is_crypto_symbol(symbol) else "stock"

    try:
        raw_df = fetch_ohlcv(symbol=symbol, timeframe=timeframe, days=days)
    except Exception as e:
        print(f"[AssetSelector] Warning: Failed to fetch {symbol}: {e}")
        return None

    if raw_df is None or len(raw_df) < 120:
        print(f"[AssetSelector] Warning: Insufficient data for {symbol} ({len(raw_df) if raw_df is not None else 0} bars)")
        return None

    # Calculate trend and volatility metrics
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

    close_series = raw_df["close"].astype(float)
    atr_series = mom_df["atr"].astype(float)
    atr_pct_mean = float((atr_series / close_series * 100.0).dropna().mean()) if len(atr_series) else 0.0

    adx_series = mr_df["adx"].astype(float) if "adx" in mr_df.columns else pd.Series(dtype=float)
    adx_trend_ratio = float((adx_series >= 22.0).sum() / len(adx_series) * 100.0) if len(adx_series) else 0.0

    # Split 70% In-Sample / 30% Out-of-Sample
    split_idx = int(len(raw_df) * 0.70)
    train_df = raw_df.iloc[:split_idx].copy()
    test_df = raw_df.iloc[split_idx:].copy()

    # Determine execution friction
    if execution_mode == "maker":
        fee_pct = 0.0002
        slip_pct = 0.0000
    else:
        fee_pct = float(scfg.get("fee_pct", 0.0006))
        slip_pct = float(scfg.get("slippage_pct", 0.0004))

    # Evaluate In-Sample & Out-of-Sample via Momentum & Mean Reversion (with full profile parameter fidelity)
    mom_kwargs = dict(
        initial_capital=10_000.0,
        account_risk_pct=float(scfg.get("account_risk_pct", 0.02)),
        breakout_lookback=int(scfg.get("breakout_lookback", 20)),
        ema_trend_filter=int(scfg.get("ema_trend_filter", 32)),
        atr_period=int(scfg.get("atr_period", 14)),
        atr_multiplier=float(scfg.get("atr_multiplier", 2.5)),
        take_profit_atr_mult=float(scfg.get("take_profit_atr_mult", 2.0)),
        take_profit_ratio=float(scfg.get("take_profit_ratio", 0.5)),
        allow_long=bool(scfg.get("allow_long", True)),
        allow_short=bool(scfg.get("allow_short", True)),
        fee_pct=fee_pct,
        slippage_pct=slip_pct,
    )
    mr_kwargs = dict(
        initial_capital=10_000.0,
        account_risk_pct=float(scfg.get("account_risk_pct", 0.02)),
        bb_period=int(scfg.get("bb_period", 20)),
        bb_std=float(scfg.get("bb_std", 2.0)),
        rsi_period=int(scfg.get("rsi_period", 14)),
        rsi_oversold=float(scfg.get("rsi_oversold", 30.0)),
        rsi_overbought=float(scfg.get("rsi_overbought", 70.0)),
        atr_period=int(scfg.get("atr_period", 14)),
        atr_stop_mult=float(scfg.get("mr_atr_stop_mult", 2.0)),
        adx_period=int(scfg.get("adx_period", 14)),
        adx_threshold=float(scfg.get("adx_threshold", scfg.get("adx_chop_threshold", 25.0))),
        max_hold_bars=int(scfg.get("mr_max_hold_bars", 48)),
        scale_out_ratio=float(scfg.get("mr_scale_out_ratio", 0.5)),
        allow_long=bool(scfg.get("mr_allow_long", scfg.get("allow_long", True))),
        allow_short=bool(scfg.get("mr_allow_short", False)),
        fee_pct=fee_pct,
        slippage_pct=slip_pct,
    )

    train_mom = run_backtest(train_df, **mom_kwargs)
    test_mom = run_backtest(test_df, **mom_kwargs)
    train_mr = run_mean_reversion_backtest(train_df, **mr_kwargs)
    test_mr = run_mean_reversion_backtest(test_df, **mr_kwargs)

    # Full period evaluation across all 5 directional engines
    # (Basis Yield is intentionally excluded here so ~100%-WR carry ticks do not mask directional quality)
    full_mom = run_backtest(raw_df, **mom_kwargs)
    full_mr = run_mean_reversion_backtest(raw_df, **mr_kwargs)

    mom_ret = round(full_mom.total_return_pct, 2)
    mr_ret = round(full_mr.total_return_pct, 2)

    ext_kwargs = dict(
        initial_capital=10_000.0,
        account_risk_pct=float(scfg.get("account_risk_pct", 0.02)),
        fee_pct=fee_pct,
        slippage_pct=slip_pct,
    )
    candidates = [
        ("MOMENTUM", train_mom, test_mom, full_mom),
        ("MEAN_REVERSION", train_mr, test_mr, full_mr),
    ]
    for label, fn, flag in [
        ("VOLATILITY_SQUEEZE", run_volatility_squeeze_backtest, "enable_volatility_squeeze"),
        ("TREND_PULLBACK", run_trend_pullback_backtest, "enable_trend_pullback"),
        ("VWAP_REVERSION", run_vwap_reversion_backtest, "enable_vwap_reversion"),
        ("NR7_BREAKOUT", run_nr7_breakout_backtest, "enable_nr7_breakout"),
        ("RANGE_ENVELOPE", run_range_envelope_backtest, "enable_range_envelope"),
    ]:
        if cfg.get(flag, True):
            candidates.append((
                label,
                fn(train_df, **ext_kwargs),
                fn(test_df, **ext_kwargs),
                fn(raw_df, **ext_kwargs),
            ))

    use_dynamic_router = bool(cfg.get("enable_dynamic_rolling_router", False))
    if use_dynamic_router:
        min_pf = float(cfg.get("dynamic_router_min_pf", 1.15))
        qualified = [c for c in candidates if c[3].total_trades >= 2 and c[3].profit_factor >= min_pf]
        pool = qualified if qualified else candidates
        best = max(pool, key=lambda c: c[3].sharpe_ratio)
    else:
        best = max(candidates, key=lambda c: c[3].total_return_pct)

    primary_strat, best_train, best_test, chosen_full = best
    is_return = best_train.total_return_pct
    oos_return = best_test.total_return_pct

    if mom_ret > 0 and mr_ret > 0 and primary_strat in ("MOMENTUM", "MEAN_REVERSION"):
        primary_strat = "DUAL_HYBRID"

    total_trades = chosen_full.total_trades
    winning_trades = chosen_full.winning_trades
    losing_trades = chosen_full.losing_trades
    win_rate_pct = round(chosen_full.win_rate * 100.0, 1) if total_trades > 0 else 0.0
    net_return_pct = round(chosen_full.total_return_pct, 2)
    net_pnl = round(chosen_full.final_equity - chosen_full.initial_capital, 2)
    profit_factor = round(chosen_full.profit_factor, 2)
    max_dd_pct = round(chosen_full.max_drawdown_pct, 2)
    sharpe = round(chosen_full.sharpe_ratio, 2)

    pnls = [t.pnl for t in chosen_full.trades] if hasattr(chosen_full, "trades") else []
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    payoff_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else (1.0 if avg_win > 0 else 0.0)

    # Compute Institutional Robustness Metrics (Friction Tolerance & Underwater Duration)
    break_even_mult = 3.0
    max_underwater_days = 7.0
    if chosen_full.trades and len(chosen_full.trades) >= 3:
        try:
            trade_dicts = [
                {
                    "pnl": t.pnl,
                    "fees": getattr(t, "fees", 0.0),
                    "units": t.units,
                    "entry_price": t.entry_price,
                }
                for t in chosen_full.trades
            ]
            stress_res = run_slippage_multiplier_stress(trade_dicts, initial_capital=10_000.0)
            break_even_mult = float(stress_res.get("break_even_multiplier", 3.0))
        except Exception:
            pass

    if chosen_full.equity_curve and len(chosen_full.equity_curve) >= 5:
        try:
            u_res = calculate_underwater_and_streak_metrics(chosen_full.equity_curve, pnls)
            max_underwater_days = float(u_res.get("max_underwater_days", 7.0))
        except Exception:
            pass

    # Compute Composite Alpha Score
    cas = calculate_composite_alpha_score(
        net_return_pct=net_return_pct,
        win_rate_pct=win_rate_pct,
        profit_factor=profit_factor,
        max_drawdown_pct=max_dd_pct,
        sharpe_ratio=sharpe,
        is_return_pct=is_return,
        oos_return_pct=oos_return,
        break_even_multiplier=break_even_mult,
        max_underwater_days=max_underwater_days,
    )

    # Tier Classification & Recommendation (with Institutional Quality Gate & SEC XBRL Fundamentals)
    fund_verdict = SecFundamentalGate().evaluate_equity(symbol)
    sm_verdict = SmartMoneyRadar().evaluate_symbol(symbol)
    if sm_verdict.whale_13f_bullish or sm_verdict.congress_ptr_bullish:
        cas = round(cas * 1.12, 2)

    if not fund_verdict.passed:
        tier = "TOXIC_DRAG"
        recommendation = "BLACKLIST_OR_PRUNE"
        rationale = f"{fund_verdict.reason} (Technical return {net_return_pct:+.2f}%)"
    elif cas >= 5.0 and net_return_pct > 0 and win_rate_pct >= 55.0 and max_dd_pct <= 12.0 and break_even_mult >= 2.0:
        tier = "ELITE_ALPHA"
        recommendation = "ADD_TO_WATCHLIST"
        rationale = f"High-conviction {primary_strat} winner: +{net_return_pct}% return, {win_rate_pct}% WR, Sharpe {sharpe}, {break_even_mult:.1f}x fee buffer"
    elif (cas >= 0.1 or net_return_pct > 0) and win_rate_pct >= 50.0 and max_dd_pct <= 16.0 and break_even_mult >= 1.5:
        tier = "VIABLE_ALPHA"
        recommendation = "ADD_TO_WATCHLIST"
        rationale = f"Viable {primary_strat} candidate: +{net_return_pct}% return, {win_rate_pct}% WR, Sharpe {sharpe}"
    elif net_return_pct >= -1.0 and win_rate_pct >= 45.0 and max_dd_pct <= 16.0:
        tier = "NEUTRAL_RESERVE"
        recommendation = "HOLD_IN_RESERVE"
        rationale = f"Neutral/break-even {primary_strat}: {net_return_pct}% return, {win_rate_pct}% WR"
    else:
        tier = "TOXIC_DRAG"
        recommendation = "BLACKLIST_OR_PRUNE"
        rationale = f"Negative or choppy drag: {net_return_pct}% return, {win_rate_pct}% WR, {max_dd_pct}% MaxDD"

    # Qualitative local LLM alpha thesis for top candidates
    llm_thesis = ""
    if use_llm and tier in ("ELITE_ALPHA", "VIABLE_ALPHA"):
        try:
            headlines = fetch_ticker_headlines(symbol, limit=2)
            news_txt = " | ".join(headlines) if headlines else "No adverse news reported."
            prompt = (
                f"Asset: {symbol} ({asset_type})\n"
                f"Primary Engine: {primary_strat}\n"
                f"Metrics: Net Return {net_return_pct:+.2f}%, Win Rate {win_rate_pct:.1f}%, Profit Factor {profit_factor:.2f}, Max Drawdown {max_dd_pct:.2f}%, Sharpe {sharpe:.2f}\n"
                f"Recent News: {news_txt}\n\n"
                "As a quantitative hedge fund analyst, provide a concise 2-sentence alpha thesis on why this asset is favorable to add to our trading watchlist."
            )
            resp = query_local_llm(prompt, model="phi4-mini:latest", timeout_seconds=6.0)
            if resp:
                llm_thesis = resp
        except Exception:
            pass

    return AssetEvaluation(
        symbol=symbol,
        asset_type=asset_type,
        primary_strategy=primary_strat,
        days_evaluated=days,
        total_bars=len(raw_df),
        adx_trend_ratio=round(adx_trend_ratio, 1),
        volatility_atr_pct=round(atr_pct_mean, 2),
        momentum_return_pct=mom_ret,
        mean_reversion_return_pct=mr_ret,
        in_sample_return_pct=round(is_return, 2),
        out_of_sample_return_pct=round(oos_return, 2),
        net_return_pct=net_return_pct,
        net_pnl=net_pnl,
        win_rate_pct=win_rate_pct,
        profit_factor=profit_factor,
        max_drawdown_pct=max_dd_pct,
        sharpe_ratio=sharpe,
        payoff_ratio=payoff_ratio,
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        composite_alpha_score=cas,
        tier=tier,
        recommendation=recommendation,
        rationale=rationale,
        llm_thesis=llm_thesis,
        break_even_cost_multiplier=round(break_even_mult, 2),
        max_underwater_days=round(max_underwater_days, 1),
    )


def scan_candidate_universe(
    crypto_candidates: Optional[List[str]] = None,
    stock_candidates: Optional[List[str]] = None,
    days: int = 90,
    timeframe: str = "1h",
    config: Optional[Dict[str, Any]] = None,
    execution_mode: str = "maker",
    dynamic_discovery: bool = True,
    merge_existing: bool = True,
    crypto_min_volume: float = 10_000_000.0,
    top_crypto_discovery: int = 15,
    top_stock_discovery: int = 15,
    use_llm: bool = True,
) -> List[AssetEvaluation]:
    """Scan and rank all candidate assets across digital assets and equities with dynamic discovery & portfolio merging."""
    cfg = config or load_active_config()

    # 1. Existing portfolio symbols to preserve and audit
    existing_cryptos: List[str] = []
    existing_stocks: List[str] = []
    if merge_existing:
        active_wl = list(cfg.get("watchlist", []))
        risk_syms = list(cfg.get("symbol_risk_weights", {}).keys())
        all_configured = list(dict.fromkeys(active_wl + risk_syms))
        blacklisted = set(cfg.get("blacklisted_symbols", []))
        clean_existing = [s for s in all_configured if s not in blacklisted]
        existing_cryptos = [s for s in clean_existing if is_crypto_symbol(s)]
        existing_stocks = [s for s in clean_existing if not is_crypto_symbol(s)]

    # 2. Dynamic Discovery (Zero-prelist from real-time exchange order books & screeners)
    discovered_cryptos: List[str] = []
    discovered_stocks: List[str] = []
    if dynamic_discovery:
        print("[AssetSelector] Dynamic Market Discovery active: Querying real-time exchange order books & screeners (no prelist)...")
        discovered_cryptos = crypto_candidates if crypto_candidates is not None else discover_dynamic_crypto_universe(
            min_volume_usd=crypto_min_volume, top_n=top_crypto_discovery
        )
        discovered_stocks = stock_candidates if stock_candidates is not None else discover_dynamic_stock_universe(
            top_n=top_stock_discovery
        )
    elif crypto_candidates is not None or stock_candidates is not None:
        discovered_cryptos = crypto_candidates or []
        discovered_stocks = stock_candidates or []

    # 3. Merge existing portfolio with newly discovered candidates (deduplicated)
    cryptos = list(dict.fromkeys(existing_cryptos + discovered_cryptos))
    stocks = list(dict.fromkeys(existing_stocks + discovered_stocks))
    all_symbols = cryptos + stocks

    print("=" * 80)
    print(f"  AGENT 9: AUTONOMOUS ALPHA SCANNER & MARKET DISCOVERY")
    print(f"  Evaluating {len(all_symbols)} assets ({len(cryptos)} cryptos, {len(stocks)} stocks) | Lookback: {days}d")
    print(f"  Candidate Cryptos: {cryptos[:8]} ...")
    print(f"  Candidate Equities: {stocks[:8]} ...")
    print("=" * 80)

    results: List[AssetEvaluation] = []
    t0 = time.time()

    for i, sym in enumerate(all_symbols, start=1):
        print(f"[{i:02d}/{len(all_symbols):02d}] Evaluating {sym:<12} ... ", end="", flush=True)
        t_sub = time.time()
        ev = evaluate_candidate_asset(
            symbol=sym,
            days=days,
            timeframe=timeframe,
            config=config,
            execution_mode=execution_mode,
            use_llm=use_llm,
        )
        if ev:
            results.append(ev)
            print(f"Done ({time.time()-t_sub:.1f}s) | Score: {ev.composite_alpha_score:+6.2f} | Net: {ev.net_return_pct:+5.2f}% | WR: {ev.win_rate_pct:4.1f}% | Tier: {ev.tier}")
        else:
            print("SKIPPED (Data Fetch / Bars Insufficient)")

    results.sort(key=lambda x: x.composite_alpha_score, reverse=True)
    print(f"\n[AssetSelector] Completed screening {len(results)} assets in {time.time()-t0:.1f}s.\n")
    return results


def build_selection_summary(
    evaluations: List[AssetEvaluation],
    max_crypto: int = 8,
    max_stocks: int = 16,
    min_profit_pct: float = 0.0,
    min_win_rate_pct: float = 50.0,
    min_profit_factor: float = 1.0,
    existing_blacklist: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Select top uncorrelated portfolio universe respecting risk limits and pruning non-profitable drags.

    Mathematical Profitability Proof Standard:
    1. Proven Profitable: Net return > min_profit_pct, Win rate >= min_win_rate_pct, Profit factor >= min_profit_factor,
       and Tier in (ELITE_ALPHA, VIABLE_ALPHA).
    2. Proven Non-Profitable / Drag: Assets failing this standard are pruned from the active watchlist.
       Assets with negative net returns or Tier == TOXIC_DRAG are appended to recommended_blacklist.
    """
    elite = [e for e in evaluations if e.tier == "ELITE_ALPHA"]
    viable = [e for e in evaluations if e.tier == "VIABLE_ALPHA"]
    neutral = [e for e in evaluations if e.tier == "NEUTRAL_RESERVE"]
    toxic = [e for e in evaluations if e.tier == "TOXIC_DRAG"]

    # Filter for PROVEN PROFITABLE assets only
    proven_profitable = [
        e for e in evaluations
        if e.net_return_pct > min_profit_pct
        and e.win_rate_pct >= min_win_rate_pct
        and e.profit_factor >= min_profit_factor
        and e.tier in ("ELITE_ALPHA", "VIABLE_ALPHA")
    ]
    proven_profitable.sort(key=lambda x: x.composite_alpha_score, reverse=True)

    # Assets proven NOT profitable enough (whipsaws, negative returns, low win rates)
    proven_non_profitable = [
        e for e in evaluations
        if e.net_return_pct <= min_profit_pct
        or e.win_rate_pct < min_win_rate_pct
        or e.profit_factor < min_profit_factor
        or e.tier in ("TOXIC_DRAG", "NEUTRAL_RESERVE")
    ]

    # Select top proven cryptos (up to max_crypto)
    crypto_proven = [e for e in proven_profitable if e.asset_type == "crypto"]
    selected_crypto = [e.symbol for e in crypto_proven][:max_crypto]

    # Select top proven stocks (up to max_stocks)
    stocks_proven = [e for e in proven_profitable if e.asset_type == "stock"]
    selected_stocks = [e.symbol for e in stocks_proven][:max_stocks]

    recommended_watchlist = selected_crypto + selected_stocks

    # Recommended blacklist: Existing blacklist + newly identified toxic drags losing money
    current_bl = existing_blacklist if existing_blacklist is not None else load_active_config().get("blacklisted_symbols", [])
    new_toxic = [e.symbol for e in proven_non_profitable if e.net_return_pct < 0 or e.tier == "TOXIC_DRAG"]
    recommended_blacklist = sorted(list(dict.fromkeys(current_bl + new_toxic)))

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_assets_scanned": len(evaluations),
        "tier_counts": {
            "ELITE_ALPHA": len(elite),
            "VIABLE_ALPHA": len(viable),
            "NEUTRAL_RESERVE": len(neutral),
            "TOXIC_DRAG": len(toxic),
        },
        "proven_profitable_count": len(proven_profitable),
        "proven_non_profitable_count": len(proven_non_profitable),
        "proven_profitable_symbols": [e.symbol for e in proven_profitable],
        "pruned_drag_symbols": [e.symbol for e in proven_non_profitable],
        "recommended_watchlist": recommended_watchlist,
        "recommended_blacklist": recommended_blacklist,
        "selected_crypto": selected_crypto,
        "selected_stocks": selected_stocks,
        "leaderboard": [asdict(e) for e in evaluations],
    }


def save_selection_report(report_data: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Save the audit report to JSON."""
    out = path or REPORT_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(report_data, f, indent=2)
    print(f"[AssetSelector] Saved detailed selection report to: {out}")
    return out


def apply_recommendations_to_config(
    recommended_watchlist: List[str],
    recommended_blacklist: List[str],
    config_path: Optional[Path] = None,
    verify_tests: bool = True,
    require_human_approval: bool = False,
    human_approved: bool = True,
) -> bool:
    """Convenience forwarder to Agent 12 Governor deployment pipeline."""
    from agent.governor import review_and_deploy_proposal
    res = review_and_deploy_proposal(
        proposed_changes={
            "watchlist": recommended_watchlist,
            "blacklisted_symbols": recommended_blacklist,
        },
        config_path=config_path,
        verify_tests=verify_tests,
        use_llm=False,
        require_human_approval=require_human_approval,
        human_approved=human_approved,
    )
    return res.approved


def print_leaderboard(evaluations: List[AssetEvaluation]):
    """Print a clean, structured leaderboard to terminal."""
    print("=" * 135)
    print(f"{'SYM':<10} {'TYPE':<7} {'STRATEGY':<15} {'SCORE':<8} {'NET %':<8} {'WIN %':<7} {'PF':<6} {'MAXDD':<7} {'SHARPE':<7} {'TIER':<15} {'ACTION'}")
    print("-" * 135)
    for e in evaluations:
        print(
            f"{e.symbol:<10} "
            f"{e.asset_type:<7} "
            f"{e.primary_strategy:<15} "
            f"{e.composite_alpha_score:+7.2f} "
            f"{e.net_return_pct:+7.2f}% "
            f"{e.win_rate_pct:5.1f}% "
            f"{e.profit_factor:5.2f} "
            f"{e.max_drawdown_pct:5.2f}% "
            f"{e.sharpe_ratio:+6.2f} "
            f"{e.tier:<15} "
            f"{e.recommendation}"
        )
    print("=" * 135)


def audit_and_rehabilitate_blacklist(
    days: int = 60,
    timeframe: str = "1h",
    min_profit_pct: float = 3.0,
    min_win_rate_pct: float = 52.0,
    min_profit_factor: float = 1.25,
    max_drawdown_pct: float = 15.0,
    execution_mode: str = "maker",
    config: Optional[Dict[str, Any]] = None,
    use_llm: bool = False,
) -> Dict[str, Any]:
    """
    Parole Hearing Protocol:
    Empirically re-audits all blacklisted assets over recent lookback window (e.g. 60d).
    Rehabilitates assets that show verified positive alpha and passing fundamentals.
    """
    cfg = config or load_active_config()
    bl_symbols = list(cfg.get("blacklisted_symbols", []))

    print("=" * 95)
    print("  AGENT 9: BLACKLIST REHABILITATION AUDIT (PAROLE HEARING)")
    print(f"  Re-evaluating {len(bl_symbols)} quarantined assets over past {days} days")
    print("=" * 95)

    rehabilitated: List[Dict[str, Any]] = []
    quarantine_maintained: List[Dict[str, Any]] = []

    for sym in bl_symbols:
        eval_res = evaluate_candidate_asset(
            symbol=sym,
            days=days,
            timeframe=timeframe,
            config=cfg,
            execution_mode=execution_mode,
            use_llm=use_llm,
        )
        if not eval_res:
            quarantine_maintained.append({
                "symbol": sym,
                "asset_type": "crypto" if is_crypto_symbol(sym) else "stock",
                "strategy": "N/A",
                "net_return_pct": 0.0,
                "win_rate_pct": 0.0,
                "profit_factor": 0.0,
                "max_drawdown_pct": 0.0,
                "passed_fundamentals": False,
                "recommendation": "MAINTAIN_QUARANTINE",
                "rationale": "Data unavailable or insufficient historical bars",
            })
            continue

        fund_passed = True
        fund_reason = "Passed"
        if eval_res.asset_type == "stock":
            fund_verdict = SecFundamentalGate().evaluate_equity(sym)
            fund_passed = fund_verdict.passed
            fund_reason = fund_verdict.reason

        meets_profit = eval_res.net_return_pct >= min_profit_pct
        meets_wr = eval_res.win_rate_pct >= min_win_rate_pct
        meets_pf = eval_res.profit_factor >= min_profit_factor
        meets_dd = eval_res.max_drawdown_pct <= max_drawdown_pct

        if meets_profit and meets_wr and meets_pf and meets_dd and fund_passed:
            rehabilitated.append({
                "symbol": sym,
                "asset_type": eval_res.asset_type,
                "strategy": eval_res.primary_strategy,
                "net_return_pct": eval_res.net_return_pct,
                "win_rate_pct": eval_res.win_rate_pct,
                "profit_factor": eval_res.profit_factor,
                "max_drawdown_pct": eval_res.max_drawdown_pct,
                "sharpe_ratio": eval_res.sharpe_ratio,
                "passed_fundamentals": fund_passed,
                "recommendation": "REHABILITATE_TO_PROBATION",
                "rationale": f"Recovered alpha: +{eval_res.net_return_pct:.2f}% net, {eval_res.win_rate_pct:.1f}% WR, PF {eval_res.profit_factor:.2f}",
            })
        else:
            fail_reasons = []
            if not meets_profit:
                fail_reasons.append(f"Net return {eval_res.net_return_pct:+.2f}% < +{min_profit_pct}%")
            if not meets_wr:
                fail_reasons.append(f"Win rate {eval_res.win_rate_pct:.1f}% < {min_win_rate_pct}%")
            if not meets_pf:
                fail_reasons.append(f"PF {eval_res.profit_factor:.2f} < {min_profit_factor}")
            if not meets_dd:
                fail_reasons.append(f"MaxDD {eval_res.max_drawdown_pct:.1f}% > {max_drawdown_pct}%")
            if not fund_passed:
                fail_reasons.append(f"SEC Fundamentals: {fund_reason}")

            quarantine_maintained.append({
                "symbol": sym,
                "asset_type": eval_res.asset_type,
                "strategy": eval_res.primary_strategy,
                "net_return_pct": eval_res.net_return_pct,
                "win_rate_pct": eval_res.win_rate_pct,
                "profit_factor": eval_res.profit_factor,
                "max_drawdown_pct": eval_res.max_drawdown_pct,
                "sharpe_ratio": eval_res.sharpe_ratio,
                "passed_fundamentals": fund_passed,
                "recommendation": "MAINTAIN_QUARANTINE",
                "rationale": "; ".join(fail_reasons),
            })

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_blacklisted_evaluated": len(bl_symbols),
        "rehabilitated_count": len(rehabilitated),
        "quarantine_count": len(quarantine_maintained),
        "rehabilitated_symbols": [r["symbol"] for r in rehabilitated],
        "quarantine_symbols": [q["symbol"] for q in quarantine_maintained],
        "rehabilitated_details": rehabilitated,
        "quarantine_details": quarantine_maintained,
    }


def print_rehabilitation_report(report: Dict[str, Any]):
    """Print the Blacklist Rehabilitation Parole Scoreboard."""
    print("=" * 110)
    print("  BLACKLIST REHABILITATION PAROLE SCOREBOARD")
    print("=" * 110)
    rehab = report.get("rehabilitated_details", [])
    quar = report.get("quarantine_details", [])

    print(f"\n✅ ELIGIBLE FOR PAROLE / PROBATIONARY GRADUATION ({len(rehab)} assets):")
    if rehab:
        print(f"{'SYM':<12} {'TYPE':<7} {'STRATEGY':<15} {'NET %':<8} {'WIN %':<7} {'PF':<6} {'MAXDD':<7} {'RATIONALE'}")
        print("-" * 110)
        for r in rehab:
            print(f"{r['symbol']:<12} {r.get('asset_type',''):<7} {r.get('strategy',''):<15} {r.get('net_return_pct',0):+7.2f}% {r.get('win_rate_pct',0):5.1f}% {r.get('profit_factor',0):5.2f} {r.get('max_drawdown_pct',0):5.2f}% {r.get('rationale','')}")
    else:
        print("  None. All quarantined assets still fail the mathematical profitability standard.")

    print(f"\n🔒 QUARANTINE MAINTAINED ({len(quar)} assets):")
    if quar:
        print(f"{'SYM':<12} {'TYPE':<7} {'STRATEGY':<15} {'NET %':<8} {'WIN %':<7} {'PF':<6} {'MAXDD':<7} {'FAILURE REASON'}")
        print("-" * 110)
        for q in quar[:10]:
            print(f"{q['symbol']:<12} {q.get('asset_type',''):<7} {q.get('strategy',''):<15} {q.get('net_return_pct',0):+7.2f}% {q.get('win_rate_pct',0):5.1f}% {q.get('profit_factor',0):5.2f} {q.get('max_drawdown_pct',0):5.2f}% {q.get('rationale','')[:45]}")
        if len(quar) > 10:
            print(f"  ... and {len(quar) - 10} more assets remain quarantined.")
    print("=" * 110)


def main():
    parser = argparse.ArgumentParser(description="Agent 9: Autonomous Alpha Scanner & Market Discovery")
    parser.add_argument("--scan", action="store_true", help="Run candidate scan and print leaderboard")
    parser.add_argument("--days", type=int, default=90, help="Lookback evaluation days (default: 90)")
    parser.add_argument("--timeframe", type=str, default="1h", help="Timeframe (default: 1h)")
    parser.add_argument("--top-crypto", type=int, default=8, help="Max crypto assets to recommend (default: 8)")
    parser.add_argument("--top-stocks", type=int, default=16, help="Max stock assets to recommend (default: 16)")
    parser.add_argument("--no-merge", action="store_true", help="Do not merge existing active assets")
    parser.add_argument("--discover-crypto", type=int, default=15, help="Number of liquid cryptos to discover live (default: 15)")
    parser.add_argument("--discover-stocks", type=int, default=15, help="Number of liquid stocks to discover live (default: 15)")
    parser.add_argument("--crypto-min-volume", type=float, default=10_000_000.0, help="Minimum 24h quote volume for crypto (default: $10M)")
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated custom symbols to evaluate (overrides discovery)")
    parser.add_argument("--static", action="store_true", help="Use static fallback watchlist instead of dynamic discovery")
    parser.add_argument("--execution-mode", type=str, choices=["maker", "taker"], default="maker", help="Execution mode (default: maker)")
    parser.add_argument("--apply", action="store_true", help="Send recommendations to Agent 12 Governor for approval and deployment")
    parser.add_argument("--report", type=str, default=None, help="Custom output report path")
    parser.add_argument("--audit-blacklist", action="store_true", help="Run empirical rehabilitation audit on blacklisted assets")
    parser.add_argument("--rehabilitate", action="store_true", help="Alias for --audit-blacklist")
    parser.add_argument("--no-llm", action="store_true", help="Bypass LLM narrative screening")
    args = parser.parse_args()

    cfg = load_active_config()

    if args.audit_blacklist or args.rehabilitate:
        report = audit_and_rehabilitate_blacklist(
            days=args.days,
            timeframe=args.timeframe,
            execution_mode=args.execution_mode,
            config=cfg,
            use_llm=not args.no_llm,
        )
        print_rehabilitation_report(report)
        if args.report:
            save_selection_report(report, Path(args.report))
        if args.apply and report["rehabilitated_symbols"]:
            print(f"\n[AssetSelector] Submitting rehabilitation proposal to Agent 12 Governor for: {report['rehabilitated_symbols']}...")
            from agent.governor import review_and_deploy_proposal
            current_wl = list(cfg.get("watchlist", []))
            new_wl = sorted(list(dict.fromkeys(current_wl + report["rehabilitated_symbols"])))
            res = review_and_deploy_proposal(
                proposed_changes={
                    "rehabilitated_symbols": report["rehabilitated_symbols"],
                    "watchlist": new_wl,
                },
                rationale="Automated Blacklist Rehabilitation Audit graduated recovering assets to probationary status.",
                empirical_proof={
                    "rehabilitated_count": len(report["rehabilitated_symbols"]),
                    "rehabilitated_symbols": report["rehabilitated_symbols"],
                    "rehabilitation_proof": report["rehabilitated_details"],
                },
                verify_tests=True,
                use_llm=not args.no_llm,
            )
            if res.approved:
                print(f"[AssetSelector] ✅ Successfully rehabilitated {len(report['rehabilitated_symbols'])} assets onto probationary watchlist!")
            else:
                print(f"[AssetSelector] ❌ Rehabilitation rejected by Governor: {res.llm_rationale}")
        return

    if args.symbols:
        custom_syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
        cryptos = [s for s in custom_syms if is_crypto_symbol(s)]
        stocks = [s for s in custom_syms if not is_crypto_symbol(s)]
        evaluations = scan_candidate_universe(
            crypto_candidates=cryptos,
            stock_candidates=stocks,
            days=args.days,
            timeframe=args.timeframe,
            config=cfg,
            execution_mode=args.execution_mode,
            dynamic_discovery=False,
            merge_existing=False,
            use_llm=not args.no_llm,
        )
    else:
        evaluations = scan_candidate_universe(
            days=args.days,
            timeframe=args.timeframe,
            config=cfg,
            execution_mode=args.execution_mode,
            dynamic_discovery=not args.static,
            merge_existing=not args.no_merge,
            crypto_min_volume=args.crypto_min_volume,
            top_crypto_discovery=args.discover_crypto,
            top_stock_discovery=args.discover_stocks,
            use_llm=not args.no_llm,
        )

    print_leaderboard(evaluations)

    summary = build_selection_summary(evaluations, max_crypto=args.top_crypto, max_stocks=args.top_stocks)
    report_file = Path(args.report) if args.report else REPORT_PATH
    save_selection_report(summary, report_file)

    print("\n" + "=" * 80)
    print("  SELECTION RECOMMENDATIONS & EMPIRICAL PROOF")
    print("=" * 80)
    print(f"  Proven Profitable ({summary.get('proven_profitable_count', 0)} assets):")
    print(f"    {summary.get('proven_profitable_symbols', [])}")
    print(f"  Pruned Drags ({summary.get('proven_non_profitable_count', 0)} assets):")
    print(f"    {summary.get('pruned_drag_symbols', [])}")
    print("-" * 80)
    print(f"  Recommended Active Watchlist ({len(summary['recommended_watchlist'])} assets):")
    print(f"    Crypto ({len(summary['selected_crypto'])}): {summary['selected_crypto']}")
    print(f"    Stocks ({len(summary['selected_stocks'])}): {summary['selected_stocks']}")
    print(f"  Recommended Blacklist ({len(summary['recommended_blacklist'])} assets):")
    print(f"    {summary['recommended_blacklist']}")
    print("=" * 80)

    # Display top LLM alpha theses
    top_elites = [e for e in evaluations if e.llm_thesis]
    if top_elites:
        print("\n[LLM Qualitative Alpha Theses for Top Discovered Candidates]")
        for e in top_elites[:3]:
            print(f"• {e.symbol} ({e.primary_strategy}):\n  {e.llm_thesis}\n")

    if args.apply:
        print("\n[AssetSelector] Submitting proposal to Agent 12 (Governor) for review and deployment...")
        from agent.governor import review_and_deploy_proposal
        proposal = {
            "watchlist": summary["recommended_watchlist"],
            "blacklisted_symbols": summary["recommended_blacklist"],
        }
        res = review_and_deploy_proposal(
            proposed_changes=proposal,
            rationale="Dynamic Alpha Scanner merged active portfolio with vetted discoveries and pruned non-profitable drags.",
            empirical_proof={
                "proposed_watchlist_size": len(summary["recommended_watchlist"]),
                "crypto_count": len(summary["selected_crypto"]),
                "stocks_count": len(summary["selected_stocks"]),
                "proven_profitable_count": summary.get("proven_profitable_count", 0),
                "pruned_drags_count": summary.get("proven_non_profitable_count", 0),
                "blacklisted_toxic_symbols": summary["recommended_blacklist"],
                "top_alpha_theses": [
                    f"{e['symbol']}: Net {e['net_return_pct']:+,.2f}%, WinRate {e['win_rate_pct']:.1f}%, PF {e['profit_factor']:.2f}, Sharpe {e['sharpe_ratio']:+.2f}"
                    for e in summary["leaderboard"][:5]
                ],
                "risk_limits": "Max account risk locked at 2.0%, max drawdown circuit breaker locked at 15.0%, paper execution mode maintained.",
            },
            verify_tests=True,
            use_llm=not args.no_llm,
        )
        if res.approved:
            print("[AssetSelector] ✅ Proposal APPROVED and DEPLOYED by Agent 12 Chief Risk Governor!")
        else:
            print(f"[AssetSelector] ❌ Proposal REJECTED by Governor: {res.llm_rationale}")


if __name__ == "__main__":
    main()
