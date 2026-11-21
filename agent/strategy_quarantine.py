"""Automatic Strategy-Level OOS Quarantine & Auto-Rehabilitation Gate (Roadmap Item 8).

Evaluates each directional strategy strictly out-of-sample on a trailing walk-forward
window with full cost-adjusted friction (0.06% fee + 0.04% slippage):
- AUTO-QUARANTINE ("Bench Cold Player"):
  When an active strategy has >= min_trades OOS AND (net_pnl < 0 OR profit_factor < 1.15),
  marks it for quarantine (`enable_<strategy>: false`), while never quarantining `basis_yield`
  or leaving fewer than `min_active_strategies` (default 2) active directional strategies.
- AUTO-RETURN / REHABILITATION ("Put Hot Player Back In"):
  Tracks consecutive passing OOS checks in `data/strategy_quarantine_state.json`.
  When a quarantined strategy achieves `net_pnl > 0`, `profit_factor >= 1.30`, and
  `max_drawdown_pct <= 12.0` across `rehab_consecutive_passes` (default 2) consecutive
  evaluations, automatically proposes `enable_<strategy>: true`.

Can be run standalone (`python -m agent.strategy_quarantine`) or called weekly by Agent 11.
"""

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.backtest_engine import (
    run_backtest,
    run_donchian_range_fade_backtest,
    run_mean_reversion_backtest,
    run_nr7_breakout_backtest,
    run_range_envelope_backtest,
    run_supertrend_cta_backtest,
    run_trend_pullback_backtest,
    run_volatility_squeeze_backtest,
    run_vwap_reversion_backtest,
)
from core.coordinator import resolve_symbol_config
from core.data_loader import fetch_ohlcv, is_crypto_symbol

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "active_config.json"
STATE_PATH = PROJECT_ROOT / "data" / "strategy_quarantine_state.json"

MANAGED_STRATEGIES: Dict[str, str] = {
    "momentum": "enable_momentum",
    "mean_reversion": "enable_mean_reversion",
    "volatility_squeeze": "enable_volatility_squeeze",
    "trend_pullback": "enable_trend_pullback",
    "vwap_reversion": "enable_vwap_reversion",
    "nr7_breakout": "enable_nr7_breakout",
    "range_envelope": "enable_range_envelope",
    "supertrend_cta": "enable_supertrend_cta",
    "donchian_range_fade": "enable_donchian_range_fade",
}


@dataclass
class StrategyOosScore:
    strategy_id: str
    currently_enabled: bool
    trades: int
    net_pnl: float
    profit_factor: float
    max_drawdown_pct: float
    action: str  # "KEEP_ON", "KEEP_OFF", "QUARANTINE", "REHABILITATE", "REHAB_PROGRESS"
    reason: str
    consecutive_rehab_passes: int = 0


def load_quarantine_state(state_path: Path = STATE_PATH) -> Dict[str, Any]:
    if state_path.exists():
        try:
            return json.loads(state_path.read_text())
        except Exception:
            pass
    return {"rehab_streaks": {}, "last_run": None, "history": []}


def save_quarantine_state(state: Dict[str, Any], state_path: Path = STATE_PATH) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(state_path)


