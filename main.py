"""Main entry point to execute the quantitative backtest pipeline."""

import json
from pathlib import Path
import matplotlib.pyplot as plt

from core.data_loader import fetch_ohlcv
from core.backtest_engine import run_backtest

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config" / "active_config.json"
DATA_DIR = PROJECT_ROOT / "data"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config file not found at {CONFIG_PATH}")
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def main():
    config = load_config()
    print("=" * 60)
    print("       WEB4 QUANTITATIVE TRADING AGENT - BACKTEST")
    print("=" * 60)
    print(f"Asset:            {config['symbol']} ({config['timeframe']})")
    print(f"Breakout Window:  {config['breakout_lookback']} bars")
    print(f"Macro Trend:      {config['ema_trend_filter']} EMA")
    print(f"ATR Trailing:     {config['atr_multiplier']}x ATR ({config['atr_period']} period)")
    print(f"Risk per Trade:   {config['account_risk_pct']*100:.1f}% (Dynamic Volatility Sized)")
    print("-" * 60)

    # 1. Fetch historical OHLCV data
    df = fetch_ohlcv(
        symbol=config["symbol"],
        timeframe=config["timeframe"],
        days=180,
    )

    # 2. Run Backtest
    print("\n[Engine] Running backtest simulation...")
    result = run_backtest(
        df=df,
        initial_capital=10000.0,
        account_risk_pct=config["account_risk_pct"],
        breakout_lookback=config["breakout_lookback"],
        ema_trend_filter=config["ema_trend_filter"],
        atr_period=config["atr_period"],
        atr_multiplier=config["atr_multiplier"],
        fee_pct=config["fee_pct"],
        slippage_pct=config["slippage_pct"],
    )

    # 3. Print Results
    print("\n" + "=" * 60)
    print("                 BACKTEST PERFORMANCE REPORT")
    print("=" * 60)
    print(f"Initial Capital:     ${result.initial_capital:,.2f}")
    print(f"Final Equity:        ${result.final_equity:,.2f}")
    print(f"Total Return:        {result.total_return_pct:+.2f}%")
    print(f"Total Trades:        {result.total_trades}")
    print(f"Winning Trades:      {result.winning_trades}")
    print(f"Losing Trades:       {result.losing_trades}")
    print(f"Win Rate:            {result.win_rate * 100:.2f}%")
    print(f"Profit Factor:       {result.profit_factor:.2f}")
    print(f"Max Drawdown:        {result.max_drawdown_pct:.2f}%")
    print(f"Annualized Sharpe:   {result.sharpe_ratio:.2f}")
    print("=" * 60)

    if result.trades:
        print("\nRecent 5 Completed Trades:")
        for t in result.trades[-5:]:
            print(
                f"  [{str(t.entry_time)[:16]} -> {str(t.exit_time)[:16]}] "
                f"Entry: ${t.entry_price:,.2f} | Exit: ${t.exit_price:,.2f} | "
                f"PnL: {t.pnl:+,.2f} ({t.return_pct*100:+.2f}%)"
            )

    # 4. Save Equity Curve Plot
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        chart_path = DATA_DIR / "equity_curve.png"
        plt.figure(figsize=(10, 5))
        plt.plot(result.equity_curve, label="Strategy Equity ($)", color="#00aa66", linewidth=1.5)
        plt.axhline(result.initial_capital, color="#888888", linestyle="--", label="Initial Capital")
        plt.title(f"Equity Curve - {config['symbol']} ({config['timeframe']}) - Return: {result.total_return_pct:+.2f}%")
        plt.xlabel("Time (Hourly Candles)")
        plt.ylabel("Portfolio Equity ($)")
        plt.grid(True, alpha=0.3)
        plt.legend()
        plt.tight_layout()
        plt.savefig(chart_path)
        plt.close()
        print(f"\n[Visual] Equity curve chart saved to: {chart_path.name}")
    except Exception as e:
        print(f"[Visual] Notice: Chart plot skipped ({e})")


if __name__ == "__main__":
    main()
