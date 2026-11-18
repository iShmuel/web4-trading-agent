"""Zero-dependency, read-only Web Dashboard for the Web4 Trading Desk.

Serves a modern dark-theme browser UI and JSON API from standard-library
http.server without ever modifying paper_portfolio.json or active_config.json.

Usage:
    .venv/bin/python web_dashboard.py                  # http://127.0.0.1:8080
    .venv/bin/python web_dashboard.py --port 8090      # custom port
    .venv/bin/python web_dashboard.py --host 0.0.0.0   # LAN/VPN access
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.journal import summarize_maker_telemetry  # noqa: E402
from core.process_lock import is_locked, read_lock_pid  # noqa: E402

DATA_DIR = PROJECT_ROOT / "data"
CONFIG_PATH = PROJECT_ROOT / "config" / "active_config.json"
PORTFOLIO_FILE = DATA_DIR / "paper_portfolio.json"
STATUS_FILE = DATA_DIR / "live_status.json"
LOCK_FILE = DATA_DIR / "live_trader.lock"


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if path.exists() and path.stat().st_size > 0:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        return None
    return None


def _classify_watchlist_row(sym: str, action_obj: Dict[str, Any], price: Optional[float]) -> Dict[str, Any]:
    action = str(action_obj.get("action", "WAITING"))
    reason = str(action_obj.get("reason", ""))
    ind = action_obj.get("indicators", {}) or {}
    rl = reason.lower()
    is_blocked = ("blocked by" in rl) or ("suppressed" in rl)
    if action in ("REJECTED", "YIELD_MONITOR") and not is_blocked:
        action = "SCANNING"

    if action == "HOLD":
        strategy_label = str(action_obj.get("strategy_id", "Active"))
        trigger = f"Stop ${float(action_obj.get('stop_loss', 0.0)):,.2f}"
    elif "selected 'volatility_squeeze'" in rl:
        strategy_label, trigger = "Vol Squeeze", "Vol Squeeze"
    elif "selected 'trend_pullback'" in rl:
        strategy_label, trigger = "Trend Pullback", "Trend Pullback"
    elif "selected 'vwap_reversion'" in rl:
        strategy_label, trigger = "VWAP Reversion", "VWAP Reversion"
    elif "selected 'nr7_breakout'" in rl:
        strategy_label, trigger = "NR7 Breakout", "NR7 Breakout"
    elif "selected 'range_envelope'" in rl:
        strategy_label, trigger = "Range Envelope", "Range Envelope"
    elif "selected 'mean_reversion'" in rl:
        strategy_label, trigger = "Mean Reversion", "Mean Reversion"
    elif "selected 'momentum'" in rl or "no breakout" in rl or "macro_bear" in rl:
        strategy_label = "Momentum"
        trigger = reason[:56] if reason else "Momentum"
    elif "selected 'basis_yield'" in rl:
        fr = ind.get("funding_rate_pct")
        strategy_label = "Basis Yield"
        trigger = f"Carry FR {fr:+.3f}%" if isinstance(fr, (int, float)) else "Basis Yield"
    elif "selected 'cash'" in rl:
        strategy_label, trigger = "Stand Down", "Cash (PF < 1.15)"
    elif "stale data" in rl:
        strategy_label, trigger = "Market Closed", "Stale / Weekend"
    elif "blacklisted" in rl or "asset pruned" in rl:
        strategy_label, trigger = "Blacklisted", "Quarantined"
    else:
        strategy_label = str(action_obj.get("strategy_id", "Scanning"))
        trigger = reason[:36] if reason else "Scanning"

    return {
        "symbol": sym,
        "price": price,
        "status": action,
        "regime": str(ind.get("regime", "TRANSITION")),
        "strategy": strategy_label,
        "trigger": trigger,
        "reason": reason,
    }


def build_dashboard_payload(
    status_path: Optional[Path] = None,
    portfolio_path: Optional[Path] = None,
    config_path: Optional[Path] = None,
    lock_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Assemble read-only JSON payload for the web dashboard."""
    st_file = status_path or STATUS_FILE
    pf_file = portfolio_path or PORTFOLIO_FILE
    cfg_file = config_path or CONFIG_PATH
    lk_file = lock_path or LOCK_FILE

    status = _read_json(st_file) or {}
    portfolio = _read_json(pf_file) or {}
    cfg = _read_json(cfg_file) or {}

    closed = list(portfolio.get("closed_trades", []))
    wins = sum(1 for t in closed if float(t.get("pnl", 0.0)) > 0)
    realized = round(sum(float(t.get("pnl", 0.0)) for t in closed), 2)
    initial_cap = float(portfolio.get("initial_capital", 10000.0))

    first_ts = str(closed[0].get("entry_time", ""))[5:16].replace("T", " ") if closed else "Start"
    eq_curve: List[float] = [round(initial_cap, 2)]
    eq_points: List[Dict[str, Any]] = [{"eq": round(initial_cap, 2), "ts": first_ts or "Start", "label": f"Start ${initial_cap:,.0f}", "pnl": 0.0, "trade_idx": None, "kind": "start"}]
    running = initial_cap
    indexed_closed: List[Dict[str, Any]] = []
    for idx, t in enumerate(closed, start=1):
        indexed_closed.append({"trade_idx": idx, **t})
        pnl_t = float(t.get("pnl", 0.0))
        running += pnl_t
        eq_val = round(running, 2)
        eq_curve.append(eq_val)
        sym_t = str(t.get("symbol", "Trade"))
        sign_t = "+" if pnl_t >= 0 else ""
        ts_t = str(t.get("exit_time") or t.get("entry_time") or "")[5:16].replace("T", " ")
        eq_points.append({"eq": eq_val, "ts": ts_t, "label": f"#{idx} {sym_t} {sign_t}${pnl_t:,.2f}" + (f" ({ts_t})" if ts_t else ""), "pnl": round(pnl_t, 2), "trade_idx": idx, "kind": "trade"})
    if isinstance(status.get("equity"), (int, float)):
        live_eq = round(float(status["equity"]), 2)
        now_ts = str(status.get("updated_at") or datetime.now(timezone.utc).isoformat())[5:16].replace("T", " ")
        eq_curve.append(live_eq)
        eq_points.append({"eq": live_eq, "ts": now_ts, "label": f"Live ${live_eq:,.2f} ({now_ts})", "pnl": None, "trade_idx": None, "kind": "live"})

    prices = status.get("prices", {}) or {}
    actions = status.get("assets") or status.get("actions") or {}
    bl_set = set(cfg.get("blacklisted_symbols", []))
    watchlist = [s for s in cfg.get("watchlist", []) if s not in bl_set]
    rows = [_classify_watchlist_row(sym, actions.get(sym, {}), prices.get(sym)) for sym in watchlist]

    pairs_stop_counts = portfolio.get("pair_stop_counts", {}) or {}
    pairs_cooldowns = portfolio.get("pair_cooldown_until", {}) or {}
    pairs_blacklist = list(cfg.get("pairs_Blacklist", cfg.get("blacklisted_pairs", [])))

    pairs_rows = []
    for p in (cfg.get("pairs_universe") or []):
        if isinstance(p, (list, tuple)) and len(p) == 2:
            pid = f"{p[0]}:{p[1]}"
            p_dec = actions.get(pid, {}) or {}
            ind = p_dec.get("indicators", {}) or {}
            pairs_rows.append({
                "pair": pid,
                "correlation": round(float(ind.get("correlation", 0.0)), 2),
                "half_life": round(float(ind.get("half_life", 0.0)), 1),
                "z_score": round(float(ind.get("z_score", 0.0)), 2),
                "verdict": str(p_dec.get("reason") or p_dec.get("action") or "Scanning"),
                "strikes": int(pairs_stop_counts.get(pid, 0)),
                "cooldown_until": pairs_cooldowns.get(pid),
            })

    equity_val = float(status.get("equity", portfolio.get("cash", initial_cap)))
    open_positions = []
    total_open_notional = 0.0
    for sym, pos in (portfolio.get("positions", {}) or {}).items():
        cur_px = float(prices.get(sym, pos.get("entry_price", 0.0)))
        ep = float(pos.get("entry_price", 0.0))
        units = float(pos.get("units", 0.0))
        side = str(pos.get("side", "LONG")).upper()
        notional = abs(cur_px * units)
        total_open_notional += notional
        upnl = ((ep - cur_px) * units) if side == "SHORT" else ((cur_px - ep) * units)
        ret_pct = ((ep - cur_px) / ep * 100.0) if (side == "SHORT" and ep > 0) else (((cur_px - ep) / ep * 100.0) if ep > 0 else 0.0)
        stop_px = float(pos.get("trailing_stop", 0.0))
        stop_dist_pct = (
            ((stop_px - cur_px) / cur_px * 100.0) if (side == "SHORT" and cur_px > 0 and stop_px > 0)
            else (((cur_px - stop_px) / cur_px * 100.0) if (cur_px > 0 and stop_px > 0) else 0.0)
        )
        open_positions.append({
            "symbol": sym,
            "strategy_id": pos.get("strategy_id", "default"),
            "side": side,
            "units": round(units, 4),
            "entry_price": round(ep, 4),
            "current_price": round(cur_px, 4),
            "trailing_stop": round(stop_px, 4),
            "notional": round(notional, 2),
            "exposure_pct": round((notional / equity_val * 100.0) if equity_val > 0 else 0.0, 1),
            "return_pct": round(ret_pct, 2),
            "stop_dist_pct": round(stop_dist_pct, 2),
            "unrealized_pnl": round(upnl, 2),
        })

    strat_agg: Dict[str, Dict[str, Any]] = {}
    gross_profit_all = 0.0
    gross_loss_all = 0.0
    total_fees_all = 0.0
    for t in closed:
        sid = str(t.get("strategy_id") or "unknown")
        pnl_v = float(t.get("pnl", 0.0))
        fee_v = float(t.get("fees", 0.0))
        total_fees_all += fee_v
        if pnl_v > 0:
            gross_profit_all += pnl_v
        elif pnl_v < 0:
            gross_loss_all += abs(pnl_v)
        bucket = strat_agg.setdefault(sid, {"strategy_id": sid, "trades": 0, "wins": 0, "gross_profit": 0.0, "gross_loss": 0.0, "net_pnl": 0.0, "fees": 0.0})
        bucket["trades"] += 1
        if pnl_v > 0:
            bucket["wins"] += 1
            bucket["gross_profit"] += pnl_v
        elif pnl_v < 0:
            bucket["gross_loss"] += abs(pnl_v)
        bucket["net_pnl"] += pnl_v
        bucket["fees"] += fee_v

    strategy_breakdown = []
    for b in strat_agg.values():
        pf = (b["gross_profit"] / b["gross_loss"]) if b["gross_loss"] > 0 else (99.9 if b["gross_profit"] > 0 else 0.0)
        strategy_breakdown.append({
            "strategy_id": b["strategy_id"],
            "trades": b["trades"],
            "wins": b["wins"],
            "win_rate_pct": round((b["wins"] / b["trades"] * 100.0) if b["trades"] else 0.0, 1),
            "profit_factor": round(pf, 2),
            "fees": round(b["fees"], 2),
            "net_pnl": round(b["net_pnl"], 2),
        })
    strategy_breakdown.sort(key=lambda x: x["net_pnl"], reverse=True)
    overall_pf = (gross_profit_all / gross_loss_all) if gross_loss_all > 0 else (99.9 if gross_profit_all > 0 else 0.0)

    bot_running = is_locked(lk_file)
    bot_pid = read_lock_pid(lk_file) if bot_running else None
    updated_at = status.get("updated_at")
    scan_age_sec: Optional[int] = None
    if updated_at:
        try:
            dt = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
            scan_age_sec = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
        except Exception:
            scan_age_sec = None

    by_status = status.get("basis_yield") or {}
    by_ind = by_status.get("indicators", {}) or {}
    by_hurdle = float(cfg.get("basis_hurdle_apr", 5.0))
    by_top_apr = round(float(by_status.get("apr", by_ind.get("apr", 0.0))), 2)
    by_top_sym = str(by_status.get("symbol", "—"))
    by_fr_8h_pct = round(float(by_ind.get("funding_rate", 0.0)) * 100.0, 4)
    if by_fr_8h_pct == 0.0 and by_top_apr != 0.0:
        by_fr_8h_pct = round(by_top_apr / (3.0 * 365.0), 4)

    by_candidates = []
    seen_by_syms = set()
    for sym, dec in actions.items():
        if "/" not in sym or ":" in sym:
            continue
        ind = dec.get("indicators", {}) or {}
        fr_pct = ind.get("funding_rate_pct")
        if fr_pct is None:
            continue
        fr_pct_f = float(fr_pct)
        apr_f = round(fr_pct_f * 3.0 * 365.0, 2)
        seen_by_syms.add(sym)
        by_candidates.append({
            "symbol": sym,
            "funding_rate_8h_pct": round(fr_pct_f, 4),
            "apr": apr_f,
            "hurdle_met": apr_f >= by_hurdle,
            "funding_regime": str(ind.get("funding_regime", "NEUTRAL")),
            "status": "QUALIFIED" if apr_f >= by_hurdle else ("LOW YIELD" if apr_f > 0 else "NEGATIVE"),
        })
    if by_top_sym != "—" and by_top_sym not in seen_by_syms:
        by_candidates.append({
            "symbol": by_top_sym,
            "funding_rate_8h_pct": by_fr_8h_pct,
            "apr": by_top_apr,
            "hurdle_met": by_top_apr >= by_hurdle,
            "funding_regime": "ATTRACTIVE_YIELD" if by_top_apr >= by_hurdle else "LOW_YIELD",
            "status": "QUALIFIED" if by_top_apr >= by_hurdle else ("LOW YIELD" if by_top_apr > 0 else "NEGATIVE"),
        })
    by_candidates.sort(key=lambda x: x["apr"], reverse=True)

    by_payout_trades = [
        {
            "timestamp": str(t.get("exit_time") or t.get("entry_time") or ""),
            "symbol": str(t.get("symbol", "—")),
            "pnl": round(float(t.get("pnl", 0.0)), 2),
            "reason": str(t.get("reason", "")),
        }
        for t in closed
        if t.get("strategy_id") == "basis_yield"
    ]
    by_total_usd = round(sum(p["pnl"] for p in by_payout_trades), 2) if by_payout_trades else round(float(portfolio.get("basis_yield_total_usd") or 0.0), 2)
    by_payout_count = len(by_payout_trades) if by_payout_trades else int(portfolio.get("basis_yield_payouts") or 0)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bot": {
            "running": bot_running,
            "pid": bot_pid,
            "scan_age_sec": scan_age_sec,
            "auto_approval": bool(cfg.get("enable_conditional_auto_approval", False)),
            "paper_only": True,
            "fred_vix_regime": str(status.get("fred_vix_regime", "STABLE (Crash Defense OFF)")),
        },
        "account": {
            "initial_capital": round(initial_cap, 2),
            "equity": round(equity_val, 2),
            "return_vs_initial_pct": round(((equity_val - initial_cap) / initial_cap * 100.0) if initial_cap > 0 else 0.0, 2),
            "cash": round(float(portfolio.get("cash", 0.0)), 2),
            "unrealized_pnl": round(sum(p["unrealized_pnl"] for p in open_positions), 2),
            "realized_pnl": realized,
            "closed_trades": len(closed),
            "wins": wins,
            "losses": len(closed) - wins,
            "win_rate_pct": round((wins / len(closed) * 100.0) if closed else 0.0, 1),
            "drawdown_pct": round(float(portfolio.get("current_drawdown", 0.0)) * 100.0, 2),
            "peak_dd_limit_pct": float(cfg.get("max_drawdown_limit_pct", 12.0)),
            "circuit_breaker": str(portfolio.get("circuit_breaker_status", "OK")),
        },
        "basis_yield": {
            "enabled": bool(cfg.get("enable_basis_yield", True)),
            "total_carry_usd": by_total_usd,
            "payouts": by_payout_count,
            "hurdle_apr": by_hurdle,
            "max_allocation_pct": round(float(cfg.get("max_basis_allocation_pct", 0.30)) * 100.0, 1),
            "top_symbol": by_top_sym,
            "top_apr": by_top_apr,
            "funding_rate_8h_pct": by_fr_8h_pct,
            "hurdle_met": bool(by_ind.get("hurdle_met", by_top_apr >= by_hurdle)),
            "hours_elapsed": round(float(by_ind.get("hours_elapsed", 0.0)), 1),
            "next_epoch_hours": round(float(by_ind.get("hours_remaining", 0.0)), 1),
            "last_accrual_time": portfolio.get("last_basis_accrual_time"),
            "reason": str(by_status.get("reason", "")),
            "candidates": by_candidates,
            "recent_payouts": list(reversed(by_payout_trades[-10:])),
        },
        "maker_telemetry": summarize_maker_telemetry(),
        "risk_telemetry": {
            "open_notional": round(total_open_notional, 2),
            "open_exposure_pct": round((total_open_notional / equity_val * 100.0) if equity_val > 0 else 0.0, 1),
            "open_count": len(open_positions),
            "max_open_positions": int(cfg.get("max_concurrent_positions", 5)),
            "peak_equity": round(float(portfolio.get("peak_equity", equity_val)), 2),
            "total_fees": round(total_fees_all, 2),
            "profit_factor": round(overall_pf, 2),
            "blacklisted_symbols_count": len(bl_set),
            "blacklisted_pairs": pairs_blacklist,
            "pair_stop_counts": pairs_stop_counts,
        },
        "strategy_breakdown": strategy_breakdown,
        "open_positions": open_positions,
        "watchlist": rows,
        "pairs": pairs_rows,
        "recent_trades": list(reversed(indexed_closed)),
        "closed_trades_history": list(reversed(indexed_closed[-120:])),
        "strategies_meta": {
            "all": {
                "name": "Dynamic Rolling Router (All Watchlist)",
                "enabled": bool(cfg.get("enable_dynamic_rolling_router", True)),
                "description": "Evaluates every watchlist symbol per scan and routes to the highest-PF strategy for its current regime.",
                "params": {
                    "timeframe": cfg.get("timeframe", "1h"),
                    "min_rolling_pf": cfg.get("dynamic_router_min_pf", 1.15),
                    "router_cache_sec": cfg.get("dynamic_router_cache_seconds", 21600),
                    "max_positions": cfg.get("max_concurrent_positions", 5),
                    "max_crypto_pos": cfg.get("max_crypto_positions", 2),
                    "account_risk_pct": f"{float(cfg.get('account_risk_pct', 0.02))*100:.1f}%",
                    "execution_mode": cfg.get("execution_mode", "maker"),
                    "min_confluence": cfg.get("min_confluence_score", 0.7),
                },
            },
            "basis_yield": {
                "name": "Delta-Neutral Basis Yield Carry",
                "enabled": bool(cfg.get("enable_basis_yield", True)),
                "description": "Sweeps idle cash into market-neutral perpetual funding carry every 8h epoch when APR >= hurdle.",
                "params": {
                    "hurdle_apr": f"{float(cfg.get('basis_hurdle_apr', 5.0)):.1f}%",
                    "max_allocation": f"{float(cfg.get('max_basis_allocation_pct', 0.30))*100:.1f}%",
                    "epoch_hours": "8.0h",
                },
            },
            "stat_arb_pairs": {
                "name": "Cointegrated Stat-Arb Pairs",
                "enabled": bool(cfg.get("enable_pairs_trading", True)),
                "description": "Trades mean-reverting spread divergences on cointegrated pairs with Z-score and hard leg stops.",
                "params": {
                    "lookback_bars": cfg.get("stat_arb_lookback", 48),
                    "entry_z": cfg.get("stat_arb_entry_z", 2.5),
                    "exit_z": cfg.get("stat_arb_exit_z", 0.5),
                    "stop_z": cfg.get("stat_arb_stop_z", 3.5),
                    "min_corr": cfg.get("min_pair_correlation", 0.7),
                    "min_ret_corr": cfg.get("min_pair_return_correlation", 0.45),
                    "max_half_life_h": cfg.get("max_pair_half_life_hours", 72.0),
                    "pair_risk_pct": f"{float(cfg.get('stat_arb_risk_pct', 0.02))*100:.1f}%",
                    "max_leg_eq_pct": f"{float(cfg.get('stat_arb_max_leg_equity_pct', 0.15))*100:.1f}%",
                    "min_edge_cost_ratio": cfg.get("stat_arb_min_edge_cost_ratio", 2.5),
                    "reversal_hook": cfg.get("stat_arb_require_reversal_hook", True),
                    "stop_cooldown_h": cfg.get("pair_stop_cooldown_hours", 12.0),
                },
            },
            "momentum": {
                "name": "Momentum / Trend Following",
                "enabled": True,
                "description": "Breakout trend-following gated by macro EMA, ATR trailing stops, and breakeven/chandelier ratchets.",
                "params": {
                    "timeframe": cfg.get("timeframe", "1h"),
                    "breakout_lookback": cfg.get("breakout_lookback", 36),
                    "ema_trend_filter": cfg.get("ema_trend_filter", 200),
                    "atr_period": cfg.get("atr_period", 14),
                    "atr_multiplier": cfg.get("atr_multiplier", 2.75),
                    "take_profit_atr": cfg.get("take_profit_atr_mult", 2.5),
                    "allow_short": cfg.get("allow_short", True),
                    "short_macro_bear": cfg.get("short_requires_macro_bear", True),
                    "breakeven_atr": cfg.get("breakeven_atr_mult", 1.2),
                    "chandelier_ratchet": cfg.get("enable_chandelier_ratchet", True),
                },
            },
            "mean_reversion": {
                "name": "Mean Reversion",
                "enabled": True,
                "description": "Fades stretched Bollinger/RSI extremes in range-bound or choppy regimes.",
                "params": {
                    "timeframe": cfg.get("timeframe", "1h"),
                    "bb_std": cfg.get("bb_std", 2.1),
                    "rsi_oversold": cfg.get("rsi_oversold", 28.0),
                    "rsi_overbought": cfg.get("rsi_overbought", 72.0),
                    "adx_threshold": cfg.get("adx_threshold", 24.0),
                    "mr_allow_short": cfg.get("mr_allow_short", True),
                    "throttle_chop_risk": cfg.get("throttle_chop_risk", True),
                    "atr_multiplier": cfg.get("atr_multiplier", 2.75),
                    "hurst_gate": f"<= {float(cfg.get('hurst_mr_max', 0.52)):.2f}" if cfg.get("enable_hurst_gate", False) else "OFF",
                },
            },
            "volatility_squeeze": {
                "name": "Volatility Squeeze",
                "enabled": True,
                "description": "Captures expansion breakouts out of low-volatility compression bands.",
                "params": {
                    "timeframe": cfg.get("timeframe", "1h"),
                    "atr_period": cfg.get("atr_period", 14),
                    "atr_multiplier": cfg.get("atr_multiplier", 2.75),
                    "take_profit_atr": cfg.get("take_profit_atr_mult", 2.5),
                    "volume_surge_mult": cfg.get("volume_surge_mult", 1.5),
                    "min_confluence": cfg.get("min_confluence_score", 0.7),
                },
            },
            "trend_pullback": {
                "name": "Trend Pullback",
                "enabled": True,
                "description": "Enters pullbacks in established trends when ADX confirms directional strength.",
                "params": {
                    "timeframe": cfg.get("timeframe", "1h"),
                    "ema_trend_filter": cfg.get("ema_trend_filter", 200),
                    "adx_trend_threshold": cfg.get("adx_trend_threshold", 28.0),
                    "atr_period": cfg.get("atr_period", 14),
                    "atr_multiplier": cfg.get("atr_multiplier", 2.75),
                    "take_profit_atr": cfg.get("take_profit_atr_mult", 2.5),
                },
            },
            "vwap_reversion": {
                "name": "VWAP Reversion",
                "enabled": bool(cfg.get("enable_vwap_reversion", True)),
                "description": "Fades intraday/hourly extensions away from VWAP with liquidity-sweep confirmation.",
                "params": {
                    "timeframe": cfg.get("timeframe", "1h"),
                    "enable_liquidity_sweep": cfg.get("enable_liquidity_sweep", True),
                    "sweep_lookback": cfg.get("sweep_lookback", 20),
                    "sweep_rsi_max": cfg.get("sweep_rsi_max", 40.0),
                    "atr_multiplier": cfg.get("atr_multiplier", 2.75),
                    "take_profit_atr": cfg.get("take_profit_atr_mult", 2.5),
                },
            },
            "nr7_breakout": {
                "name": "NR7 Compression Breakout",
                "enabled": bool(cfg.get("enable_nr7_breakout", True)),
                "description": "Enters upward breakouts following the narrowest high-low range bar in the trailing lookback window.",
                "params": {
                    "timeframe": cfg.get("timeframe", "1h"),
                    "nr7_lookback": cfg.get("nr7_lookback", 7),
                    "nr7_atr_stop_mult": cfg.get("nr7_atr_stop_mult", 2.0),
                    "atr_period": cfg.get("atr_period", 14),
                },
            },
            "range_envelope": {
                "name": "Rolling Range Envelope (Average Down/Top + Net Dollar Floor)",
                "enabled": bool(cfg.get("enable_range_envelope", True)),
                "description": "Enters at rolling Average Down (q10) or Average Top (q90) when range covers fees + minimum net dollar floor, scaling out 50% at midpoint/floor (+Break-Even lock) and exiting at opposite boundary.",
                "params": {
                    "timeframe": cfg.get("timeframe", "1h"),
                    "range_window": cfg.get("range_window", 36),
                    "range_low_quantile": cfg.get("range_low_quantile", 0.10),
                    "range_high_quantile": cfg.get("range_high_quantile", 0.90),
                    "range_rsi_oversold": cfg.get("range_rsi_oversold", 30.0),
                    "range_rsi_overbought": cfg.get("range_rsi_overbought", 70.0),
                    "range_adx_max": cfg.get("range_adx_max", 24.0),
                    "range_min_net_profit_usd": cfg.get("range_min_net_profit_usd", 10.0),
                    "range_allow_short": cfg.get("range_allow_short", True),
                },
            },
        },
        "equity_curve": eq_curve[-120:],
        "equity_points": eq_points[-120:],
    }