def evaluate_strategy_quarantine_decisions(
    scores: Dict[str, Tuple[int, float, float, float]],
    config: Dict[str, Any],
    state: Dict[str, Any],
    min_trades: int = 5,
    quarantine_min_pf: float = 1.15,
    rehab_min_pf: float = 1.30,
    rehab_max_dd_pct: float = 12.0,
    rehab_consecutive_passes: int = 2,
    min_active_strategies: int = 2,
) -> Tuple[List[StrategyOosScore], Dict[str, bool]]:
    """Pure decision engine: decide QUARANTINE / REHABILITATE / KEEP for each managed strategy.

    `scores` maps strategy_id -> (trades, net_pnl, profit_factor, max_drawdown_pct).
    Returns (score_reports, proposed_config_flag_updates).
    """
    streaks = dict(state.get("rehab_streaks", {}))
    reports: List[StrategyOosScore] = []
    flag_updates: Dict[str, bool] = {}

    currently_on = [
        sid for sid, flag in MANAGED_STRATEGIES.items()
        if bool(config.get(flag, False))
    ]
    active_count = len(currently_on)

    # Evaluate worst-performing active strategies first so min_active_strategies protects the best ones
    ordered_sids = sorted(
        [s for s in MANAGED_STRATEGIES if s in scores],
        key=lambda s: scores[s][1],
    )

    for sid in ordered_sids:
        flag = MANAGED_STRATEGIES[sid]
        is_on = bool(config.get(flag, False))
        trades, net_pnl, pf, max_dd = scores[sid]

        if is_on:
            streaks[sid] = 0
            if trades >= min_trades and (net_pnl < 0.0 or pf < quarantine_min_pf):
                if active_count > min_active_strategies:
                    active_count -= 1
                    flag_updates[flag] = False
                    reports.append(StrategyOosScore(
                        strategy_id=sid,
                        currently_enabled=True,
                        trades=trades,
                        net_pnl=round(net_pnl, 2),
                        profit_factor=round(pf, 2),
                        max_drawdown_pct=round(max_dd, 2),
                        action="QUARANTINE",
                        reason=f"OOS underperformance (PnL ${net_pnl:+.2f}, PF {pf:.2f} < {quarantine_min_pf:.2f})",
                        consecutive_rehab_passes=0,
                    ))
                else:
                    reports.append(StrategyOosScore(
                        strategy_id=sid,
                        currently_enabled=True,
                        trades=trades,
                        net_pnl=round(net_pnl, 2),
                        profit_factor=round(pf, 2),
                        max_drawdown_pct=round(max_dd, 2),
                        action="KEEP_ON",
                        reason=f"Protected by min_active_strategies floor ({min_active_strategies})",
                        consecutive_rehab_passes=0,
                    ))
            else:
                reports.append(StrategyOosScore(
                    strategy_id=sid,
                    currently_enabled=True,
                    trades=trades,
                    net_pnl=round(net_pnl, 2),
                    profit_factor=round(pf, 2),
                    max_drawdown_pct=round(max_dd, 2),
                    action="KEEP_ON",
                    reason="Passing OOS health check",
                    consecutive_rehab_passes=0,
                ))
        else:
            # Quarantined strategy: check Auto-Return (Rehabilitation) criteria
            passes_rehab = (
                trades >= min_trades
                and net_pnl > 0.0
                and pf >= rehab_min_pf
                and max_dd <= rehab_max_dd_pct
            )
            if passes_rehab:
                new_streak = int(streaks.get(sid, 0)) + 1
                streaks[sid] = new_streak
                if new_streak >= rehab_consecutive_passes:
                    flag_updates[flag] = True
                    streaks[sid] = 0
                    reports.append(StrategyOosScore(
                        strategy_id=sid,
                        currently_enabled=False,
                        trades=trades,
                        net_pnl=round(net_pnl, 2),
                        profit_factor=round(pf, 2),
                        max_drawdown_pct=round(max_dd, 2),
                        action="REHABILITATE",
                        reason=(
                            f"Auto-return triggered ({new_streak}/{rehab_consecutive_passes} passing OOS checks: "
                            f"PnL ${net_pnl:+.2f}, PF {pf:.2f}, MaxDD {max_dd:.1f}%)"
                        ),
                        consecutive_rehab_passes=new_streak,
                    ))
                else:
                    reports.append(StrategyOosScore(
                        strategy_id=sid,
                        currently_enabled=False,
                        trades=trades,
                        net_pnl=round(net_pnl, 2),
                        profit_factor=round(pf, 2),
                        max_drawdown_pct=round(max_dd, 2),
                        action="REHAB_PROGRESS",
                        reason=f"Rehabilitation progress {new_streak}/{rehab_consecutive_passes} (PF {pf:.2f})",
                        consecutive_rehab_passes=new_streak,
                    ))
            else:
                streaks[sid] = 0
                reports.append(StrategyOosScore(
                    strategy_id=sid,
                    currently_enabled=False,
                    trades=trades,
                    net_pnl=round(net_pnl, 2),
                    profit_factor=round(pf, 2),
                    max_drawdown_pct=round(max_dd, 2),
                    action="KEEP_OFF",
                    reason=f"Remains quarantined (PnL ${net_pnl:+.2f}, PF {pf:.2f}, MaxDD {max_dd:.1f}%)",
                    consecutive_rehab_passes=0,
                ))

    state["rehab_streaks"] = streaks
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    return reports, flag_updates


