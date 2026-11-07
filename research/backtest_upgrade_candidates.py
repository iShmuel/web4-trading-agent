"""Empirical Backtest Suite for Strategy Candidate Upgrades.

Evaluates 3 candidate trading strategy upgrades against historical datasets
with full fee, slippage, and spread friction modeling:
1. Selective Shorting on Equities (SPY, QQQ) vs Long-Only Baseline
2. Statistical Arbitrage Pairs Trading (BTC/ETH, GLD/SLV, SPY/QQQ)
3. Delta-Neutral Basis Funding Rate Yield Harvesting (BTC & ETH historical funding)
"""

import json
import math
import sys
from pathlib import Path
from typing import Dict, Any, List, Tuple
import numpy as np
import pandas as pd
import ccxt

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_dataset(filename: str) -> pd.DataFrame:
    path = DATA_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Missing data file: {path}")
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    return df


# =====================================================================
# 1. Selective Shorting on Equities vs Long-Only Baseline
# =====================================================================
def run_momentum_backtest(
    df: pd.DataFrame,
    symbol: str,
    allow_short: bool = False,
    allow_long: bool = True,
    lookback: int = 24,
    macro_ema: int = 200,
    atr_mult: float = 2.25,
    fee_pct: float = 0.0004,
    slippage_pct: float = 0.0003,
    initial_capital: float = 10000.0,
    risk_pct: float = 0.02,
) -> Dict[str, Any]:
    """Simulates breakout momentum with Donchian + 200 EMA + ATR trailing stop."""
    data = df.copy()
    data["upper"] = data["high"].shift(1).rolling(lookback).max()
    data["lower"] = data["low"].shift(1).rolling(lookback).min()
    data["ema200"] = data["close"].ewm(span=macro_ema, adjust=False).mean()

    # ATR
    hl = data["high"] - data["low"]
    hc = (data["high"] - data["close"].shift(1)).abs()
    lc = (data["low"] - data["close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    data["atr"] = tr.rolling(14).mean()

    capital = initial_capital
    position = None
    trades = []
    equity_curve = [capital]

    for i in range(max(lookback, macro_ema) + 5, len(data)):
        row = data.iloc[i]
        price = row["close"]
        upper = row["upper"]
        lower = row["lower"]
        ema = row["ema200"]
        atr = row["atr"]

        if pd.isna(upper) or pd.isna(lower) or pd.isna(ema) or pd.isna(atr) or atr <= 0:
            continue

        # Position Management / Exit
        if position is not None:
            side = position["side"]
            entry_p = position["entry_price"]
            units = position["units"]
            stop = position["stop"]
            dollar_risk = position["dollar_risk"]

            # Update trailing stop
            if side == "LONG":
                new_stop = price - (atr * atr_mult)
                if new_stop > stop:
                    position["stop"] = new_stop
                    stop = new_stop

                # Check exit
                if price <= stop:
                    exit_price = price * (1.0 - slippage_pct)
                    gross = (exit_price - entry_p) * units
                    fee = (entry_p * units * fee_pct) + (exit_price * units * fee_pct)
                    net_pnl = gross - fee
                    capital += net_pnl
                    trades.append({
                        "side": "LONG",
                        "entry": entry_p,
                        "exit": exit_price,
                        "pnl": net_pnl,
                        "win": net_pnl > 0,
                        "reason": "ATR_STOP_HIT",
                    })
                    position = None
            else:  # SHORT
                new_stop = price + (atr * atr_mult)
                if new_stop < stop:
                    position["stop"] = new_stop
                    stop = new_stop

                # Check exit
                if price >= stop:
                    exit_price = price * (1.0 + slippage_pct)
                    gross = (entry_p - exit_price) * units
                    fee = (entry_p * units * fee_pct) + (exit_price * units * fee_pct)
                    net_pnl = gross - fee
                    capital += net_pnl
                    trades.append({
                        "side": "SHORT",
                        "entry": entry_p,
                        "exit": exit_price,
                        "pnl": net_pnl,
                        "win": net_pnl > 0,
                        "reason": "ATR_STOP_HIT",
                    })
                    position = None

        # Entry logic
        if position is None:
            # LONG breakout: above Donchian upper & above 200 EMA
            if allow_long and price > upper and price > ema:
                stop = price - (atr * atr_mult)
                risk_per_unit = max(price - stop, price * 0.005)
                dollar_risk = capital * risk_pct
                units = dollar_risk / risk_per_unit
                entry_exec = price * (1.0 + slippage_pct)
                position = {
                    "side": "LONG",
                    "entry_price": entry_exec,
                    "units": units,
                    "stop": stop,
                    "dollar_risk": dollar_risk,
                }
            # SHORT breakdown: below Donchian lower & below 200 EMA
            elif allow_short and price < lower and price < ema:
                stop = price + (atr * atr_mult)
                risk_per_unit = max(stop - price, price * 0.005)
                dollar_risk = capital * risk_pct
                units = dollar_risk / risk_per_unit
                entry_exec = price * (1.0 - slippage_pct)
                position = {
                    "side": "SHORT",
                    "entry_price": entry_exec,
                    "units": units,
                    "stop": stop,
                    "dollar_risk": dollar_risk,
                }

        equity_curve.append(capital)

    # Close open position at end
    if position is not None:
        row = data.iloc[-1]
        price = row["close"]
        side = position["side"]
        entry_p = position["entry_price"]
        units = position["units"]
        exit_price = price * (1.0 - slippage_pct if side == "LONG" else 1.0 + slippage_pct)
        gross = (exit_price - entry_p) * units if side == "LONG" else (entry_p - exit_price) * units
        fee = (entry_p * units * fee_pct) + (exit_price * units * fee_pct)
        net_pnl = gross - fee
        capital += net_pnl
        trades.append({
            "side": side,
            "entry": entry_p,
            "exit": exit_price,
            "pnl": net_pnl,
            "win": net_pnl > 0,
            "reason": "MARKET_CLOSE",
        })

    total_return_pct = ((capital - initial_capital) / initial_capital) * 100.0
    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    win_rate = (len(wins) / len(trades) * 100.0) if trades else 0.0

    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

    # Max Drawdown
    eq_series = pd.Series(equity_curve)
    peak = eq_series.cummax()
    dd = (peak - eq_series) / peak
    max_dd_pct = float(dd.max() * 100.0)

    # Sharpe ratio
    returns = eq_series.pct_change().dropna()
    mean_ret = returns.mean()
    std_ret = returns.std()
    sharpe = float((mean_ret / std_ret) * math.sqrt(252 * 6.5)) if std_ret > 0 else 0.0

    return {
        "symbol": symbol,
        "mode": "LONG_AND_SHORT" if allow_short else "LONG_ONLY",
        "bars": len(df),
        "total_trades": len(trades),
        "win_rate": round(win_rate, 2),
        "total_return_pct": round(total_return_pct, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "profit_factor": round(profit_factor, 2),
        "sharpe_ratio": round(sharpe, 2),
        "final_capital": round(capital, 2),
        "short_trades": sum(1 for t in trades if t["side"] == "SHORT"),
        "long_trades": sum(1 for t in trades if t["side"] == "LONG"),
    }


# =====================================================================
# 2. Statistical Arbitrage / Pairs Trading Backtest
# =====================================================================
def run_pairs_trading_backtest(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    pair_name: str,
    lookback: int = 48,
    entry_z: float = 2.0,
    exit_z: float = 0.5,
    stop_z: float = 3.5,
    fee_pct: float = 0.0004,
    slippage_pct: float = 0.0002,
    initial_capital: float = 10000.0,
    allocation_pct: float = 0.30,
) -> Dict[str, Any]:
    """Simulates market-neutral pairs trading using rolling beta and Z-score."""
    # Align on common timestamps
    common_idx = df_a.index.intersection(df_b.index)
    if len(common_idx) < lookback + 10:
        return {"error": f"Insufficient overlapping bars ({len(common_idx)})"}

    series_a = df_a.loc[common_idx, "close"].astype(float)
    series_b = df_b.loc[common_idx, "close"].astype(float)

    capital = initial_capital
    position = None  # {'type': 'LONG_A_SHORT_B' | 'SHORT_A_LONG_B', 'units_a', 'units_b', 'entry_a', 'entry_b'}
    trades = []
    equity_curve = [capital]

    for i in range(lookback, len(common_idx)):
        window_a = series_a.iloc[i - lookback : i]
        window_b = series_b.iloc[i - lookback : i]

        # Rolling Beta = Cov(A, B) / Var(B)
        cov = np.cov(window_a, window_b)[0, 1]
        var_b = np.var(window_b)
        beta = float(cov / var_b) if var_b > 0 else 1.0
        beta = max(0.05, min(beta, 5.0))

        # Spread
        window_spread = window_a - (beta * window_b)
        mean_spread = float(window_spread.mean())
        std_spread = float(window_spread.std())

        cur_a = float(series_a.iloc[i])
        cur_b = float(series_b.iloc[i])
        cur_spread = cur_a - (beta * cur_b)
        z_score = float((cur_spread - mean_spread) / std_spread) if std_spread > 0 else 0.0

        # Position evaluation
        if position is not None:
            p_type = position["type"]
            entry_a = position["entry_a"]
            entry_b = position["entry_b"]
            u_a = position["units_a"]
            u_b = position["units_b"]

            should_exit = False
            reason = ""

            # Check mean reversion exit (|Z| <= exit_z)
            if abs(z_score) <= exit_z:
                should_exit = True
                reason = f"TARGET_REVERSION (|z| {abs(z_score):.2f} <= {exit_z})"
            # Check stop loss (|Z| >= stop_z)
            elif abs(z_score) >= stop_z:
                should_exit = True
                reason = f"STOP_LOSS_HIT (|z| {abs(z_score):.2f} >= {stop_z})"

            if should_exit:
                if p_type == "LONG_A_SHORT_B":
                    pnl_a = (cur_a * (1.0 - slippage_pct) - entry_a) * u_a
                    pnl_b = (entry_b - cur_b * (1.0 + slippage_pct)) * u_b
                else:  # SHORT_A_LONG_B
                    pnl_a = (entry_a - cur_a * (1.0 + slippage_pct)) * u_a
                    pnl_b = (cur_b * (1.0 - slippage_pct) - entry_b) * u_b

                total_fees = (entry_a * u_a + cur_a * u_a + entry_b * u_b + cur_b * u_b) * fee_pct
                net_pnl = pnl_a + pnl_b - total_fees
                capital += net_pnl

                trades.append({
                    "pair": pair_name,
                    "type": p_type,
                    "pnl": net_pnl,
                    "win": net_pnl > 0,
                    "reason": reason,
                    "z_exit": round(z_score, 2),
                })
                position = None

        # Entry evaluation
        if position is None:
            notional = capital * allocation_pct
            if z_score <= -entry_z:
                # Spread is abnormally cheap -> Buy A, Sell B
                u_a = (notional * 0.5) / cur_a
                u_b = (notional * 0.5) / cur_b
                position = {
                    "type": "LONG_A_SHORT_B",
                    "entry_a": cur_a * (1.0 + slippage_pct),
                    "entry_b": cur_b * (1.0 - slippage_pct),
                    "units_a": u_a,
                    "units_b": u_b,
                    "beta": beta,
                }
            elif z_score >= entry_z:
                # Spread is abnormally expensive -> Sell A, Buy B
                u_a = (notional * 0.5) / cur_a
                u_b = (notional * 0.5) / cur_b
                position = {
                    "type": "SHORT_A_LONG_B",
                    "entry_a": cur_a * (1.0 - slippage_pct),
                    "entry_b": cur_b * (1.0 + slippage_pct),
                    "units_a": u_a,
                    "units_b": u_b,
                    "beta": beta,
                }

        equity_curve.append(capital)

    # Close open position at end
    if position is not None:
        cur_a = float(series_a.iloc[-1])
        cur_b = float(series_b.iloc[-1])
        p_type = position["type"]
        u_a = position["units_a"]
        u_b = position["units_b"]
        entry_a = position["entry_a"]
        entry_b = position["entry_b"]

        if p_type == "LONG_A_SHORT_B":
            pnl_a = (cur_a * (1.0 - slippage_pct) - entry_a) * u_a
            pnl_b = (entry_b - cur_b * (1.0 + slippage_pct)) * u_b
        else:
            pnl_a = (entry_a - cur_a * (1.0 + slippage_pct)) * u_a
            pnl_b = (cur_b * (1.0 - slippage_pct) - entry_b) * u_b

        total_fees = (entry_a * u_a + cur_a * u_a + entry_b * u_b + cur_b * u_b) * fee_pct
        net_pnl = pnl_a + pnl_b - total_fees
        capital += net_pnl
        trades.append({
            "pair": pair_name,
            "type": p_type,
            "pnl": net_pnl,
            "win": net_pnl > 0,
            "reason": "END_OF_DATA",
            "z_exit": 0.0,
        })

    total_return_pct = ((capital - initial_capital) / initial_capital) * 100.0
    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    win_rate = (len(wins) / len(trades) * 100.0) if trades else 0.0

    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

    eq_series = pd.Series(equity_curve)
    peak = eq_series.cummax()
    dd = (peak - eq_series) / peak
    max_dd_pct = float(dd.max() * 100.0)

    returns = eq_series.pct_change().dropna()
    std_ret = returns.std()
    sharpe = float((returns.mean() / std_ret) * math.sqrt(8760)) if std_ret > 0 else 0.0

    return {
        "pair": pair_name,
        "bars": len(common_idx),
        "total_trades": len(trades),
        "win_rate": round(win_rate, 2),
        "total_return_pct": round(total_return_pct, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "profit_factor": round(profit_factor, 2),
        "sharpe_ratio": round(sharpe, 2),
        "final_capital": round(capital, 2),
    }


# =====================================================================
# 3. Basis Yield Harvesting Backtest (Funding Rate Cash-and-Carry)
# =====================================================================
def run_basis_yield_backtest(
    symbol: str = "BTC/USDT:USDT",
    hurdle_apr: float = 8.0,
    allocation: float = 3000.0,
    fee_pct: float = 0.0004,
) -> Dict[str, Any]:
    """Simulates cash-and-carry delta neutral funding yield on real Binance funding history."""
    binance = ccxt.binance({"enableRateLimit": True, "timeout": 15000})
    try:
        # Fetch up to 500 historical funding epochs (~166 days)
        history = binance.fetch_funding_rate_history(symbol, limit=500)
    except Exception as e:
        return {"error": f"Failed to fetch funding history: {e}"}

    if not history:
        return {"error": "No funding rate history returned"}

    # Sort chronologically
    history = sorted(history, key=lambda x: x["timestamp"])

    in_position = False
    entry_fee_paid = 0.0
    exit_fee_paid = 0.0
    cumulative_yield = 0.0
    funding_epochs_received = 0
    negative_epochs = 0
    yield_curve = [0.0]

    for epoch in history:
        rate = float(epoch.get("fundingRate", 0.0))
        annualized_apr = rate * 3 * 365 * 100.0

        if not in_position:
            # Entry condition: annualized APR exceeds hurdle rate
            if annualized_apr >= hurdle_apr:
                in_position = True
                # Pay 2 legs entry fee: Spot Long + Perp Short
                entry_cost = (allocation * 2) * fee_pct
                entry_fee_paid += entry_cost
                cumulative_yield -= entry_cost
        else:
            # Exit condition: 2 consecutive negative funding rates or rate < -0.01%
            if rate < -0.0001:
                negative_epochs += 1
                if negative_epochs >= 2:
                    # Unwind position
                    in_position = False
                    exit_cost = (allocation * 2) * fee_pct
                    exit_fee_paid += exit_cost
                    cumulative_yield -= exit_cost
                    negative_epochs = 0
            else:
                negative_epochs = 0

            if in_position:
                # Accrue 8h funding payout: Long receives if rate < 0, Short receives if rate > 0
                # Because we are Long Spot and Short Perp:
                # When rate > 0: Perp Longs pay Perp Shorts -> We RECEIVE rate * allocation!
                # When rate < 0: Perp Shorts pay Perp Longs -> We PAY rate * allocation!
                payout = allocation * rate
                cumulative_yield += payout
                funding_epochs_received += 1

        yield_curve.append(cumulative_yield)

    # Net metrics
    total_fees = entry_fee_paid + exit_fee_paid
    net_yield_pct = (cumulative_yield / allocation) * 100.0
    days_elapsed = (len(history) * 8.0) / 24.0
    annualized_net_apy = (net_yield_pct * (365.25 / days_elapsed)) if days_elapsed > 0 else 0.0

    y_series = pd.Series(yield_curve)
    peak = y_series.cummax()
    dd = peak - y_series
    max_dd_dollars = float(dd.max())
    max_dd_pct = (max_dd_dollars / allocation) * 100.0

    return {
        "symbol": symbol,
        "epochs_evaluated": len(history),
        "days_simulated": round(days_elapsed, 1),
        "hurdle_apr": hurdle_apr,
        "allocation_usd": allocation,
        "gross_funding_received": round(cumulative_yield + total_fees, 2),
        "trading_fees_paid": round(total_fees, 2),
        "net_cash_yield_usd": round(cumulative_yield, 2),
        "net_yield_pct": round(net_yield_pct, 2),
        "annualized_net_apy": round(annualized_net_apy, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "epochs_in_market": funding_epochs_received,
    }


def main():
    print("=" * 70)
    print("EMPIRICAL STRATEGY UPGRADE BACKTEST RESEARCH")
    print("=" * 70)

    results = {}

    # -------------------------------------------------------------
    # 1. Backtest Selective Shorting on Equities vs Long-Only
    # -------------------------------------------------------------
    print("\n--- [1] BACKTEST: Selective Shorting on Equities vs Baseline ---")
    equity_assets = ["SPY", "QQQ", "NVDA"]
    short_results = []

    for sym in equity_assets:
        try:
            df = load_dataset(f"stock_{sym}_1h_365d.csv")
            long_only = run_momentum_backtest(df, sym, allow_short=False, allow_long=True)
            long_short = run_momentum_backtest(df, sym, allow_short=True, allow_long=True)

            short_results.append({
                "symbol": sym,
                "long_only": long_only,
                "long_short": long_short,
                "return_delta_pct": round(long_short["total_return_pct"] - long_only["total_return_pct"], 2),
                "dd_delta_pct": round(long_short["max_drawdown_pct"] - long_only["max_drawdown_pct"], 2),
                "sharpe_delta": round(long_short["sharpe_ratio"] - long_only["sharpe_ratio"], 2),
            })
            print(
                f"{sym:<6} | Long-Only: Ret {long_only['total_return_pct']:+6.2f}% (DD {long_only['max_drawdown_pct']:4.2f}%, Sh {long_only['sharpe_ratio']:4.2f}) | "
                f"Long+Short: Ret {long_short['total_return_pct']:+6.2f}% (DD {long_short['max_drawdown_pct']:4.2f}%, Sh {long_short['sharpe_ratio']:4.2f}) | "
                f"Delta: {long_short['total_return_pct'] - long_only['total_return_pct']:+5.2f}%"
            )
        except Exception as e:
            print(f"Error on {sym}: {e}")

    results["selective_shorting"] = short_results

    # -------------------------------------------------------------
    # 2. Backtest Statistical Arbitrage Pairs Trading
    # -------------------------------------------------------------
    print("\n--- [2] BACKTEST: Statistical Arbitrage Pairs Trading ---")
    pairs_to_test = [
        ("crypto_BTC_USDT_1h_365d.csv", "crypto_ETH_USDT_1h_365d.csv", "BTC/ETH"),
        ("stock_GLD_1h_365d.csv", "stock_SLV_1h_365d.csv", "GLD/SLV (Gold vs Silver)"),
        ("stock_SPY_1h_365d.csv", "stock_QQQ_1h_365d.csv", "SPY/QQQ (Market vs Tech)"),
    ]
    stat_arb_results = []

    for file_a, file_b, name in pairs_to_test:
        try:
            df_a = load_dataset(file_a)
            df_b = load_dataset(file_b)
            arb_res = run_pairs_trading_backtest(df_a, df_b, name, lookback=48, entry_z=2.0, exit_z=0.5, stop_z=3.5)
            stat_arb_results.append(arb_res)
            print(
                f"{name:<25} | Trades: {arb_res['total_trades']:<3} | WinRate: {arb_res['win_rate']:5.1f}% | "
                f"Return: {arb_res['total_return_pct']:+6.2f}% | MaxDD: {arb_res['max_drawdown_pct']:4.2f}% | Sharpe: {arb_res['sharpe_ratio']:4.2f}"
            )
        except Exception as e:
            print(f"Error on pair {name}: {e}")

    results["pairs_trading"] = stat_arb_results

    # -------------------------------------------------------------
    # 3. Backtest Basis Yield Harvesting (Binance Historical Funding)
    # -------------------------------------------------------------
    print("\n--- [3] BACKTEST: Delta-Neutral Basis Funding Rate Yield ---")
    basis_assets = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
    basis_results = []

    for sym in basis_assets:
        try:
            basis_res = run_basis_yield_backtest(sym, hurdle_apr=8.0, allocation=3000.0)
            basis_results.append(basis_res)
            if "error" not in basis_res:
                print(
                    f"{sym:<15} | Days: {basis_res['days_simulated']:<5} | Gross Yield: ${basis_res['gross_funding_received']:+6.2f} | "
                    f"Fees: ${basis_res['trading_fees_paid']:5.2f} | Net Cash: ${basis_res['net_cash_yield_usd']:+6.2f} | "
                    f"Net APY: {basis_res['annualized_net_apy']:+5.1f}% | MaxDD: {basis_res['max_drawdown_pct']:4.2f}%"
                )
            else:
                print(f"{sym:<15} | Error: {basis_res['error']}")
        except Exception as e:
            print(f"Error on basis yield {sym}: {e}")

    results["basis_yield"] = basis_results

    # Save artifact
    out_file = DATA_DIR / "candidate_upgrades_backtest.json"
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n[Artifact Saved] Detailed results saved to {out_file.name}")


if __name__ == "__main__":
    main()
