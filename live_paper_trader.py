"""Live Multi-Asset Paper Trading Runner with Multi-Strategy Coordination.
Monitors a basket of Crypto & Stocks simultaneously, coordinates independent
Momentum and Mean Reversion strategies, enforces centralized risk limits,
and executes paper orders with zero real capital risk.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine
from core.coordinator import PortfolioCoordinator
from core.data_loader import fetch_ohlcv, fetch_ohlcv_batch, check_data_quality, is_crypto_symbol
from core.process_lock import SingleInstanceLock, AlreadyRunningError
from core.async_agent_runner import AsyncAgentTaskRunner
from core.cpi_benchmark import run_cpi_benchmark

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config" / "active_config.json"
DATA_DIR = PROJECT_ROOT / "data"
LOG_FILE = DATA_DIR / "live_trader.log"
MAX_LOG_BYTES = 10 * 1024 * 1024  # 10 MB automatic log rotation threshold
LOCK_FILE = DATA_DIR / "live_trader.lock"
STATUS_FILE = DATA_DIR / "live_status.json"


def log(msg: str):
    """Print to console and append to data/live_trader.log with 10 MB rotation."""
    print(msg, flush=True)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size >= MAX_LOG_BYTES:
            LOG_FILE.replace(LOG_FILE.with_suffix(".log.1"))
        with open(LOG_FILE, "a") as f:
            f.write(f"{msg}\n")
    except Exception:
        pass


def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def print_status(account: PaperAccount, current_prices: Dict[str, float] = None):
    if current_prices is None:
        current_prices = {}

    log("=" * 68)
    log("          MULTI-ASSET LIVE PAPER TRADING PORTFOLIO")
    log("=" * 68)
    log(f"Execution Mode:      PAPER TRADING (Simulated Funds)")
    log(f"Initial Capital:     ${account.initial_capital:,.2f}")
    log(f"Available Cash:      ${account.cash:,.2f}")

    total_equity = account.get_total_equity(current_prices)
    unrealized_total = 0.0

    open_pos = account.positions
    log(f"Active Positions:    {len(open_pos)} open")

    if open_pos:
        log("\n" + "-" * 68)
        log(f"{'SYMBOL':<10} {'STRATEGY':<12} {'SIDE':<6} {'UNITS':<10} {'ENTRY':<10} {'CURRENT':<10} {'UNREALIZED PNL':<16}")
        log("-" * 68)
        for sym, pos in open_pos.items():
            cur_price = current_prices.get(sym, pos["entry_price"])
            side = pos.get("side", "LONG")
            strat = pos.get("strategy_id", "momentum")
            if side == "SHORT":
                pnl = (pos["entry_price"] - cur_price) * pos["units"]
                pnl_pct = (pos["entry_price"] - cur_price) / pos["entry_price"] * 100.0
            else:
                pnl = (cur_price - pos["entry_price"]) * pos["units"]
                pnl_pct = (cur_price - pos["entry_price"]) / pos["entry_price"] * 100.0
            unrealized_total += pnl

            log(
                f"{sym:<10} {strat:<12} {side:<6} {pos['units']:<10.4f} ${pos['entry_price']:<9.2f} ${cur_price:<9.2f} "
                f"${pnl:+,.2f} ({pnl_pct:+.2f}%)"
            )
        log("-" * 68)

    log(f"Total Portfolio Value: ${total_equity:,.2f} (Unrealized: ${unrealized_total:+,.2f})")

    trades = account.state.get("closed_trades", [])
    log(f"\nClosed Trades:       {len(trades)}")
    if trades:
        wins = sum(1 for t in trades if t["pnl"] > 0)
        losses = sum(1 for t in trades if t["pnl"] <= 0)
        win_rate = (wins / len(trades)) * 100.0
        total_pnl = sum(t["pnl"] for t in trades)
        log(f"Win Rate:            {win_rate:.1f}% ({wins}W / {losses}L)")
        log(f"Realized PnL:        ${total_pnl:+,.2f}")
    log("=" * 68)


def run_portfolio_tick(
    account: PaperAccount,
    risk_engine: RiskEngine,
    coordinator: PortfolioCoordinator,
) -> Dict[str, float]:
    """Scan all assets in the watchlist using the PortfolioCoordinator."""
    config = load_config()
    if config != coordinator.config:
        coordinator.reload_config(config)
    watchlist = config.get("watchlist", ["BTC/USDT", "ETH/USDT", "SOL/USDT", "NVDA", "SPY"])
    pairs_universe = config.get("pairs_universe", [])
    pair_symbols = [s for p in pairs_universe if len(p) == 2 for s in p]
    open_pair_symbols = [
        s for pos in account.positions.values() if pos.get("pair_id") for s in str(pos["pair_id"]).split(":")
    ]
    poll_symbols = list(dict.fromkeys(watchlist + pair_symbols + open_pair_symbols))
    max_positions = config.get("max_concurrent_positions", 3)

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    log(f"\n[{now_str}] Polling live watchlist ({len(watchlist)} assets + {len(pairs_universe)} pairs | Max open positions: {max_positions})...")

    market_data: Dict[str, Any] = {}
    current_prices: Dict[str, float] = {}

    is_weekend = datetime.now(timezone.utc).weekday() >= 5
    symbol_params = {}
    for symbol in poll_symbols:
        timeframe = coordinator.symbol_config(symbol).get("timeframe", config.get("timeframe", "1h"))
        fetch_days = 30 if is_crypto_symbol(symbol) else 90
        symbol_params[symbol] = {
            "timeframe": timeframe,
            "days": fetch_days,
            "force_refresh": is_crypto_symbol(symbol) or (not is_weekend),
            "closed_only": True,
        }

    raw_data = fetch_ohlcv_batch(poll_symbols, symbol_params=symbol_params)
    for symbol in poll_symbols:
        df = raw_data.get(symbol)
        if df is None or df.empty:
            log(f"   ⚠️ Warning: failed to fetch market data for {symbol}")
            continue
        try:
            # Stocks close overnight/weekends, so allow 0 (skip age check) for equities; allow 180m for 1h crypto closed bars
            stale_limit = 180 if is_crypto_symbol(symbol) else 0
            valid, reason = check_data_quality(df, max_stale_minutes=stale_limit)
            if not valid:
                log(f"   ⚠️ Data quality warning on {symbol}: {reason}")
                continue
            df.attrs["quote_time"] = datetime.now(timezone.utc)
            market_data[symbol] = df
            current_prices[symbol] = float(df.attrs.get("live_price", df.iloc[-1]["close"]))
        except Exception as e:
            log(f"   ⚠️ Warning validating {symbol}: {e}")

    # Run cycle through the authoritative Coordinator
    cycle_report = coordinator.run_cycle(market_data)

    # Settle past decisions once their forward window has traded
    try:
        from core.journal import settle_journal
        settled_cnt, _ = settle_journal(market_data, holding_bars=24)
        if settled_cnt > 0:
            log(f"   [Journal] Settled {settled_cnt} past decisions against forward alpha benchmark")
    except Exception as e:
        pass

    for d in cycle_report.decisions:
        sym = d.symbol
        price = d.price
        regime = getattr(d, "indicators", {}).get("regime", "")
        regime_badge = f"[{regime}] " if regime else ""
        if d.action in ("ENTER_LONG", "ENTER_SHORT"):
            side_str = "BUY (LONG)" if d.action == "ENTER_LONG" else "SELL SHORT"
            log(f"\n🚀 [PAPER ALERT] {side_str} ORDER EXECUTED ON {sym} via {d.strategy_id}!")
            log(f"   Units: {d.units:.5f} @ ${price:,.2f} | Stop: ${d.stop_loss:,.2f} | Reason: {d.reason}")
        elif d.action in ("LONG_A_SHORT_B", "SHORT_A_LONG_B"):
            log(f"\n⚖️ [PAIR TRADE ALERT] DUAL-LEG ENTRY EXECUTED ON {sym} via {d.strategy_id}!")
            log(f"   Action: {d.action} | Reason: {d.reason}")
        elif d.action == "CLOSE_PAIR":
            log(f"\n⚖️ [PAIR TRADE ALERT] DUAL-LEG EXIT EXECUTED ON {sym} via {d.strategy_id}!")
            log(f"   Reason: {d.reason}")
        elif d.action == "ACCRUED_YIELD":
            log(f"\n🌾 [YIELD HARVEST ALERT] DELTA-NEUTRAL FUNDING CASH ACCRUED ON {sym}!")
            log(f"   Reason: {d.reason}")
        elif d.action == "PARTIAL_EXIT":
            log(f"\n🎯 [PAPER ALERT] PARTIAL TAKE-PROFIT (50% BANKED) ON {sym} via {d.strategy_id}!")
            log(f"   Scaled out {d.units:.5f} units @ ${price:,.2f} | Reason: {d.reason}")
        elif d.action == "EXIT":
            log(f"\n🚨 [PAPER ALERT] EXIT EXECUTED ON {sym} via {d.strategy_id}!")
            log(f"   Closed {d.units:.5f} units @ ${price:,.2f} | Reason: {d.reason}")
        elif d.action == "HOLD":
            log(f"   HOLDING {sym:<8} Price: ${price:>9.2f} | Stop: ${d.stop_loss:>9.2f} ({d.strategy_id}) {regime_badge}")
        elif d.action == "CONFLICT":
            log(f"   ⚡ CONFLICT RESOLVED: {sym:<8} Price: ${price:>9.2f} | {d.reason}")
        else:
            log(f"   SCANNING {sym:<8} Price: ${price:>9.2f} | {regime_badge}{d.reason}")

    if getattr(cycle_report, "macro_regime", None):
        ret_str = (
            f"{cycle_report.macro_return_pct:+.2f}%"
            if cycle_report.macro_return_pct is not None
            else "n/a"
        )
        log(f"   Macro Regime Playbook B: {cycle_report.macro_regime} (20d 50/50 BNB+AAPL = {ret_str})")
    log(f"   Portfolio Equity: ${cycle_report.portfolio_equity:,.2f} | Cash: ${cycle_report.available_cash:,.2f} | Open: {cycle_report.open_positions}")
    write_live_status(account, cycle_report, current_prices, watchlist)
    return current_prices


def write_live_status(account: PaperAccount, cycle_report, current_prices: Dict[str, float], watchlist: List[str]):
    """Write a read-only snapshot of the latest tick for the dashboard.

    This is a separate file from the portfolio; it is never read back by the bot.
    """
    try:
        assets = {}
        basis_opportunity = None
        for d in cycle_report.decisions:
            if d.strategy_id == "basis_yield":
                basis_opportunity = {
                    "symbol": d.symbol,
                    "action": d.action,
                    "apr": d.price,
                    "reason": d.reason,
                    "indicators": getattr(d, "indicators", {}) or {},
                }
                continue
            assets[d.symbol] = {
                "action": d.action,
                "strategy_id": d.strategy_id,
                "price": d.price,
                "stop_loss": d.stop_loss,
                "take_profit": d.take_profit,
                "reason": d.reason,
                "indicators": getattr(d, "indicators", {}) or {},
            }
        for sym in watchlist:
            assets.setdefault(sym, {"action": "NO_DATA", "price": current_prices.get(sym), "reason": "Data unavailable or stale"})

        snapshot = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "equity": cycle_report.portfolio_equity,
            "cash": cycle_report.available_cash,
            "open_positions": cycle_report.open_positions,
            "macro_regime": getattr(cycle_report, "macro_regime", None),
            "macro_return_pct": getattr(cycle_report, "macro_return_pct", None),
            "prices": current_prices,
            "assets": assets,
            "basis_yield": basis_opportunity,
        }
        tmp = STATUS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(snapshot, indent=2, default=str))
        os.replace(tmp, STATUS_FILE)
    except Exception as e:
        log(f"   ⚠️ Could not write live status snapshot: {e}")


def main():
    parser = argparse.ArgumentParser(description="Multi-Asset Live Paper Trading Runner")
    parser.add_argument("--once", action="store_true", help="Run a single live portfolio scan across all assets")
    parser.add_argument("--status", action="store_true", help="Display current multi-asset portfolio status")
    parser.add_argument("--journal", action="store_true", help="Display audit report of settled decision journal and gate alpha")
    parser.add_argument("--cpi-benchmark", action="store_true", help="Run the 3-tier Automated CPI Benchmark Harness (Item 1)")
    parser.add_argument("--reset", action="store_true", help="Reset portfolio to $10,000 initial cash")
    parser.add_argument("--interval", type=int, default=60, help="Loop interval in seconds (default: 60s)")
    parser.add_argument("--optimize-hours", type=int, default=6, help="Self-improvement tournament interval in hours (default: 6)")
    parser.add_argument("--scan-hours", type=int, default=24, help="Dynamic market universe discovery interval in hours (default: 24)")
    parser.add_argument("--auto-approve", action="store_true", help="Enable 4-gate conditional auto-approval for self-improvement and discovery")
    parser.add_argument("--loop", action="store_true", help="Run continuous monitoring loop")
    args = parser.parse_args()

    # --status, --journal, and --cpi-benchmark are read-only and may run alongside the bot.
    if args.cpi_benchmark:
        rep = run_cpi_benchmark()
        print("=" * 88)
        print("       AUTOMATED CENTRAL PERFORMANCE INDEX (CPI) BENCHMARK HARNESS")
        print("=" * 88)
        for t_data in rep["tiers"].values():
            print(
                f"{t_data['label']:40s} | "
                f"AvgRet: {t_data['avg_return_pct']:+6.2f}% | "
                f"MaxDD: {t_data['avg_max_drawdown_pct']:5.2f}% | "
                f"Sharpe: {t_data['avg_sharpe_ratio']:+5.2f} | "
                f"PF: {t_data['avg_profit_factor']:4.2f} | "
                f"CPI: {t_data['cpi_score']:5.1f}/100"
            )
        print("=" * 88)
        return

    if args.journal:
        from core.journal import generate_journal_report
        print(generate_journal_report())
        return

    if args.status:
        _run(args)
        return

    # Every other mode writes the portfolio file, so only one process may run.
    # The lock is taken BEFORE the portfolio is loaded, so we never start from
    # a stale in-memory copy of another process's state.
    try:
        with SingleInstanceLock(LOCK_FILE):
            _run(args)
    except AlreadyRunningError as e:
        print(f"\n❌ {e}", file=sys.stderr)
        print("   Only one bot may write data/paper_portfolio.json at a time.", file=sys.stderr)
        print("   Watch it live with:  python dashboard.py", file=sys.stderr)
        sys.exit(1)


def _run(args):
    account = PaperAccount(initial_capital=10000.0)

    if args.reset:
        account.reset(10000.0)
        return

    config = load_config()
    watchlist = config.get("watchlist", ["BTC/USDT", "ETH/USDT", "SOL/USDT", "NVDA", "SPY"])

    if args.status:
        prices = {}
        tf = config.get("timeframe", "1h")
        sym_params = {
            sym: {
                "timeframe": tf,
                "days": 2 if is_crypto_symbol(sym) else 7,
                "force_refresh": True,
                "closed_only": True,
            }
            for sym in watchlist
        }
        raw_data = fetch_ohlcv_batch(watchlist, symbol_params=sym_params)
        for sym, df in raw_data.items():
            if df is not None and not df.empty:
                prices[sym] = float(df.iloc[-1]["close"])
        print_status(account, prices)
        return

    risk_engine = RiskEngine(
        default_risk_pct=config.get("account_risk_pct", 0.02),
        max_open_positions=config.get("max_concurrent_positions", 3),
        max_strategy_allocation_pct=config.get("max_strategy_allocation_pct", 0.75),
        max_crypto_positions=config.get("max_crypto_positions", 2),
        dynamic_scaling=config.get("dynamic_scaling", True),
        symbol_risk_weights=config.get("symbol_risk_weights"),
        symbol_exposure_caps=config.get("symbol_exposure_caps"),
    )
    coordinator = PortfolioCoordinator(account, risk_engine, config)

    log("=" * 68)
    log("       WEB4 MULTI-STRATEGY LIVE PAPER TRADING ENGINE STARTED")
    log("=" * 68)
    log(f"Execution Mode:     PAPER TRADING ONLY (Zero Real Capital Risk)")
    log(f"Strategies:         Momentum / Trend Following + Mean Reversion + Stat-Arb Pairs + Basis Yield")
    log(f"Watchlist:          {', '.join(watchlist)}")
    for sym in watchlist:
        sc = coordinator.symbol_config(sym)
        log(f"   {sym:<9} profile={sc.get('profile_name')} tf={sc.get('timeframe')} breakout={sc.get('breakout_lookback')} "
            f"ema={sc.get('ema_trend_filter')} atr_x={sc.get('atr_multiplier')} mom_tp={sc.get('take_profit_ratio', 0)*100:.0f}%@{sc.get('take_profit_atr_mult', 0)}ATR")
    log(f"Max Concurrent:     {config.get('max_concurrent_positions', 3)} positions")
    log(f"Risk per Position:  {config.get('account_risk_pct', 0.02)*100:.1f}% of portfolio")
    log(f"Auto-Optimize:      Every {args.optimize_hours} hours")
    auto_appr = bool(getattr(args, "auto_approve", False) or config.get("enable_conditional_auto_approval", False))
    log(f"Auto-Approval:      {'ENABLED (4-Gate Verification)' if auto_appr else 'DISABLED (Requires Manual Review)'}")
    log(f"Log File:           {LOG_FILE}")
    log("=" * 68)

    if args.loop:
        log(f"\n[Engine] Starting continuous portfolio monitoring loop (Checking every {args.interval}s)...")
        async_runner = AsyncAgentTaskRunner(max_workers=3)
        last_opt_time = time.time()
        last_scan_time = time.time()
        last_forensic_time = time.time()
        last_analyzed_trade_count = len(account.state.get("closed_trades", []))
        forensic_interval = int(config.get("forensic_trade_interval", 15))
        forensic_max_hours = float(config.get("forensic_max_hours", 12.0))

        def _bg_forensic_task(tw: int):
            from agent.trade_analyst import run_trade_forensic_analysis
            return run_trade_forensic_analysis(
                trade_window=tw,
                use_llm=True,
                auto_submit_to_governor=True,
            )

        def _bg_optimizer_task():
            from agent.multi_strategy_optimizer import run_multi_strategy_optimization
            return run_multi_strategy_optimization(auto_commit=True)

        def _bg_discovery_task():
            from agent.asset_selector import (
                scan_candidate_universe,
                build_selection_summary,
                audit_and_rehabilitate_blacklist,
            )
            from agent.governor import review_and_deploy_proposal
            from strategies.stat_arb_pairs import discover_cointegrated_pairs

            evals = scan_candidate_universe(
                days=90,
                dynamic_discovery=True,
                merge_existing=True,
                top_crypto_discovery=15,
                top_stock_discovery=15,
                use_llm=True,
            )
            summary = build_selection_summary(evals, max_crypto=8, max_stocks=16)
            rehab_report = audit_and_rehabilitate_blacklist(days=60, use_llm=True)
            rehab_syms = rehab_report.get("rehabilitated_symbols", [])
            if summary.get("recommended_watchlist") or rehab_syms:
                merged_wl = list(
                    dict.fromkeys(list(summary.get("recommended_watchlist", [])) + list(rehab_syms))
                )
                dyn_pairs = discover_cointegrated_pairs(merged_wl, load_config())
                prop = {
                    "watchlist": merged_wl,
                    "blacklisted_symbols": [
                        s for s in summary.get("recommended_blacklist", []) if s not in set(rehab_syms)
                    ],
                    "rehabilitated_symbols": rehab_syms,
                }
                if dyn_pairs:
                    prop["pairs_universe"] = dyn_pairs
                res = review_and_deploy_proposal(
                    proposed_changes=prop,
                    rationale="Periodic market discovery scan merged active portfolio with vetted discoveries, audited blacklist for parole, and pruned non-profitable drags.",
                    empirical_proof={
                        "scanned": summary["total_assets_scanned"],
                        "proven_profitable": summary.get("proven_profitable_count", 0),
                        "pruned_drags": summary.get("proven_non_profitable_count", 0),
                        "rehabilitated_count": len(rehab_syms),
                        "rehabilitated_symbols": rehab_syms,
                        "elite_assets": summary["tier_counts"]["ELITE_ALPHA"],
                        "viable_assets": summary["tier_counts"]["VIABLE_ALPHA"],
                    },
                    verify_tests=True,
                    use_llm=True,
                )
                return {
                    "approved": res.approved,
                    "merged_wl": merged_wl,
                    "rehab_syms": rehab_syms,
                    "blacklisted": prop["blacklisted_symbols"],
                }
            return {"approved": False}

        try:
            while True:
                run_portfolio_tick(account, risk_engine, coordinator)

                # Item 4: Harvest completed non-blocking background agent tasks (< 0.001s)
                for done_task in async_runner.harvest_completed():
                    if not done_task.success:
                        log(f"[AsyncAgent:{done_task.task_key}] Error ({done_task.duration_sec:.2f}s): {done_task.error}")
                        continue
                    if done_task.task_key == "trade_analyst_forensics":
                        summary = done_task.result
                        coordinator.reload_config(load_config())
                        log(
                            f"[Trade Forensic Audit] Completed in {done_task.duration_sec:.1f}s (non-blocking) | "
                            f"WR: {summary.win_rate_pct}% | PnL: ${summary.total_realized_pnl:+,.2f} | PF: {summary.profit_factor:.2f}"
                        )
                    elif done_task.task_key == "multi_strategy_optimizer":
                        res = done_task.result or {}
                        for r in res.get("results", []):
                            detail = r.get("reason") or r.get("candidate") or ""
                            log(f"[Auto-Optimize] {r.get('task')}: {str(r.get('status')).upper()} {detail}")
                        if res.get("status") == "deployed":
                            coordinator.reload_config(load_config())
                        log(f"[Auto-Optimize Result] Status: {res.get('status')} ({done_task.duration_sec:.1f}s non-blocking)")
                    elif done_task.task_key == "asset_selector_discovery":
                        res = done_task.result or {}
                        if res.get("approved"):
                            coordinator.reload_config(load_config())
                            log(
                                f"[Market Discovery] Approved & deployed {len(res.get('merged_wl', []))} proven assets "
                                f"({done_task.duration_sec:.1f}s non-blocking | Rehabilitated: {len(res.get('rehab_syms', []))}, "
                                f"Pruned: {len(res.get('blacklisted', []))})."
                            )

                # Event-driven + periodic post-trade forensic audit trigger (every X closed trades, or every 12h if new trades closed)
                cur_closed_list = account.state.get("closed_trades", [])
                cur_closed = len(cur_closed_list)
                if cur_closed > last_analyzed_trade_count:
                    try:
                        from agent.governor import check_and_rollback_if_degraded
                        rb_res = check_and_rollback_if_degraded(cur_closed_list)
                        if rb_res.get("rolled_back"):
                            coordinator.reload_config(load_config())
                            last_analyzed_trade_count = cur_closed
                            log(f"⚠️ [AUTO-ROLLBACK SAFETY NET] {rb_res.get('reason')}")
                    except Exception as e:
                        log(f"[Auto-Rollback Notice] {e}")
                new_trades = cur_closed - last_analyzed_trade_count
                forensic_due = new_trades >= forensic_interval or (
                    new_trades > 0 and (time.time() - last_forensic_time) >= (forensic_max_hours * 3600)
                )
                if forensic_due and cur_closed > 0 and not async_runner.is_running("trade_analyst_forensics"):
                    log("\n" + "=" * 68)
                    log(f"   🔍 AGENT 10: ASYNC POST-TRADE FORENSIC AUDIT DISPATCHED ({cur_closed} trades, +{new_trades} new)")
                    log("=" * 68)
                    async_runner.submit_task(
                        "trade_analyst_forensics",
                        _bg_forensic_task,
                        max(forensic_interval, new_trades),
                    )
                    last_analyzed_trade_count = cur_closed
                    last_forensic_time = time.time()

                # Periodic self-improvement tournament (Agent 11)
                elapsed = time.time() - last_opt_time
                if elapsed >= (args.optimize_hours * 3600) and not async_runner.is_running("multi_strategy_optimizer"):
                    log("\n" + "=" * 68)
                    log("   ⏰ ASYNC MULTI-STRATEGY SELF-IMPROVEMENT TOURNAMENT DISPATCHED (Agent 11)")
                    log("=" * 68)
                    async_runner.submit_task("multi_strategy_optimizer", _bg_optimizer_task)
                    last_opt_time = time.time()

                # Periodic dynamic market universe discovery (Agent 9 & 12)
                scan_elapsed = time.time() - last_scan_time
                if scan_elapsed >= (args.scan_hours * 3600) and not async_runner.is_running("asset_selector_discovery"):
                    log("\n" + "=" * 68)
                    log("   🌐 ASYNC DYNAMIC MARKET DISCOVERY DISPATCHED (Agent 9)")
                    log("=" * 68)
                    async_runner.submit_task("asset_selector_discovery", _bg_discovery_task)
                    last_scan_time = time.time()

                time.sleep(args.interval)
        except KeyboardInterrupt:
            async_runner.shutdown(wait=False)
            log("\n[Engine] Multi-asset trading paused by user.")
    else:
        run_portfolio_tick(account, risk_engine, coordinator)
        log("\n[Tip] To run continuously in the background, use: python live_paper_trader.py --loop")


if __name__ == "__main__":
    main()