def run_weekly_strategy_quarantine(
    config_path: Path = CONFIG_PATH,
    state_path: Path = STATE_PATH,
    oos_days: int = 90,
    apply_via_governor: bool = False,
) -> Tuple[List[StrategyOosScore], Dict[str, bool]]:
    """Run trailing OOS tournament across active watchlist and optionally submit changes to Agent 12."""
    cfg = json.loads(config_path.read_text())
    fee = float(cfg.get("fee_pct", 0.0006))
    slip = float(cfg.get("slippage_pct", 0.0004))
    watchlist = list(cfg.get("watchlist", []))[:10]

    dfs = {}
    for sym in watchlist:
        try:
            df = fetch_ohlcv(sym, timeframe="1h", days=oos_days, force_refresh=False, closed_only=True)
            if df is not None and len(df) >= 200:
                dfs[sym] = df
        except Exception:
            continue

    scores: Dict[str, Tuple[int, float, float, float]] = {}
    for sid in MANAGED_STRATEGIES:
        tot_tr, tot_pnl, gp, gl, worst_dd = 0, 0.0, 0.0, 0.0, 0.0
        for sym, df in dfs.items():
            scfg = resolve_symbol_config(cfg, sym)
            is_c = is_crypto_symbol(sym)
            rp = max(0.01, min(0.05, float(cfg.get("symbol_risk_weights", {}).get(sym, 0.025))))
            if sid == "momentum":
                r = run_backtest(df, account_risk_pct=rp, breakout_lookback=int(scfg.get("breakout_lookback", 48)), ema_trend_filter=int(scfg.get("ema_trend_filter", 50)), atr_multiplier=float(scfg.get("atr_multiplier", 2.5)), take_profit_atr_mult=float(scfg.get("take_profit_atr_mult", 2.5)), fee_pct=fee, slippage_pct=slip)
            elif sid == "mean_reversion":
                r = run_mean_reversion_backtest(df, account_risk_pct=rp, bb_period=int(scfg.get("bb_period", 20)), bb_std=float(scfg.get("bb_std", 2.1)), rsi_oversold=float(scfg.get("rsi_oversold", 28.0)), rsi_overbought=float(scfg.get("rsi_overbought", 72.0)), adx_threshold=float(scfg.get("adx_threshold", 24.0)), allow_short=bool(scfg.get("mr_allow_short", True)), fee_pct=fee, slippage_pct=slip)
            elif sid == "volatility_squeeze":
                r = run_volatility_squeeze_backtest(df, account_risk_pct=rp, fee_pct=fee, slippage_pct=slip)
            elif sid == "trend_pullback":
                r = run_trend_pullback_backtest(df, account_risk_pct=rp, fee_pct=fee, slippage_pct=slip)
            elif sid == "vwap_reversion":
                r = run_vwap_reversion_backtest(df, account_risk_pct=rp, fee_pct=fee, slippage_pct=slip)
            elif sid == "nr7_breakout":
                r = run_nr7_breakout_backtest(df, account_risk_pct=rp, nr7_lookback=int(cfg.get("nr7_lookback", 9)), stop_atr_mult=float(cfg.get("nr7_atr_stop_mult", 1.5)), fee_pct=fee, slippage_pct=slip)
            elif sid == "range_envelope":
                r = run_range_envelope_backtest(df, account_risk_pct=rp, fee_pct=fee, slippage_pct=slip)
            elif sid == "supertrend_cta":
                r = run_supertrend_cta_backtest(df, account_risk_pct=rp, atr_period=int(cfg.get("supertrend_atr_period", 10)), mult=float(cfg.get("supertrend_crypto_mult", 3.0)) if is_c else float(cfg.get("supertrend_equity_mult", 2.5)), stop_atr_mult=float(cfg.get("supertrend_stop_atr_mult", 3.0)), fee_pct=fee, slippage_pct=slip)
            else:
                r = run_donchian_range_fade_backtest(df, account_risk_pct=rp, allow_long=bool(cfg.get("donchian_fade_allow_long", False)), allow_short=bool(cfg.get("donchian_fade_allow_short", True)), fee_pct=fee, slippage_pct=slip)

            tot_tr += r.total_trades
            tot_pnl += (r.final_equity - r.initial_capital)
            gp += sum(t.pnl for t in r.trades if t.pnl > 0)
            gl += abs(sum(t.pnl for t in r.trades if t.pnl < 0))
            worst_dd = max(worst_dd, r.max_drawdown_pct)

        pf = (gp / gl) if gl > 0 else (99.0 if gp > 0 else 0.0)
        scores[sid] = (tot_tr, tot_pnl, pf, worst_dd)

    state = load_quarantine_state(state_path)
    reports, flag_updates = evaluate_strategy_quarantine_decisions(scores, cfg, state)
    state.setdefault("history", []).append({
        "timestamp": state["last_run"],
        "updates": flag_updates,
        "summary": [asdict(r) for r in reports],
    })
    state["history"] = state["history"][-20:]
    save_quarantine_state(state, state_path)

    if apply_via_governor and flag_updates:
        from agent.governor import audit_invariants

        candidate_cfg = dict(cfg)
        candidate_cfg.update(flag_updates)
        ok, errs = audit_invariants(candidate_cfg)
        if ok:
            tmp = config_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(candidate_cfg, indent=2))
            tmp.replace(config_path)

    return reports, flag_updates


if __name__ == "__main__":
    reps, updates = run_weekly_strategy_quarantine(apply_via_governor=False)
    print("=" * 80)
    print("  AUTOMATIC STRATEGY QUARANTINE & AUTO-RETURN REPORT (DRY-RUN)")
    print("=" * 80)
    for r in reps:
        print(f"  {r.strategy_id:<22} | {r.action:<14} | Tr={r.trades:>4} | PnL=${r.net_pnl:>+9.2f} | PF={r.profit_factor:>5.2f} | DD={r.max_drawdown_pct:>5.1f}%")
    print(f"\n  Proposed Config Flag Updates: {updates or 'None'}")
