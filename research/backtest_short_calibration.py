"""Empirical Research Backtest: Mean Reversion Short Calibration.

Compares:
1. Baseline: Long-Only Mean Reversion (allow_short=False)
2. Strict Short: Shorting at RSI > 75 (allow_short=True, rsi_overbought=75)
3. Calibrated Short: Shorting at RSI > 70 (allow_short=True, rsi_overbought=70)
4. Aggressive Short: Shorting at RSI > 68 (allow_short=True, rsi_overbought=68)

Uses realistic execution:
- Next-Open execution (fill_mode="next_open")
- 0.06% fees + 0.04% slippage
- 180-day historical window
"""

import json
import sys
from pathlib import Path
import pandas as pd
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.data_loader import fetch_ohlcv, get_data_dir, is_crypto_symbol
from core.backtest_engine import run_mean_reversion_backtest, BacktestResult
CONFIG_PATH = PROJECT_ROOT / "config" / "active_config.json"


def run_comparison():
    with open(CONFIG_PATH, "r") as f:
        cfg = json.load(f)

    watchlist = cfg.get("watchlist", [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "SUI/USDT", "TAO/USDT", "DOGE/USDT",
        "AVAX/USDT", "BNB/USDT", "NEAR/USDT", "LINK/USDT", "NVDA", "AMD",
        "AAPL", "PLTR", "TSLA", "SPY", "QQQ", "GLD", "SLV", "TLT", "XLE"
    ])

    results = []

    print("=" * 80)
    print("   RUNNING EMPIRICAL BACKTEST: MEAN REVERSION SHORT CALIBRATION")
    print("   Universe: 21 Assets | Cost Model: 0.06% Fee + 0.04% Slippage (Next-Open)")
    print("=" * 80)

    for sym in watchlist:
        try:
            days = 180
            df = fetch_ohlcv(symbol=sym, timeframe="1h", days=days)
            if df is None or len(df) < 100:
                continue

            # 1. Baseline: Long-Only
            res_long_only = run_mean_reversion_backtest(
                df=df,
                initial_capital=10000.0,
                account_risk_pct=0.02,
                rsi_oversold=30.0,
                rsi_overbought=75.0,
                allow_short=False,
                fee_pct=0.0006,
                slippage_pct=0.0004,
                fill_mode="next_open",
            )

            # 2. Strict Short: RSI > 75
            res_short_75 = run_mean_reversion_backtest(
                df=df,
                initial_capital=10000.0,
                account_risk_pct=0.02,
                rsi_oversold=30.0,
                rsi_overbought=75.0,
                allow_short=True,
                fee_pct=0.0006,
                slippage_pct=0.0004,
                fill_mode="next_open",
            )

            # 3. Calibrated Short: RSI > 70
            res_short_70 = run_mean_reversion_backtest(
                df=df,
                initial_capital=10000.0,
                account_risk_pct=0.02,
                rsi_oversold=30.0,
                rsi_overbought=70.0,
                allow_short=True,
                fee_pct=0.0006,
                slippage_pct=0.0004,
                fill_mode="next_open",
            )

            # 4. Aggressive Short: RSI > 68
            res_short_68 = run_mean_reversion_backtest(
                df=df,
                initial_capital=10000.0,
                account_risk_pct=0.02,
                rsi_oversold=30.0,
                rsi_overbought=68.0,
                allow_short=True,
                fee_pct=0.0006,
                slippage_pct=0.0004,
                fill_mode="next_open",
            )

            results.append({
                "symbol": sym,
                "bars": len(df),
                "long_only_ret": res_long_only.total_return_pct,
                "long_only_dd": res_long_only.max_drawdown_pct,
                "long_only_wr": res_long_only.win_rate * 100.0,
                "long_only_trades": res_long_only.total_trades,

                "short_75_ret": res_short_75.total_return_pct,
                "short_75_dd": res_short_75.max_drawdown_pct,
                "short_75_wr": res_short_75.win_rate * 100.0,
                "short_75_trades": res_short_75.total_trades,

                "short_70_ret": res_short_70.total_return_pct,
                "short_70_dd": res_short_70.max_drawdown_pct,
                "short_70_wr": res_short_70.win_rate * 100.0,
                "short_70_trades": res_short_70.total_trades,

                "short_68_ret": res_short_68.total_return_pct,
                "short_68_dd": res_short_68.max_drawdown_pct,
                "short_68_wr": res_short_68.win_rate * 100.0,
                "short_68_trades": res_short_68.total_trades,
            })
            print(f"Tested {sym:<10}: LongOnly: {res_long_only.total_return_pct:>+5.2f}% | Short75: {res_short_75.total_return_pct:>+5.2f}% | Short70: {res_short_70.total_return_pct:>+5.2f}% | Short68: {res_short_68.total_return_pct:>+5.2f}%")

        except Exception as e:
            print(f"Error testing {sym}: {e}")

    # Summary table
    res_df = pd.DataFrame(results)
    out_file = PROJECT_ROOT / "data" / "short_calibration_research.json"
    res_df.to_json(out_file, indent=2, orient="records")

    print("\n" + "=" * 90)
    print(f"{'Symbol':<10} {'Long-Only Return':<18} {'Short RSI>75 Return':<20} {'Short RSI>70 Return':<20} {'Short RSI>68 Return':<20}")
    print("-" * 90)
    for _, r in res_df.iterrows():
        print(f"{r['symbol']:<10} {r['long_only_ret']:>+6.2f}% (DD {r['long_only_dd']:4.1f}%)    {r['short_75_ret']:>+6.2f}% (DD {r['short_75_dd']:4.1f}%)      {r['short_70_ret']:>+6.2f}% (DD {r['short_70_dd']:4.1f}%)      {r['short_68_ret']:>+6.2f}% (DD {r['short_68_dd']:4.1f}%)")

    print("=" * 90)
    print(f"AVERAGES ACROSS {len(res_df)} ASSETS:")
    print(f"  Long-Only Baseline: Return {res_df['long_only_ret'].mean():>+5.2f}% | Max DD {res_df['long_only_dd'].mean():4.1f}% | Win Rate {res_df['long_only_wr'].mean():4.1f}% | Trades {res_df['long_only_trades'].mean():4.1f}")
    print(f"  Short RSI > 75:     Return {res_df['short_75_ret'].mean():>+5.2f}% | Max DD {res_df['short_75_dd'].mean():4.1f}% | Win Rate {res_df['short_75_wr'].mean():4.1f}% | Trades {res_df['short_75_trades'].mean():4.1f}")
    print(f"  Short RSI > 70:     Return {res_df['short_70_ret'].mean():>+5.2f}% | Max DD {res_df['short_70_dd'].mean():4.1f}% | Win Rate {res_df['short_70_wr'].mean():4.1f}% | Trades {res_df['short_70_trades'].mean():4.1f}")
    print(f"  Short RSI > 68:     Return {res_df['short_68_ret'].mean():>+5.2f}% | Max DD {res_df['short_68_dd'].mean():4.1f}% | Win Rate {res_df['short_68_wr'].mean():4.1f}% | Trades {res_df['short_68_trades'].mean():4.1f}")
    print("=" * 90)


if __name__ == "__main__":
    run_comparison()
