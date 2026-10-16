"""Live Paper Trading Runner.
Executes live market scans, monitors open positions, and manages paper orders
in real time using simulated capital.
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine
from core.strategy import compute_indicators
from core.data_loader import fetch_ohlcv

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config" / "active_config.json"


def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def print_status(account: PaperAccount, current_price: float = None):
    print("=" * 65)
    print("             LIVE PAPER TRADING PORTFOLIO STATUS")
    print("=" * 65)
    print(f"Initial Capital:   ${account.initial_capital:,.2f}")
    print(f"Available Cash:    ${account.cash:,.2f}")

    pos = account.position
    if pos:
        units = pos["units"]
        entry_price = pos["entry_price"]
        cur_price = current_price if current_price else entry_price
        pos_value = units * cur_price
        total_equity = account.cash + pos_value
        unrealized_pnl = (cur_price - entry_price) * units
        unrealized_pct = (cur_price - entry_price) / entry_price * 100.0

        print(f"Total Equity:      ${total_equity:,.2f} (Unrealized PnL: {unrealized_pnl:+,.2f} / {unrealized_pct:+.2f}%)")
        print("\n[ACTIVE POSITION]")
        print(f"  Asset:           {pos['symbol']}")
        print(f"  Entry Time:      {pos['entry_time'][:19]}")
        print(f"  Entry Price:     ${entry_price:,.2f}")
        print(f"  Current Price:   ${cur_price:,.2f}")
        print(f"  Position Size:   {units:.5f} units (${pos_value:,.2f})")
        print(f"  Dollar Risk:     ${pos['dollar_risk']:,.2f}")
        print(f"  Trailing Stop:   ${pos['trailing_stop']:,.2f} ({((pos['trailing_stop'] - cur_price)/cur_price)*100:+.2f}%)")
    else:
        print(f"Total Equity:      ${account.cash:,.2f}")
        print("\n[ACTIVE POSITION] None (100% in cash, scanning for breakout)")

    trades = account.state.get("closed_trades", [])
    print(f"\nClosed Trades:     {len(trades)}")
    if trades:
        wins = sum(1 for t in trades if t["pnl"] > 0)
        losses = sum(1 for t in trades if t["pnl"] <= 0)
        win_rate = (wins / len(trades)) * 100.0
        total_pnl = sum(t["pnl"] for t in trades)
        print(f"Win Rate:          {win_rate:.1f}% ({wins}W / {losses}L)")
        print(f"Net Realized PnL:  ${total_pnl:+,.2f}")
        print("\nLast Completed Trade:")
        last = trades[-1]
        print(
            f"  {last['symbol']} [{last['entry_time'][:16]} -> {last['exit_time'][:16]}] "
            f"PnL: ${last['pnl']:+,.2f} ({last['return_pct']:+.2f}%) | Reason: {last['exit_reason']}"
        )
    print("=" * 65)


def run_tick(account: PaperAccount, config: dict):
    symbol = config["symbol"]
    timeframe = config["timeframe"]
    risk_pct = config["account_risk_pct"]
    atr_mult = config["atr_multiplier"]
    fee_pct = config.get("fee_pct", 0.0006)
    slippage_pct = config.get("slippage_pct", 0.0004)

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n[{now_str}] Polling live market for {symbol} ({timeframe})...")

    # Fetch ~30 days of data with force_refresh to capture the newest live bar
    df = fetch_ohlcv(symbol=symbol, timeframe=timeframe, days=30, force_refresh=True)

    # Compute indicators
    data = compute_indicators(
        df,
        breakout_lookback=config["breakout_lookback"],
        ema_trend_filter=config["ema_trend_filter"],
        atr_period=config["atr_period"],
    )

    latest_bar = data.iloc[-1]
    prev_bar = data.iloc[-2]  # Most recently closed candle
    cur_price = float(latest_bar["close"])
    cur_atr = float(latest_bar["atr"])
    cur_ema = float(latest_bar["ema_filter"])
    breakout_high = float(latest_bar["breakout_high"])
    in_uptrend = cur_price > cur_ema

    risk_engine = RiskEngine(default_risk_pct=risk_pct)
    pos = account.position

    # --- SCENARIO 1: We hold an active position ---
    if pos:
        trailing_stop = float(pos["trailing_stop"])
        # Check if current price breached trailing stop
        if latest_bar["low"] <= trailing_stop or cur_price <= trailing_stop:
            exec_exit = min(cur_price, trailing_stop) * (1.0 - slippage_pct)
            print("\n🚨 [PAPER ALERT] TRAILING STOP TRIGGERED!")
            trade = account.close_position(
                exit_price=exec_exit,
                exit_reason="ATR Trailing Stop Hit",
                fee_pct=fee_pct,
            )
            print(
                f"   Sold {trade['units']:.5f} {symbol} at ${trade['exit_price']:,.2f}\n"
                f"   Net PnL: ${trade['pnl']:+,.2f} ({trade['return_pct']:+.2f}%)\n"
                f"   Cash Balance: ${account.cash:,.2f}"
            )
        else:
            # Ratchet trailing stop upward
            updated_stop = risk_engine.update_trailing_stop(
                current_price=cur_price,
                current_atr=cur_atr,
                atr_multiplier=atr_mult,
                existing_stop=trailing_stop,
            )
            if updated_stop > trailing_stop:
                account.update_trailing_stop(updated_stop, cur_price)
                print(f"   📈 Trailing stop ratcheted up: ${trailing_stop:,.2f} -> ${updated_stop:,.2f}")

            unrealized = (cur_price - pos["entry_price"]) * pos["units"]
            print(
                f"   Hold: {symbol} | Price: ${cur_price:,.2f} | Stop: ${pos['trailing_stop']:,.2f} | "
                f"Unrealized PnL: ${unrealized:+,.2f} ({(cur_price/pos['entry_price'] - 1)*100:+.2f}%)"
            )

    # --- SCENARIO 2: No active position (Scanning for breakout) ---
    else:
        # Check entry trigger on the recently closed candle or live breakout
        entry_triggered = in_uptrend and (cur_price > breakout_high)

        print(
            f"   Market: ${cur_price:,.2f} | Breakout Line: ${breakout_high:,.2f} | "
            f"200 EMA: ${cur_ema:,.2f} ({'Uptrend' if in_uptrend else 'Downtrend'})"
        )

        if entry_triggered:
            total_equity = account.get_total_equity(cur_price)
            exec_entry = cur_price * (1.0 + slippage_pct)

            units, initial_stop, dollar_risk = risk_engine.calculate_position(
                equity=total_equity,
                entry_price=exec_entry,
                atr=cur_atr,
                atr_multiplier=atr_mult,
                risk_pct=risk_pct,
            )

            # Cap units to 95% of available cash
            max_units = (account.cash * 0.95) / exec_entry
            units = min(units, max_units)

            if units > 0:
                print("\n🚀 [PAPER ALERT] BREAKOUT DETECTED! EXECUTING BUY ORDER...")
                new_pos = account.open_position(
                    symbol=symbol,
                    entry_price=exec_entry,
                    units=units,
                    stop_loss=initial_stop,
                    dollar_risk=dollar_risk,
                    fee_pct=fee_pct,
                )
                print(
                    f"   Bought {units:.5f} {symbol} at ${exec_entry:,.2f}\n"
                    f"   Initial Stop-Loss: ${initial_stop:,.2f} (Risk capped at ${dollar_risk:,.2f} / {risk_pct*100:.1f}%)\n"
                    f"   Remaining Cash: ${account.cash:,.2f}"
                )
        else:
            dist_to_breakout = ((breakout_high - cur_price) / cur_price) * 100.0
            print(f"   Status: Awaiting breakout (+{dist_to_breakout:.2f}% above current price). Cash: ${account.cash:,.2f}")


def main():
    parser = argparse.ArgumentParser(description="Live Paper Trading Runner")
    parser.add_argument("--once", action="store_true", help="Run a single live tick scan and exit")
    parser.add_argument("--status", action="store_true", help="Display current paper portfolio state")
    parser.add_argument("--reset", action="store_true", help="Reset portfolio to $10,000 initial cash")
    parser.add_argument("--interval", type=int, default=60, help="Loop interval in seconds (default: 60s)")
    parser.add_argument("--loop", action="store_true", help="Run continuous monitoring loop")
    args = parser.parse_args()

    account = PaperAccount(initial_capital=10000.0)

    if args.reset:
        account.reset(10000.0)
        return

    if args.status:
        config = load_config()
        # Fetch latest quick price for status display
        df = fetch_ohlcv(symbol=config["symbol"], timeframe=config["timeframe"], days=2, force_refresh=True)
        cur_price = float(df.iloc[-1]["close"])
        print_status(account, cur_price)
        return

    config = load_config()
    print("=" * 65)
    print("        WEB4 LIVE PAPER TRADING ENGINE STARTED")
    print("=" * 65)
    print(f"Asset:          {config['symbol']} ({config['timeframe']})")
    print(f"Risk per Trade: {config['account_risk_pct']*100:.1f}% (Dynamic ATR sizing)")
    print(f"Trailing Stop:  {config['atr_multiplier']}x ATR")
    print("=" * 65)

    if args.loop:
        print(f"\n[Engine] Starting continuous monitoring loop (Checking every {args.interval}s)... Press Ctrl+C to stop.\n")
        try:
            while True:
                run_tick(account, config)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[Engine] Live paper trading paused by user.")
    else:
        # Default: run one live tick
        run_tick(account, config)
        print("\n[Tip] To run continuously in the background, use: python live_paper_trader.py --loop")


if __name__ == "__main__":
    main()
