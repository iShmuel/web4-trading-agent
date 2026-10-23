"""Live Multi-Asset Paper Trading Runner with Autonomous Self-Improvement.
Monitors a basket of Crypto & Stocks simultaneously, executes paper orders,
and enforces a maximum concurrent position limit while capping risk per trade.
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
from core.strategy import compute_indicators
from core.data_loader import fetch_ohlcv
from agent.optimizer import run_optimization_cycle

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config" / "active_config.json"
DATA_DIR = PROJECT_ROOT / "data"
LOG_FILE = DATA_DIR / "live_trader.log"


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
    log(f"Initial Capital:     ${account.initial_capital:,.2f}")
    log(f"Available Cash:      ${account.cash:,.2f}")

    total_equity = account.get_total_equity(current_prices)
    unrealized_total = 0.0

    open_pos = account.positions
    log(f"Active Positions:    {len(open_pos)} open")

    if open_pos:
        log("\n" + "-" * 68)
        log(f"{'SYMBOL':<10} {'UNITS':<10} {'ENTRY':<10} {'CURRENT':<10} {'UNREALIZED PNL':<18} {'TRAIL STOP':<12}")
        log("-" * 68)
        for sym, pos in open_pos.items():
            cur_price = current_prices.get(sym, pos["entry_price"])
            pos_val = pos["units"] * cur_price
            pnl = (cur_price - pos["entry_price"]) * pos["units"]
            pnl_pct = (cur_price - pos["entry_price"]) / pos["entry_price"] * 100.0
            unrealized_total += pnl

            log(
                f"{sym:<10} {pos['units']:<10.4f} ${pos['entry_price']:<9.2f} ${cur_price:<9.2f} "
                f"${pnl:+,.2f} ({pnl_pct:+.2f}%)   ${pos['trailing_stop']:<10.2f}"
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


def get_asset_profile(symbol: str, config: dict) -> dict:
    """Return asset-specific strategy parameters (e.g. 50 EMA for crypto, 2x ATR TP for stocks)."""
    profiles = config.get("asset_profiles", {})
    for profile_name, prof in profiles.items():
        if symbol in prof.get("symbols", []):
            merged = dict(config)
            merged.update(prof)
            return merged

    # Fallback heuristics
    merged = dict(config)
    if "/" in symbol or "USDT" in symbol:
        merged.setdefault("ema_trend_filter", 50)
        merged.setdefault("atr_multiplier", 3.0)
        merged.setdefault("take_profit_atr_mult", 0.0)
        merged.setdefault("take_profit_ratio", 0.0)
    else:
        merged.setdefault("ema_trend_filter", 100)
        merged.setdefault("atr_multiplier", 2.5)
        merged.setdefault("take_profit_atr_mult", 2.0)
        merged.setdefault("take_profit_ratio", 0.5)
    return merged


def evaluate_asset(
    symbol: str,
    account: PaperAccount,
    config: dict,
    max_positions: int,
    current_prices: Dict[str, float],
):
    asset_cfg = get_asset_profile(symbol, config)
    timeframe = asset_cfg.get("timeframe", "1h")
    risk_pct = asset_cfg.get("account_risk_pct", 0.02)
    atr_mult = asset_cfg.get("atr_multiplier", 3.0 if "/" in symbol else 2.5)
    ema_period = asset_cfg.get("ema_trend_filter", 50 if "/" in symbol else 100)
    lookback = asset_cfg.get("breakout_lookback", 24)
    atr_period = asset_cfg.get("atr_period", 14)
    tp_mult = asset_cfg.get("take_profit_atr_mult", 0.0)
    tp_ratio = asset_cfg.get("take_profit_ratio", 0.5)
    fee_pct = asset_cfg.get("fee_pct", 0.0006)
    slippage_pct = asset_cfg.get("slippage_pct", 0.0004)

    # Fetch ~30 days of data
    df = fetch_ohlcv(symbol=symbol, timeframe=timeframe, days=30, force_refresh=True)
    data = compute_indicators(
        df,
        breakout_lookback=lookback,
        ema_trend_filter=ema_period,
        atr_period=atr_period,
    )

    latest_bar = data.iloc[-1]
    cur_price = float(latest_bar["close"])
    current_prices[symbol] = cur_price

    cur_atr = float(latest_bar["atr"])
    cur_ema = float(latest_bar["ema_filter"])
    breakout_high = float(latest_bar["breakout_high"])
    in_uptrend = cur_price > cur_ema

    risk_engine = RiskEngine(default_risk_pct=risk_pct)
    is_holding = account.has_position(symbol)

    # --- IF CURRENTLY HOLDING POSITION ---
    if is_holding:
        pos = account.get_position(symbol)
        trailing_stop = float(pos["trailing_stop"])
        entry_price = float(pos["entry_price"])
        tp_target = float(pos.get("tp_target", 0.0))
        tp_hit = bool(pos.get("tp_hit", False))

        # Check Partial Take-Profit (for stocks / configured assets)
        if tp_mult > 0 and not tp_hit and tp_target > 0 and cur_price >= tp_target:
            scale_units = pos["units"] * tp_ratio
            if scale_units > 0:
                exec_tp = cur_price * (1.0 - slippage_pct)
                trade = account.partial_close_position(
                    symbol=symbol,
                    exit_price=exec_tp,
                    exit_units=scale_units,
                    exit_reason=f"Take Profit ({int(tp_ratio*100)}% Scale-Out)",
                    fee_pct=fee_pct,
                )
                log(f"\n🎯 [PAPER ALERT] TAKE PROFIT HIT FOR {symbol}!")
                log(f"   Scaled out {scale_units:.5f} units @ ${exec_tp:,.2f} | PnL: ${trade['pnl']:+,.2f} | Stop moved to Breakeven (${entry_price:,.2f})")
                pos["tp_hit"] = True
                new_stop = max(trailing_stop, entry_price)
                account.update_trailing_stop(symbol, new_stop, cur_price)
                trailing_stop = new_stop

        if latest_bar["low"] <= trailing_stop or cur_price <= trailing_stop:
            exec_exit = min(cur_price, trailing_stop) * (1.0 - slippage_pct)
            log(f"\n🚨 [PAPER ALERT] TRAILING STOP HIT FOR {symbol}!")
            trade = account.close_position(
                symbol=symbol,
                exit_price=exec_exit,
                exit_reason="ATR Trailing Stop Hit",
                fee_pct=fee_pct,
            )
            log(
                f"   Sold {trade['units']:.5f} {symbol} at ${trade['exit_price']:,.2f} | "
                f"PnL: ${trade['pnl']:+,.2f} ({trade['return_pct']:+.2f}%) | "
                f"Cash: ${account.cash:,.2f}"
            )
        else:
            updated_stop = risk_engine.update_trailing_stop(
                current_price=cur_price,
                current_atr=cur_atr,
                atr_multiplier=atr_mult,
                existing_stop=trailing_stop,
            )
            if updated_stop > trailing_stop:
                account.update_trailing_stop(symbol, updated_stop, cur_price)
                log(f"   📈 [{symbol}] Trailing stop ratcheted: ${trailing_stop:,.2f} -> ${updated_stop:,.2f}")

            unrealized = (cur_price - pos["entry_price"]) * pos["units"]
            pnl_pct = (cur_price - pos["entry_price"]) / pos["entry_price"] * 100.0
            log(
                f"   HOLDING {symbol:<9} Price: ${cur_price:>9.2f} | Stop: ${pos['trailing_stop']:>9.2f} | "
                f"Unrealized: ${unrealized:+,.2f} ({pnl_pct:+.2f}%)"
            )

    # --- IF NOT HOLDING: SCAN FOR BREAKOUT ---
    else:
        entry_triggered = in_uptrend and (cur_price > breakout_high)

        if len(account.positions) >= max_positions:
            log(f"   SCANNING {symbol:<8} Price: ${cur_price:>9.2f} | (Max slots full: {len(account.positions)}/{max_positions})")
            return

        if entry_triggered:
            total_equity = account.get_total_equity(current_prices)
            exec_entry = cur_price * (1.0 + slippage_pct)

            units, initial_stop, dollar_risk = risk_engine.calculate_position(
                equity=total_equity,
                entry_price=exec_entry,
                atr=cur_atr,
                atr_multiplier=atr_mult,
                risk_pct=risk_pct,
            )

            # Cap position cost so it doesn't exceed available cash
            # Allocate max 35% of total capital to any single asset
            max_cash_allocation = min(account.cash * 0.90, total_equity * 0.35)
            max_units = max_cash_allocation / exec_entry
            units = min(units, max_units)

            if units > 0 and (units * exec_entry) <= account.cash:
                tp_target = exec_entry + (tp_mult * cur_atr) if tp_mult > 0 else 0.0
                log(f"\n🚀 [PAPER ALERT] BREAKOUT DETECTED ON {symbol}! EXECUTING BUY...")
                new_pos = account.open_position(
                    symbol=symbol,
                    entry_price=exec_entry,
                    units=units,
                    stop_loss=initial_stop,
                    dollar_risk=dollar_risk,
                    max_concurrent_positions=max_positions,
                    fee_pct=fee_pct,
                )
                new_pos["tp_target"] = tp_target
                new_pos["tp_hit"] = False
                account._save()

                tp_str = f" | TP Target: ${tp_target:,.2f}" if tp_target > 0 else ""
                log(
                    f"   Bought {units:.5f} {symbol} at ${exec_entry:,.2f}\n"
                    f"   Stop-Loss: ${initial_stop:,.2f} (Max Risk: ${dollar_risk:,.2f} / {risk_pct*100:.1f}%){tp_str}\n"
                    f"   Remaining Cash: ${account.cash:,.2f}"
                )
        else:
            dist = ((breakout_high - cur_price) / cur_price) * 100.0
            trend_str = "Uptrend" if in_uptrend else "Downtrend"
            log(
                f"   SCANNING {symbol:<8} ({timeframe}) Price: ${cur_price:>9.2f} | "
                f"Breakout: ${breakout_high:>9.2f} ({dist:+.2f}%) | {ema_period} EMA ({trend_str})"
            )



def run_portfolio_tick(account: PaperAccount) -> Dict[str, float]:
    """Scan all assets in the watchlist simultaneously."""
    config = load_config()
    watchlist = config.get("watchlist", ["BTC/USDT", "ETH/USDT", "SOL/USDT", "NVDA", "SPY"])
    max_positions = config.get("max_concurrent_positions", 3)

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    log(f"\n[{now_str}] Polling live watchlist ({len(watchlist)} assets | Max open positions: {max_positions})...")

    current_prices: Dict[str, float] = {}

    for symbol in watchlist:
        try:
            evaluate_asset(
                symbol=symbol,
                account=account,
                config=config,
                max_positions=max_positions,
                current_prices=current_prices,
            )
        except Exception as e:
            log(f"   ⚠️ Warning evaluating {symbol}: {e}")

    total_equity = account.get_total_equity(current_prices)
    log(f"   Portfolio Equity: ${total_equity:,.2f} | Cash: ${account.cash:,.2f} | Open: {len(account.positions)}")
    return current_prices


def main():
    parser = argparse.ArgumentParser(description="Multi-Asset Live Paper Trading Runner")
    parser.add_argument("--once", action="store_true", help="Run a single live portfolio scan across all assets")
    parser.add_argument("--status", action="store_true", help="Display current multi-asset portfolio status")
    parser.add_argument("--reset", action="store_true", help="Reset portfolio to $10,000 initial cash")
    parser.add_argument("--interval", type=int, default=60, help="Loop interval in seconds (default: 60s)")
    parser.add_argument("--optimize-hours", type=int, default=6, help="Self-improvement tournament interval in hours (default: 6)")
    parser.add_argument("--loop", action="store_true", help="Run continuous monitoring loop")
    args = parser.parse_args()

    account = PaperAccount(initial_capital=10000.0)

    if args.reset:
        account.reset(10000.0)
        return

    if args.status:
        config = load_config()
        watchlist = config.get("watchlist", ["BTC/USDT", "ETH/USDT", "SOL/USDT", "NVDA", "SPY"])
        prices = {}
        for sym in watchlist:
            try:
                df = fetch_ohlcv(symbol=sym, timeframe=config.get("timeframe", "1h"), days=2, force_refresh=True)
                prices[sym] = float(df.iloc[-1]["close"])
            except Exception:
                pass
        print_status(account, prices)
        return

    config = load_config()
    watchlist = config.get("watchlist", ["BTC/USDT", "ETH/USDT", "SOL/USDT", "NVDA", "SPY"])
    log("=" * 68)
    log("       WEB4 MULTI-ASSET LIVE PAPER TRADING ENGINE STARTED")
    log("=" * 68)
    log(f"Watchlist:          {', '.join(watchlist)}")
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
                run_portfolio_tick(account)

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
        run_portfolio_tick(account)
        log("\n[Tip] To run continuously in the background, use: python live_paper_trader.py --loop")


if __name__ == "__main__":
    main()
