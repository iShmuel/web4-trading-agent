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
from core.data_loader import fetch_ohlcv, check_data_quality
from core.process_lock import SingleInstanceLock, AlreadyRunningError
from agent.optimizer import run_optimization_cycle

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config" / "active_config.json"
DATA_DIR = PROJECT_ROOT / "data"
LOG_FILE = DATA_DIR / "live_trader.log"
LOCK_FILE = DATA_DIR / "live_trader.lock"
STATUS_FILE = DATA_DIR / "live_status.json"


def log(msg: str):
    """Print to console and append to data/live_trader.log."""
    print(msg, flush=True)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
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
    watchlist = config.get("watchlist", ["BTC/USDT", "ETH/USDT", "SOL/USDT", "NVDA", "SPY"])
    max_positions = config.get("max_concurrent_positions", 3)

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    log(f"\n[{now_str}] Polling live watchlist ({len(watchlist)} assets | Max open positions: {max_positions})...")

    market_data: Dict[str, Any] = {}
    current_prices: Dict[str, float] = {}

    for symbol in watchlist:
        try:
            timeframe = coordinator.symbol_config(symbol).get("timeframe", config.get("timeframe", "1h"))
            df = fetch_ohlcv(symbol=symbol, timeframe=timeframe, days=30, force_refresh=True)
            valid, reason = check_data_quality(df, max_stale_minutes=120)
            if not valid:
                log(f"   ⚠️ Data quality warning on {symbol}: {reason}")
                continue
            market_data[symbol] = df
            current_prices[symbol] = float(df.iloc[-1]["close"])
        except Exception as e:
            log(f"   ⚠️ Warning fetching {symbol}: {e}")

    # Run cycle through the authoritative Coordinator
    cycle_report = coordinator.run_cycle(market_data)

    for d in cycle_report.decisions:
        sym = d.symbol
        price = d.price
        if d.action in ("ENTER_LONG", "ENTER_SHORT"):
            side_str = "BUY (LONG)" if d.action == "ENTER_LONG" else "SELL SHORT"
            log(f"\n🚀 [PAPER ALERT] {side_str} ORDER EXECUTED ON {sym} via {d.strategy_id}!")
            log(f"   Units: {d.units:.5f} @ ${price:,.2f} | Stop: ${d.stop_loss:,.2f} | Reason: {d.reason}")
        elif d.action == "PARTIAL_EXIT":
            log(f"\n🎯 [PAPER ALERT] PARTIAL TAKE-PROFIT (50% BANKED) ON {sym} via {d.strategy_id}!")
            log(f"   Scaled out {d.units:.5f} units @ ${price:,.2f} | Reason: {d.reason}")
        elif d.action == "EXIT":
            log(f"\n🚨 [PAPER ALERT] EXIT EXECUTED ON {sym} via {d.strategy_id}!")
            log(f"   Closed {d.units:.5f} units @ ${price:,.2f} | Reason: {d.reason}")
        elif d.action == "HOLD":
            log(f"   HOLDING {sym:<8} Price: ${price:>9.2f} | Stop: ${d.stop_loss:>9.2f} ({d.strategy_id})")
        elif d.action == "CONFLICT":
            log(f"   ⚡ CONFLICT RESOLVED: {sym:<8} Price: ${price:>9.2f} | {d.reason}")
        else:
            log(f"   SCANNING {sym:<8} Price: ${price:>9.2f} | {d.reason}")

    log(f"   Portfolio Equity: ${cycle_report.portfolio_equity:,.2f} | Cash: ${cycle_report.available_cash:,.2f} | Open: {cycle_report.open_positions}")
    write_live_status(account, cycle_report, current_prices, watchlist)
    return current_prices


def write_live_status(account: PaperAccount, cycle_report, current_prices: Dict[str, float], watchlist: List[str]):
    """Write a read-only snapshot of the latest tick for the dashboard.

    This is a separate file from the portfolio; it is never read back by the bot.
    """
    try:
        assets = {}
        for d in cycle_report.decisions:
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
            "prices": current_prices,
            "assets": assets,
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
    parser.add_argument("--reset", action="store_true", help="Reset portfolio to $10,000 initial cash")
    parser.add_argument("--interval", type=int, default=60, help="Loop interval in seconds (default: 60s)")
    parser.add_argument("--optimize-hours", type=int, default=6, help="Self-improvement tournament interval in hours (default: 6)")
    parser.add_argument("--loop", action="store_true", help="Run continuous monitoring loop")
    args = parser.parse_args()

    # --status is read-only and may run alongside the bot.
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
        for sym in watchlist:
            try:
                df = fetch_ohlcv(symbol=sym, timeframe=config.get("timeframe", "1h"), days=2, force_refresh=True)
                prices[sym] = float(df.iloc[-1]["close"])
            except Exception:
                pass
        print_status(account, prices)
        return

    risk_engine = RiskEngine(
        default_risk_pct=config.get("account_risk_pct", 0.02),
        max_open_positions=config.get("max_concurrent_positions", 3),
        max_strategy_allocation_pct=config.get("max_strategy_allocation_pct", 0.75),
    )
    coordinator = PortfolioCoordinator(account, risk_engine, config)

    log("=" * 68)
    log("       WEB4 MULTI-STRATEGY LIVE PAPER TRADING ENGINE STARTED")
    log("=" * 68)
    log(f"Execution Mode:     PAPER TRADING ONLY (Zero Real Capital Risk)")
    log(f"Strategies:         Momentum / Trend Following + Mean Reversion")
    log(f"Watchlist:          {', '.join(watchlist)}")
    for sym in watchlist:
        sc = coordinator.symbol_config(sym)
        log(f"   {sym:<9} profile={sc.get('profile_name')} tf={sc.get('timeframe')} breakout={sc.get('breakout_lookback')} "
            f"ema={sc.get('ema_trend_filter')} atr_x={sc.get('atr_multiplier')} mom_tp={sc.get('take_profit_ratio', 0)*100:.0f}%@{sc.get('take_profit_atr_mult', 0)}ATR")
    log(f"Max Concurrent:     {config.get('max_concurrent_positions', 3)} positions")
    log(f"Risk per Position:  {config.get('account_risk_pct', 0.02)*100:.1f}% of portfolio")
    log(f"Auto-Optimize:      Every {args.optimize_hours} hours")
    log(f"Log File:           {LOG_FILE}")
    log("=" * 68)

    if args.loop:
        log(f"\n[Engine] Starting continuous portfolio monitoring loop (Checking every {args.interval}s)...")
        last_opt_time = time.time()

        try:
            while True:
                run_portfolio_tick(account, risk_engine, coordinator)

                # Periodic self-improvement check
                elapsed = time.time() - last_opt_time
                if elapsed >= (args.optimize_hours * 3600):
                    log("\n" + "=" * 68)
                    log("   ⏰ PERIODIC SELF-IMPROVEMENT TOURNAMENT TRIGGERED")
                    log("=" * 68)
                    try:
                        res = run_optimization_cycle(auto_commit=True)
                        log(f"[Auto-Optimize Result] Status: {res.get('status')}")
                    except Exception as e:
                        log(f"[Auto-Optimize Error] {e}")
                    last_opt_time = time.time()

                time.sleep(args.interval)
        except KeyboardInterrupt:
            log("\n[Engine] Multi-asset trading paused by user.")
    else:
        run_portfolio_tick(account, risk_engine, coordinator)
        log("\n[Tip] To run continuously in the background, use: python live_paper_trader.py --loop")


if __name__ == "__main__":
    main()