HTML_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Web4 Autonomous Trading Desk</title>
<style>
  :root { --bg:#0b1120; --card:#111827; --border:#1f2937; --text:#e5e7eb; --muted:#9ca3af; --green:#10b981; --red:#ef4444; --cyan:#38bdf8; --amber:#f59e0b; --purple:#a78bfa; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--text); }
  .wrap { max-width:1520px; margin:0 auto; padding:12px 18px; }
  .topbar { display:flex; flex-wrap:wrap; gap:8px; justify-content:space-between; align-items:center; background:var(--card); border:1px solid var(--border); border-radius:10px; padding:8px 14px; margin-bottom:12px; }
  .title { font-size:15px; font-weight:700; color:var(--cyan); }
  .badges { display:flex; flex-wrap:wrap; gap:6px; font-size:11px; }
  .badge { padding:2px 8px; border-radius:999px; background:#1e293b; border:1px solid var(--border); }
  .ok { color:var(--green); border-color:rgba(16,185,129,.35); }
  .warn { color:var(--amber); border-color:rgba(245,158,11,.35); }
  .bad { color:var(--red); border-color:rgba(239,68,68,.35); }
  .overview-layout { display:grid; grid-template-columns:275px minmax(0,1fr); gap:16px; align-items:start; }
  .overview-main { min-width:0; }
  .grid { display:grid; grid-template-columns:1fr; gap:12px; }
  @media (max-width:1020px) { .overview-layout { grid-template-columns:1fr; } .grid { grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); } }
  .card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:14px 16px; }
  .k { font-size:12px; color:var(--muted); margin-bottom:6px; }
  .v { font-size:22px; font-weight:700; }
  .sub { font-size:12px; color:var(--muted); margin-top:4px; }
  .section { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:16px; margin-bottom:16px; overflow-x:auto; }
  .sec-head { display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; gap:10px; margin-bottom:12px; }
  h2 { margin:0; font-size:15px; color:var(--cyan); }
  .controls { display:flex; gap:8px; }
  input, select { background:#0f172a; color:var(--text); border:1px solid var(--border); border-radius:8px; padding:6px 10px; font-size:13px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--border); white-space:nowrap; }
  th { color:var(--muted); font-weight:600; font-size:12px; }
  .pill { padding:2px 8px; border-radius:6px; font-size:12px; font-weight:600; background:#1e293b; }
  .p-green { color:var(--green); background:rgba(16,185,129,.12); }
  .p-cyan { color:var(--cyan); background:rgba(56,189,248,.12); }
  .p-purple { color:var(--purple); background:rgba(167,139,250,.12); }
  .p-muted { color:var(--muted); }
  .zbar { position:relative; display:inline-block; width:110px; height:8px; background:#1e293b; border-radius:4px; vertical-align:middle; margin-right:6px; }
  .zbar-mid { position:absolute; left:50%; top:-2px; width:1px; height:12px; background:#475569; }
  .zbar-dot { position:absolute; top:-1px; width:8px; height:10px; border-radius:3px; transform:translateX(-50%); }
  svg { width:100%; height:220px; display:block; overflow:visible; }
  .eq-wrap { position:relative; }
  .eq-dot { cursor:pointer; transition:r .1s; }
  .eq-dot:hover { r:5.5; }
  .row-highlight td { background:rgba(56,189,248,.18); transition:background .4s ease; }
  .row-highlight td:first-child { box-shadow:inset 3px 0 0 var(--cyan); }
  #eq-tip { position:fixed; display:none; pointer-events:none; background:#0f172a; border:1px solid var(--cyan); color:var(--text); padding:4px 9px; border-radius:6px; font-size:12px; font-weight:600; z-index:99; white-space:nowrap; box-shadow:0 4px 12px rgba(0,0,0,.5); }
  .two-col { display:grid; grid-template-columns:repeat(auto-fit,minmax(460px,1fr)); gap:16px; margin-bottom:16px; }
  .two-col .section { margin-bottom:0; min-height:290px; }
  .nav-tabs { display:flex; gap:6px; align-items:center; }
  .nav-tab { padding:4px 10px; border-radius:7px; font-size:12px; font-weight:600; color:var(--muted); background:#0f172a; border:1px solid var(--border); text-decoration:none; cursor:pointer; }
  .nav-tab:hover { color:var(--text); border-color:#334155; }
  .nav-tab.active { color:var(--cyan); border-color:var(--cyan); background:rgba(56,189,248,.12); }
  .nav-dd { position:relative; }
  .nav-menu { position:absolute; top:calc(100% + 6px); left:0; min-width:240px; background:#0f172a; border:1px solid var(--border); border-radius:10px; padding:6px; box-shadow:0 12px 28px rgba(0,0,0,.55); z-index:120; display:none; }
  .nav-menu.open { display:block; }
  .nav-item { display:block; padding:7px 10px; border-radius:7px; color:var(--text); text-decoration:none; font-size:12.5px; font-weight:500; cursor:pointer; }
  .nav-item:hover { background:rgba(56,189,248,.12); color:var(--cyan); }
  .nav-item.active { background:rgba(56,189,248,.18); color:var(--cyan); font-weight:700; }
  .param-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(175px,1fr)); gap:10px; margin-top:10px; }
  .param-box { background:#0f172a; border:1px solid var(--border); border-radius:8px; padding:8px 10px; }
  .param-k { font-size:11px; color:var(--muted); margin-bottom:3px; }
  .param-v { font-size:14px; font-weight:700; color:var(--text); }
  .hidden { display:none; }
  .closed-wrap { max-height:360px; overflow-y:auto; border-top:1px solid var(--border); }
  .closed-section { margin-bottom:0; }
  .closed-table { font-size:12.5px; }
  .closed-table th, .closed-table td { padding:7px 8px; }
  .closed-table thead th { position:sticky; top:0; background:var(--card); z-index:2; }
  .closed-table td:last-child { white-space:normal; word-break:break-word; min-width:180px; }
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div style="display:flex;flex-wrap:wrap;align-items:center;gap:14px">
      <div class="title">WEB4 AUTONOMOUS TRADING DESK — LIVE WEB TERMINAL</div>
      <nav class="nav-tabs">
        <a class="nav-tab active" href="/" data-page="overview" onclick="return navClick(event,'overview')">Overview</a>
        <div class="nav-dd">
          <a class="nav-tab" id="strat-nav-btn" href="/strategies" data-page="strategies" onclick="return toggleStratMenu(event)">Strategies &amp; Scanners &#9662;</a>
          <div class="nav-menu" id="strat-menu">
            <a class="nav-item" href="/strategies" data-strat="all" onclick="return selectStrategy(event,'all')">All Strategies &amp; Watchlist</a>
            <a class="nav-item" href="/strategies/basis_yield" data-strat="basis_yield" onclick="return selectStrategy(event,'basis_yield')">Basis Yield Carry</a>
            <a class="nav-item" href="/strategies/stat_arb_pairs" data-strat="stat_arb_pairs" onclick="return selectStrategy(event,'stat_arb_pairs')">Stat-Arb Pairs</a>
            <a class="nav-item" href="/strategies/momentum" data-strat="momentum" onclick="return selectStrategy(event,'momentum')">Momentum</a>
            <a class="nav-item" href="/strategies/mean_reversion" data-strat="mean_reversion" onclick="return selectStrategy(event,'mean_reversion')">Mean Reversion</a>
            <a class="nav-item" href="/strategies/volatility_squeeze" data-strat="volatility_squeeze" onclick="return selectStrategy(event,'volatility_squeeze')">Volatility Squeeze</a>
            <a class="nav-item" href="/strategies/trend_pullback" data-strat="trend_pullback" onclick="return selectStrategy(event,'trend_pullback')">Trend Pullback</a>
            <a class="nav-item" href="/strategies/vwap_reversion" data-strat="vwap_reversion" onclick="return selectStrategy(event,'vwap_reversion')">VWAP Reversion</a>
            <a class="nav-item" href="/strategies/nr7_breakout" data-strat="nr7_breakout" onclick="return selectStrategy(event,'nr7_breakout')">NR7 Breakout</a>
            <a class="nav-item" href="/strategies/range_envelope" data-strat="range_envelope" onclick="return selectStrategy(event,'range_envelope')">Range Envelope</a>
          </div>
        </div>
      </nav>
    </div>
    <div class="badges" id="top-badges">Loading...</div>
  </div>

  <div id="page-overview">
    <div class="overview-layout">
      <div class="grid" id="kpi-grid"></div>
      <div class="overview-main">
        <div class="two-col">
          <div class="section">
            <div class="sec-head"><h2>Portfolio Equity Curve (Realized Ledger + Live Mark)</h2><span class="sub" id="eq-range"></span></div>
            <div class="eq-wrap"><svg id="eq-svg" viewBox="0 0 800 220" preserveAspectRatio="none"></svg></div>
          </div>
          <div class="section">
            <div class="sec-head"><h2>Strategy Performance Breakdown</h2><span class="sub" id="strat-sub"></span></div>
            <table><thead><tr><th>Strategy</th><th>Trades</th><th>Win Rate</th><th>Profit Factor</th><th>Fees Paid</th><th>Net P&amp;L</th></tr></thead><tbody id="strat-body"></tbody></table>
          </div>
        </div>
        <div id="eq-tip"></div>

        <div class="section">
          <div class="sec-head"><h2>Open Positions</h2><span class="sub" id="open-sub"></span></div>
          <table><thead><tr><th>Symbol</th><th>Strategy</th><th>Side</th><th>Units</th><th>Notional</th><th>Entry</th><th>Now</th><th>Stop (Dist)</th><th>Unrealized P&amp;L</th></tr></thead><tbody id="open-body"></tbody></table>
        </div>

        <div class="section closed-section">
          <div class="sec-head">
            <h2>All Closed Fills <span class="sub" id="closed-sub" style="font-weight:400;margin-left:8px"></span></h2>
            <div class="controls">
              <select id="closed-filter"><option value="">All Strategies</option><option>momentum</option><option>mean_reversion</option><option>stat_arb_pairs</option><option>basis_yield</option><option>volatility_squeeze</option><option>trend_pullback</option><option>vwap_reversion</option><option>nr7_breakout</option><option>range_envelope</option></select>
            </div>
          </div>
          <div class="closed-wrap">
            <table class="closed-table"><thead><tr><th>Time (UTC)</th><th>Symbol</th><th>Strategy</th><th>Side</th><th>Entry</th><th>Exit</th><th>Fees</th><th>Net P&amp;L</th><th>Return</th><th>Reason</th></tr></thead><tbody id="closed-body"></tbody></table>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div id="page-strategies" class="hidden">
    <div class="section" id="strat-detail-card">
      <div class="sec-head">
        <div>
          <h2 id="sd-title">Strategy</h2>
          <div class="sub" id="sd-desc"></div>
        </div>
        <div class="badges" id="sd-badges"></div>
      </div>
      <div class="param-grid" id="sd-params"></div>
    </div>

    <div class="section" id="sec-watchlist">
      <div class="sec-head">
        <h2>Live Watchlist (Web4 Dynamic Rolling Router)</h2>
        <div class="controls">
          <input id="wl-search" placeholder="Search symbol..." />
          <select id="wl-filter"><option value="">All Strategies</option><option>Vol Squeeze</option><option>Trend Pullback</option><option>VWAP Reversion</option><option>NR7 Breakout</option><option>Range Envelope</option><option>Mean Reversion</option><option>Momentum</option><option>Basis Yield</option><option>Stand Down</option><option>Market Closed</option></select>
        </div>
      </div>
      <table><thead><tr><th>Symbol</th><th>Price</th><th>Status</th><th>Regime</th><th>Active Strategy</th><th>Trigger / Info</th></tr></thead><tbody id="wl-body"></tbody></table>
    </div>

    <div class="section" id="sec-basis">
      <div class="sec-head"><h2>Delta-Neutral Basis Yield Carry Desk</h2><span class="sub" id="basis-sub"></span></div>
      <div id="basis-summary" style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:10px;font-size:12px"></div>
      <table><thead><tr><th>Symbol</th><th>8h Funding</th><th>Annualized APR</th><th>Hurdle</th><th>Funding Regime</th><th>Status</th></tr></thead><tbody id="basis-body"></tbody></table>
    </div>

    <div class="section" id="sec-pairs">
      <div class="sec-head"><h2>Cointegrated Stat-Arb Pairs Desk</h2><span class="sub" id="pairs-sub"></span></div>
      <table><thead><tr><th>Pair</th><th>Correlation</th><th>Half-Life</th><th>Z-Score Divergence</th><th>Strikes / Guard</th><th>Status / Verdict</th></tr></thead><tbody id="pairs-body"></tbody></table>
    </div>

    <div class="section hidden" id="sec-strat-fills">
      <div class="sec-head"><h2>Closed Fills for Strategy</h2><span class="sub" id="sf-sub"></span></div>
      <div class="closed-wrap">
        <table class="closed-table"><thead><tr><th>Time (UTC)</th><th>Symbol</th><th>Strategy</th><th>Side</th><th>Entry</th><th>Exit</th><th>Fees</th><th>Net P&amp;L</th><th>Return</th><th>Reason</th></tr></thead><tbody id="sf-body"></tbody></table>
      </div>
    </div>
  </div>
</div>
<script>
let lastData = null;
let selectedTradeIdx = null;
let highlightTimer = null;
let currentStrategy = 'all';
const STRAT_WL_MAP = {
  momentum: 'Momentum',
  mean_reversion: 'Mean Reversion',
  volatility_squeeze: 'Vol Squeeze',
  trend_pullback: 'Trend Pullback',
  vwap_reversion: 'VWAP Reversion',
  nr7_breakout: 'NR7 Breakout',
  range_envelope: 'Range Envelope',
  basis_yield: 'Basis Yield',
};
const fmt = (n, d=2) => (n === null || n === undefined) ? '—' : Number(n).toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});
function stratPill(s){
  if(['Vol Squeeze','Trend Pullback','VWAP Reversion','NR7 Breakout','Range Envelope'].includes(s)) return `<span class="pill p-green">${s}</span>`;
  if(s==='Basis Yield') return `<span class="pill p-cyan">${s}</span>`;
  if(['Mean Reversion','Momentum'].includes(s)) return `<span class="pill p-purple">${s}</span>`;
  return `<span class="pill p-muted">${s}</span>`;
}
function renderWatchlist(){
  if(!lastData) return;
  const q = document.getElementById('wl-search').value.trim().toUpperCase();
  const f = document.getElementById('wl-filter').value;
  const rows = lastData.watchlist.filter(r => (!q || r.symbol.includes(q)) && (!f || r.strategy === f));
  document.getElementById('wl-body').innerHTML = rows.map(r => `<tr>
    <td><b>${r.symbol}</b></td><td>${r.price !== null ? '$'+fmt(r.price, r.price < 1 ? 5 : 2) : '—'}</td>
    <td>${r.status}</td><td>${r.regime}</td><td>${stratPill(r.strategy)}</td><td>${r.trigger}</td>
  </tr>`).join('') || '<tr><td colspan="6" class="p-muted">No matching symbols</td></tr>';
}
function showEqTip(e, txt){
  const tip = document.getElementById('eq-tip');
  tip.textContent = txt;
  tip.style.display = 'block';
  tip.style.left = Math.min(window.innerWidth - 220, e.clientX + 10) + 'px';
  tip.style.top = Math.max(8, e.clientY - 30) + 'px';
}
function hideEqTip(){ document.getElementById('eq-tip').style.display = 'none'; }
function renderCurve(pts, metaPts){
  const svg = document.getElementById('eq-svg');
  if(!pts || pts.length < 2){ svg.innerHTML = ''; return; }
  const min = Math.min(...pts), max = Math.max(...pts), span = (max - min) || 1;
  document.getElementById('eq-range').textContent = `Min $${fmt(min)} | Max $${fmt(max)} | Now $${fmt(pts[pts.length-1])}`;
  const x0 = 58, x1 = 790, y0 = 14, y1 = 192, w = x1 - x0, h = y1 - y0;
  const hGrid = [0, 0.5, 1].map(f => {
    const y = (y0 + f * h).toFixed(1);
    const val = max - f * span;
    return `<line x1="${x0}" y1="${y}" x2="${x1}" y2="${y}" stroke="#1f2937" stroke-dasharray="3,3" /><text x="52" y="${Number(y)+3}" text-anchor="end" fill="#9ca3af" font-size="10">$${fmt(val,0)}</text>`;
  }).join('');
  let xTicks = '', seenDays = new Set(), lastLabelX = -999;
  for(let i = 0; i < pts.length; i++){
    const rawTs = (metaPts && metaPts[i] && metaPts[i].ts) ? String(metaPts[i].ts) : '';
    const day = rawTs.slice(0, 5);
    if(!day || seenDays.has(day)) continue;
    seenDays.add(day);
    const xNum = x0 + (i / (pts.length - 1)) * w;
    const x = xNum.toFixed(1);
    xTicks += `<line x1="${x}" y1="${y0}" x2="${x}" y2="${y1}" stroke="#1e293b" stroke-dasharray="2,3" />`;
    if(xNum - lastLabelX >= 36){
      const anchor = i === 0 ? 'start' : (xNum > x1 - 24 ? 'end' : 'middle');
      xTicks += `<text x="${x}" y="212" text-anchor="${anchor}" fill="#9ca3af" font-size="10">${day}</text>`;
      lastLabelX = xNum;
    }
  }
  const xy = pts.map((v,i) => [Number((x0 + (i/(pts.length-1))*w).toFixed(1)), Number((y1 - ((v-min)/span)*h).toFixed(1))]);
  const coords = xy.map(p => `${p[0]},${p[1]}`).join(' ');
  const dots = xy.map((p, i) => {
    const m = (metaPts && metaPts[i]) || {label: `$${fmt(pts[i])}`, pnl: null};
    const fill = m.pnl === null ? '#38bdf8' : (m.pnl > 0 ? '#10b981' : (m.pnl < 0 ? '#ef4444' : '#38bdf8'));
    const safeTxt = String(m.label || '').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    const tid = m.trade_idx === null || m.trade_idx === undefined ? 'null' : Number(m.trade_idx);
    const kind = String(m.kind || '');
    return `<circle class="eq-dot" cx="${p[0]}" cy="${p[1]}" r="3" fill="${fill}" onclick="focusTradeFromDot(${tid},'${kind}')" onmouseenter="showEqTip(event,'${safeTxt}')" onmousemove="showEqTip(event,'${safeTxt}')" onmouseleave="hideEqTip()"><title>${safeTxt}</title></circle>`;
  }).join('');
  svg.innerHTML = `${hGrid}${xTicks}<polyline fill="none" stroke="#38bdf8" stroke-width="2" points="${coords}" />${dots}`;
}
function render(d){
  lastData = d;
  document.getElementById('top-badges').innerHTML = `
    <span class="badge ${d.bot.running ? 'ok':'bad'}">${d.bot.running ? 'RUNNING PID '+d.bot.pid : 'STOPPED'}</span>
    <span class="badge">Scan: ${d.bot.scan_age_sec !== null ? d.bot.scan_age_sec+'s ago' : '—'}</span>
    <span class="badge ok">PAPER ONLY</span>
    <span class="badge ok">VIX: ${d.bot.fred_vix_regime}</span>
    <span class="badge">${d.bot.auto_approval ? '4-Gate Auto-Approval: ON':'Manual Approval'}</span>`;
  const a = d.account, by = d.basis_yield, mt = d.maker_telemetry, rt = d.risk_telemetry || {};
  document.getElementById('kpi-grid').innerHTML = `
    <div class="card"><div class="k">Total Equity</div><div class="v">$${fmt(a.equity)} <span class="${a.return_vs_initial_pct>=0?'ok':'bad'}" style="font-size:13px">(${a.return_vs_initial_pct>=0?'+':''}${fmt(a.return_vs_initial_pct)}% vs $${fmt(a.initial_capital,0)})</span></div><div class="sub">Cash: $${fmt(a.cash)} | Unrealized: <span class="${a.unrealized_pnl>=0?'ok':'bad'}">$${fmt(a.unrealized_pnl)}</span> | Peak: $${fmt(rt.peak_equity ?? a.equity)}</div></div>
    <div class="card"><div class="k">Open Exposure &amp; Risk</div><div class="v">${rt.open_count ?? 0}/${rt.max_open_positions ?? 5}</div><div class="sub">${fmt(rt.open_exposure_pct ?? 0, 1)}% deployed ($${fmt(rt.open_notional ?? 0)})</div></div>
    <div class="card"><div class="k">Drawdown &amp; Breaker</div><div class="v ${a.circuit_breaker==='OK'?'ok':'bad'}">${a.drawdown_pct}% <span class="p-muted" style="font-size:13px">/ ${fmt(a.peak_dd_limit_pct ?? 12,1)}%</span></div><div class="sub">Breaker: ${a.circuit_breaker} | Pruned: ${rt.blacklisted_symbols_count ?? 0}</div></div>
    <div class="card"><div class="k">Closed Fills (${a.closed_trades})</div><div class="v ${a.realized_pnl>=0?'ok':'bad'}">$${fmt(a.realized_pnl)}</div><div class="sub">WR: ${a.win_rate_pct}% (${a.wins}W / ${a.losses}L) | PF: ${fmt(rt.profit_factor ?? 0)} | Fees: $${fmt(rt.total_fees ?? 0)}</div></div>
    <div class="card"><div class="k">Basis Yield Carry</div><div class="v ok">+$${fmt(by.total_carry_usd)}</div><div class="sub">${by.payouts} payouts | Top: ${by.top_symbol} (${fmt(by.top_apr)}% / ${by.hurdle_apr}%) | Next: ${by.next_epoch_hours}h</div></div>
    <div class="card"><div class="k">Maker Telemetry</div><div class="v">${mt.fill_rate_pct}%</div><div class="sub">${mt.filled}/${mt.total} fills | ${mt.avg_slippage_bps} bps slip</div></div>`;
  renderCurve(d.equity_curve, d.equity_points);
  document.getElementById('open-sub').textContent = `${d.open_positions.length} open | $${fmt(rt.open_notional ?? 0)} notional (${fmt(rt.open_exposure_pct ?? 0, 1)}% eq)`;
  document.getElementById('open-body').innerHTML = d.open_positions.map(p => `<tr>
    <td><b>${p.symbol}</b></td><td>${p.strategy_id}</td><td>${p.side}</td><td>${p.units}</td>
    <td>$${fmt(p.notional)} (${fmt(p.exposure_pct,1)}%)</td>
    <td>$${fmt(p.entry_price, p.entry_price < 1 ? 4 : 2)}</td><td>$${fmt(p.current_price, p.current_price < 1 ? 4 : 2)}</td>
    <td>$${fmt(p.trailing_stop, p.trailing_stop < 1 ? 4 : 2)} (${fmt(p.stop_dist_pct, 1)}%)</td>
    <td class="${p.unrealized_pnl>=0?'ok':'bad'}">$${fmt(p.unrealized_pnl)} (${p.return_pct>=0?'+':''}${fmt(p.return_pct)}%)</td>
  </tr>`).join('') || '<tr><td colspan="9" class="p-muted">No open positions</td></tr>';
  const sb = d.strategy_breakdown || [];
  document.getElementById('strat-sub').textContent = `${sb.length} strategies with closed fills`;
  document.getElementById('strat-body').innerHTML = sb.map(s => `<tr>
    <td><b>${s.strategy_id}</b></td><td>${s.trades} (${s.wins}W)</td><td>${fmt(s.win_rate_pct,1)}%</td>
    <td>${fmt(s.profit_factor)}</td><td>$${fmt(s.fees)}</td>
    <td class="${s.net_pnl>=0?'ok':'bad'}">$${fmt(s.net_pnl)}</td>
  </tr>`).join('') || '<tr><td colspan="6" class="p-muted">No strategy fills yet</td></tr>';
  renderWatchlist();
  const byMeta = by.hurdle_met ? '<span class="pill p-green">QUALIFIED</span>' : '<span class="pill warn">BELOW HURDLE</span>';
  const byElapsed = Number(by.hours_elapsed || 0).toFixed(1);
  const byNext = Number(by.next_epoch_hours || 0).toFixed(1);
  const byLast = by.last_accrual_time ? String(by.last_accrual_time).replace('T',' ').slice(0,16)+' UTC' : 'None yet';
  document.getElementById('basis-sub').textContent = `${by.enabled ? 'ENABLED' : 'DISABLED'} | Hurdle: ${fmt(by.hurdle_apr,1)}% APR | Max Alloc: ${fmt(by.max_allocation_pct ?? 30,1)}% | Next 8h Epoch: ~${byNext}h`;
  document.getElementById('basis-summary').innerHTML = `
    <span class="badge ok">Total Carry: +$${fmt(by.total_carry_usd)} (${by.payouts} payouts)</span>
    <span class="badge">Top Candidate: <b>${by.top_symbol}</b> (${by.top_apr >= 0 ? '+' : ''}${fmt(by.top_apr)}% APR | 8h FR ${by.funding_rate_8h_pct >= 0 ? '+' : ''}${fmt(by.funding_rate_8h_pct, 4)}%)</span>
    <span class="badge">Epoch Window: ${byElapsed}h / 8.0h</span>
    <span class="badge">Last Payout: ${byLast}</span>
    ${byMeta}`;
  document.getElementById('basis-body').innerHTML = (by.candidates || []).map(c => {
    const cls = c.apr >= (by.hurdle_apr || 5) ? 'ok' : (c.apr > 0 ? 'warn' : 'bad');
    const pill = c.status === 'QUALIFIED' ? '<span class="pill p-green">QUALIFIED</span>' : (c.status === 'LOW YIELD' ? '<span class="pill warn">LOW YIELD</span>' : '<span class="pill p-muted">NEGATIVE</span>');
    return `<tr>
      <td><b>${c.symbol}</b></td>
      <td class="${c.funding_rate_8h_pct>=0?'ok':'bad'}">${c.funding_rate_8h_pct>=0?'+':''}${fmt(c.funding_rate_8h_pct, 4)}%</td>
      <td class="${cls}"><b>${c.apr>=0?'+':''}${fmt(c.apr)}%</b></td>
      <td>${fmt(by.hurdle_apr, 1)}%</td>
      <td>${c.funding_regime}</td>
      <td>${pill}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="6" class="p-muted">No crypto funding rates scanned yet</td></tr>';
  const blPairs = rt.blacklisted_pairs || [];
  document.getElementById('pairs-sub').textContent = `${(d.pairs||[]).length} active pairs | Blacklisted: ${blPairs.length ? blPairs.join(', ') : 'None'}`;
  document.getElementById('pairs-body').innerHTML = (d.pairs || []).map(p => {
    const z = Number(p.z_score || 0);
    const pct = Math.max(4, Math.min(96, 50 + (z / 3.5) * 45));
    const col = Math.abs(z) >= 2.5 ? 'var(--amber)' : 'var(--cyan)';
    const guard = p.cooldown_until ? `<span class="pill warn">Cooldown</span>` : (p.strikes > 0 ? `<span class="pill warn">${p.strikes}/2 strikes</span>` : `<span class="pill p-green">Clear (0/2)</span>`);
    return `<tr>
      <td><b>${p.pair}</b></td><td>${p.correlation >= 0 ? '+':''}${fmt(p.correlation)}</td>
      <td>${fmt(p.half_life, 1)}h</td>
      <td><span class="zbar"><span class="zbar-mid"></span><span class="zbar-dot" style="left:${pct.toFixed(1)}%;background:${col}"></span></span>${z >= 0 ? '+':''}${fmt(z)}</td>
      <td>${guard}</td><td>${p.verdict}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="6" class="p-muted">No pairs configured</td></tr>';
  renderClosed();
  renderStrategyPage();
  fitClosedHeight();
}
function renderStrategyPage(){
  if(!lastData) return;
  const metaMap = lastData.strategies_meta || {};
  const meta = metaMap[currentStrategy] || metaMap.all || {name: currentStrategy, enabled: true, description: '', params: {}};
  document.getElementById('sd-title').textContent = meta.name || currentStrategy;
  document.getElementById('sd-desc').textContent = meta.description || '';
  const sb = (lastData.strategy_breakdown || []).find(s => s.strategy_id === currentStrategy);
  const statHtml = sb ? `<span class="badge">Trades: ${sb.trades} (${sb.wins}W)</span><span class="badge">WR: ${fmt(sb.win_rate_pct,1)}% | PF: ${fmt(sb.profit_factor)}</span><span class="badge ${sb.net_pnl>=0?'ok':'bad'}">Net: $${fmt(sb.net_pnl)}</span>` : '';
  document.getElementById('sd-badges').innerHTML = `<span class="badge ${meta.enabled?'ok':'bad'}">${meta.enabled?'ENABLED':'DISABLED'}</span>${statHtml}`;
  const params = meta.params || {};
  document.getElementById('sd-params').innerHTML = Object.entries(params).map(([k,v]) => `<div class="param-box"><div class="param-k">${k}</div><div class="param-v">${String(v)}</div></div>`).join('');

  const showAll = currentStrategy === 'all';
  document.getElementById('sec-basis').classList.toggle('hidden', !showAll && currentStrategy !== 'basis_yield');
  document.getElementById('sec-pairs').classList.toggle('hidden', !showAll && currentStrategy !== 'stat_arb_pairs');
  const showWl = showAll || Boolean(STRAT_WL_MAP[currentStrategy]);
  document.getElementById('sec-watchlist').classList.toggle('hidden', !showWl);
  const wlFilter = document.getElementById('wl-filter');
  if(!showAll && STRAT_WL_MAP[currentStrategy]){ wlFilter.value = STRAT_WL_MAP[currentStrategy]; renderWatchlist(); }
  else if(showAll && Object.values(STRAT_WL_MAP).includes(wlFilter.value)){ /* keep user choice */ }

  const secFills = document.getElementById('sec-strat-fills');
  secFills.classList.toggle('hidden', showAll);
  if(!showAll){
    const fills = (lastData.recent_trades || []).filter(t => (t.strategy_id || '') === currentStrategy);
    document.getElementById('sf-sub').textContent = `${fills.length} fills`;
    document.getElementById('sf-body').innerHTML = fills.map(t => {
      const ts = t.exit_time ? String(t.exit_time).replace('T',' ').slice(0,16) : '—';
      return `<tr>
        <td class="p-muted">${ts}</td><td><b>${t.symbol}</b></td><td>${t.strategy_id||'—'}</td><td>${t.side}</td>
        <td>$${fmt(t.entry_price,4)}</td><td>$${fmt(t.exit_price,4)}</td><td>$${fmt(t.fees ?? 0)}</td>
        <td class="${t.pnl>=0?'ok':'bad'}">$${fmt(t.pnl)}</td><td>${fmt(t.return_pct)}%</td><td>${t.exit_reason||''}</td>
      </tr>`;
    }).join('') || '<tr><td colspan="10" class="p-muted">No closed fills for this strategy yet</td></tr>';
  }
}
function fitClosedHeight(){
  const cw = document.querySelector('.closed-wrap');
  const ov = document.getElementById('page-overview');
  if(!cw || !ov || ov.classList.contains('hidden')) return;
  if(window.innerWidth <= 1020){ cw.style.maxHeight = '420px'; return; }
  const rect = cw.getBoundingClientRect();
  const avail = Math.floor((window.innerHeight - rect.top - 28) * 0.96);
  cw.style.maxHeight = Math.max(150, avail) + 'px';
}
function focusTradeFromDot(tradeIdx, kind){
  if(kind === 'live'){
    const el = document.getElementById('open-body');
    if(el) el.scrollIntoView({behavior:'smooth', block:'center'});
    return;
  }
  if(!tradeIdx) return;
  selectedTradeIdx = tradeIdx;
  const cfEl = document.getElementById('closed-filter');
  if(cfEl) cfEl.value = '';
  renderClosed();
  const row = document.getElementById('trade-row-' + tradeIdx);
  if(row){
    row.scrollIntoView({behavior:'smooth', block:'center'});
    row.classList.add('row-highlight');
    if(highlightTimer) clearTimeout(highlightTimer);
    highlightTimer = setTimeout(() => {
      const r = document.getElementById('trade-row-' + tradeIdx);
      if(r) r.classList.remove('row-highlight');
    }, 3500);
  }
}
function renderClosed(){
  if(!lastData) return;
  const cf = document.getElementById('closed-filter').value;
  const allRows = lastData.recent_trades || [];
  let rows = allRows.filter(t => !cf || (t.strategy_id || '') === cf);
  if(selectedTradeIdx && !cf && !rows.some(t => t.trade_idx === selectedTradeIdx)){
    const extra = (lastData.closed_trades_history || []).find(t => t.trade_idx === selectedTradeIdx);
    if(extra) rows = [{...extra, _from_chart: true}, ...rows];
  }
  const subEl = document.getElementById('closed-sub');
  if(subEl) subEl.textContent = cf ? `(${rows.length} of ${allRows.length} fills)` : `(${allRows.length} fills)`;
  document.getElementById('closed-body').innerHTML = rows.map(t => {
    const ts = t.exit_time ? String(t.exit_time).replace('T',' ').slice(0,16) : '—';
    const badge = t._from_chart ? ` <span class="pill p-cyan">Chart #${t.trade_idx}</span>` : '';
    const rowCls = (t.trade_idx && t.trade_idx === selectedTradeIdx) ? ' class="row-highlight"' : '';
    return `<tr id="trade-row-${t.trade_idx ?? ''}"${rowCls}>
      <td class="p-muted">${ts}${badge}</td><td><b>${t.symbol}</b></td><td>${t.strategy_id||'—'}</td><td>${t.side}</td>
      <td>$${fmt(t.entry_price,4)}</td><td>$${fmt(t.exit_price,4)}</td><td>$${fmt(t.fees ?? 0)}</td>
      <td class="${t.pnl>=0?'ok':'bad'}">$${fmt(t.pnl)}</td><td>${fmt(t.return_pct)}%</td><td>${t.exit_reason||''}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="10" class="p-muted">No matching closed trades</td></tr>';
}
async function poll(){ try{ const r = await fetch('/api/state'); if(r.ok) render(await r.json()); }catch(e){} }
document.getElementById('wl-search').addEventListener('input', renderWatchlist);
document.getElementById('wl-filter').addEventListener('change', renderWatchlist);
document.getElementById('closed-filter').addEventListener('change', renderClosed);
const STRAT_LABELS = {
  all: 'All Strategies & Watchlist',
  basis_yield: 'Basis Yield Carry',
  stat_arb_pairs: 'Stat-Arb Pairs',
  momentum: 'Momentum',
  mean_reversion: 'Mean Reversion',
  volatility_squeeze: 'Volatility Squeeze',
  trend_pullback: 'Trend Pullback',
  vwap_reversion: 'VWAP Reversion',
  nr7_breakout: 'NR7 Breakout',
  range_envelope: 'Range Envelope',
};
function setPage(page, strat, push){
  const isStrat = page === 'strategies';
  if(isStrat) currentStrategy = strat || 'all';
  document.getElementById('page-overview').classList.toggle('hidden', isStrat);
  document.getElementById('page-strategies').classList.toggle('hidden', !isStrat);
  document.querySelectorAll('.nav-tab').forEach(el => el.classList.toggle('active', el.dataset.page === page));
  document.querySelectorAll('.nav-item').forEach(el => el.classList.toggle('active', isStrat && el.dataset.strat === currentStrategy));
  const btn = document.getElementById('strat-nav-btn');
  if(btn) btn.innerHTML = (isStrat ? (STRAT_LABELS[currentStrategy] || currentStrategy) : 'Strategies &amp; Scanners') + ' &#9662;';
  const targetUrl = !isStrat ? '/' : (currentStrategy === 'all' ? '/strategies' : '/strategies/' + currentStrategy);
  if(push) history.pushState({page, strat: currentStrategy}, '', targetUrl);
  if(isStrat) renderStrategyPage();
  else fitClosedHeight();
}
function navClick(e, page){
  if(e) e.preventDefault();
  document.getElementById('strat-menu').classList.remove('open');
  setPage(page, 'all', true);
  return false;
}
function toggleStratMenu(e){
  if(e) e.preventDefault();
  e.stopPropagation();
  document.getElementById('strat-menu').classList.toggle('open');
  return false;
}
function selectStrategy(e, strat){
  if(e) e.preventDefault();
  document.getElementById('strat-menu').classList.remove('open');
  if(strat === 'all') document.getElementById('wl-filter').value = '';
  setPage('strategies', strat, true);
  return false;
}
document.addEventListener('click', (e) => {
  if(!e.target.closest('.nav-dd')) document.getElementById('strat-menu').classList.remove('open');
});
function syncFromUrl(){
  const p = window.location.pathname;
  if(p === '/strategies') setPage('strategies', 'all', false);
  else if(p.startsWith('/strategies/')) setPage('strategies', p.slice('/strategies/'.length) || 'all', false);
  else setPage('overview', 'all', false);
}
window.addEventListener('resize', fitClosedHeight);
window.addEventListener('popstate', syncFromUrl);
syncFromUrl();
fitClosedHeight();
poll(); setInterval(poll, 3000);
</script>
</body>
</html>"""


class WebDashboardHandler(BaseHTTPRequestHandler):
    """Read-only HTTP request handler for HTML UI, JSON state, and health check."""

    def do_GET(self) -> None:
        req_path = self.path.split("?", 1)[0]
        if req_path == "/health":
            body = json.dumps({"status": "ok"}).encode("utf-8")
            self._send(HTTPStatus.OK, "application/json", body)
        elif req_path == "/api/state":
            body = json.dumps(build_dashboard_payload()).encode("utf-8")
            self._send(HTTPStatus.OK, "application/json", body)
        elif req_path in ("/", "/index.html", "/strategies") or req_path.startswith("/strategies/"):
            self._send(HTTPStatus.OK, "text/html; charset=utf-8", HTML_PAGE.encode("utf-8"))
        else:
            self._send(HTTPStatus.NOT_FOUND, "application/json", b'{"error":"not_found"}')

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Web4 Read-Only Browser Dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port (default: 8080)")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), WebDashboardHandler)
    print(f"[WebDashboard] Read-only UI running at http://{args.host}:{args.port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
