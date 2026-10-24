"""Event-driven backtesting engine with realistic fees, slippage, and performance metrics."""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import numpy as np
import pandas as pd

from core.risk_engine import RiskEngine
from core.strategy import compute_indicators, compute_mean_reversion_indicators


@dataclass
class TradeRecord:
    entry_time: Any
    exit_time: Any
    entry_price: float
    exit_price: float
    units: float
    dollar_risk: float
    pnl: float
    return_pct: float
    exit_reason: str
    side: str = "LONG"
    strategy_id: str = "momentum"


@dataclass
class BacktestResult:
    initial_capital: float
    final_equity: float
    total_return_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    max_drawdown_pct: float
    sharpe_ratio: float
    total_fees_paid: float = 0.0
    trades: List[TradeRecord] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)


def _estimate_bars_per_year(df: pd.DataFrame) -> float:
    """Estimate annualization factor from the dataframe's actual time index."""
    if len(df) < 2:
        return 8760.0  # default: hourly
    idx = df.index
    if hasattr(idx, 'to_series'):
        diffs = idx.to_series().diff().dropna()
    else:
        diffs = pd.Series(idx).diff().dropna()
    if len(diffs) == 0:
        return 8760.0
    median_seconds = diffs.dt.total_seconds().median()
    if median_seconds <= 0:
        return 8760.0
    bars_per_year = (365.25 * 24 * 3600) / median_seconds
    return bars_per_year


def run_backtest(
    df: pd.DataFrame,
    initial_capital: float = 10000.0,
    account_risk_pct: float = 0.02,
    breakout_lookback: int = 24,
    ema_trend_filter: int = 200,
    atr_period: int = 14,
    atr_multiplier: float = 2.5,
    take_profit_atr_mult: float = 2.0,
    take_profit_ratio: float = 0.5,
    allow_short: bool = True,
    fee_pct: float = 0.0006,
    slippage_pct: float = 0.0004,
) -> BacktestResult:
    """Run Momentum (Donchian Breakout + EMA + ATR) backtest simulation."""
    data = compute_indicators(
        df,
        breakout_lookback=breakout_lookback,
        ema_trend_filter=ema_trend_filter,
        atr_period=atr_period,
    )

    risk_engine = RiskEngine(default_risk_pct=account_risk_pct)

    equity = initial_capital
    peak_equity = initial_capital
    max_drawdown = 0.0
    total_fees = 0.0

    in_position = False
    pos_side = "LONG"
    entry_price = 0.0
    entry_time = None
    units = 0.0
    dollar_risk = 0.0
    trailing_stop = 0.0
    tp_target = 0.0
    tp_hit = False

    trades: List[TradeRecord] = []
    equity_curve: List[float] = [equity]

    for idx, row in data.iterrows():
        close = row["close"]
        high = row["high"]
        low = row["low"]
        atr = row["atr"]

        if pd.isna(atr) or pd.isna(row["ema_filter"]):
            equity_curve.append(equity)
            continue

        if not in_position:
            # Check Long signal
            if row.get("long_signal", row.get("entry_signal", False)):
                pos_side = "LONG"
                exec_entry_price = close * (1.0 + slippage_pct)
                units, initial_stop, dollar_risk = risk_engine.calculate_position(
                    equity=equity,
                    entry_price=exec_entry_price,
                    atr=atr,
                    atr_multiplier=atr_multiplier,
                    risk_pct=account_risk_pct,
                    is_short=False,
                )
                max_allowed_units = (equity * 0.95) / exec_entry_price
                units = min(units, max_allowed_units)

                if units > 0:
                    entry_fee = (units * exec_entry_price) * fee_pct
                    total_fees += entry_fee
                    equity -= entry_fee
                    in_position = True
                    entry_price = exec_entry_price
                    entry_time = idx
                    trailing_stop = initial_stop
                    tp_hit = False
                    if take_profit_atr_mult > 0:
                        tp_target = entry_price + (take_profit_atr_mult * atr)

            # Check Short signal
            elif allow_short and row.get("short_signal", False):
                pos_side = "SHORT"
                exec_entry_price = close * (1.0 - slippage_pct)
                units, initial_stop, dollar_risk = risk_engine.calculate_position(
                    equity=equity,
                    entry_price=exec_entry_price,
                    atr=atr,
                    atr_multiplier=atr_multiplier,
                    risk_pct=account_risk_pct,
                    is_short=True,
                )
                max_allowed_units = (equity * 0.95) / exec_entry_price
                units = min(units, max_allowed_units)

                if units > 0:
                    entry_fee = (units * exec_entry_price) * fee_pct
                    total_fees += entry_fee
                    equity -= entry_fee
                    in_position = True
                    entry_price = exec_entry_price
                    entry_time = idx
                    trailing_stop = initial_stop
                    tp_hit = False
                    if take_profit_atr_mult > 0:
                        tp_target = entry_price - (take_profit_atr_mult * atr)

        else:
            if pos_side == "LONG":
                # Partial TP check
                if take_profit_atr_mult > 0 and not tp_hit and high >= tp_target:
                    exec_tp_price = tp_target * (1.0 - slippage_pct)
                    tp_units = units * take_profit_ratio
                    exit_fee = (tp_units * exec_tp_price) * fee_pct
                    total_fees += exit_fee
                    gross_pnl = (exec_tp_price - entry_price) * tp_units
                    net_pnl = gross_pnl - exit_fee
                    equity += net_pnl
                    ret_pct = (exec_tp_price - entry_price) / entry_price

                    trades.append(
                        TradeRecord(
                            entry_time=entry_time,
                            exit_time=idx,
                            entry_price=entry_price,
                            exit_price=exec_tp_price,
                            units=tp_units,
                            dollar_risk=dollar_risk * take_profit_ratio,
                            pnl=net_pnl,
                            return_pct=ret_pct,
                            exit_reason=f"Take Profit ({int(take_profit_ratio*100)}% Scale-Out)",
                            side="LONG",
                            strategy_id="momentum",
                        )
                    )
                    units -= tp_units
                    tp_hit = True
                    trailing_stop = max(trailing_stop, entry_price)

                # Trailing stop check
                if low <= trailing_stop:
                    exec_exit_price = min(trailing_stop, close) * (1.0 - slippage_pct)
                    exit_fee = (units * exec_exit_price) * fee_pct
                    total_fees += exit_fee
                    gross_pnl = (exec_exit_price - entry_price) * units
                    net_pnl = gross_pnl - exit_fee
                    equity += net_pnl
                    ret_pct = (exec_exit_price - entry_price) / entry_price

                    trades.append(
                        TradeRecord(
                            entry_time=entry_time,
                            exit_time=idx,
                            entry_price=entry_price,
                            exit_price=exec_exit_price,
                            units=units,
                            dollar_risk=dollar_risk * (1.0 - (take_profit_ratio if tp_hit else 0.0)),
                            pnl=net_pnl,
                            return_pct=ret_pct,
                            exit_reason="ATR Trailing Stop",
                            side="LONG",
                            strategy_id="momentum",
                        )
                    )
                    in_position = False
                    units = 0.0
                else:
                    trailing_stop = risk_engine.update_trailing_stop(
                        current_price=close,
                        current_atr=atr,
                        atr_multiplier=atr_multiplier,
                        existing_stop=trailing_stop,
                        is_short=False,
                    )

            elif pos_side == "SHORT":
                # Partial TP check
                if take_profit_atr_mult > 0 and not tp_hit and low <= tp_target:
                    exec_tp_price = tp_target * (1.0 + slippage_pct)
                    tp_units = units * take_profit_ratio
                    exit_fee = (tp_units * exec_tp_price) * fee_pct
                    total_fees += exit_fee
                    gross_pnl = (entry_price - exec_tp_price) * tp_units
                    net_pnl = gross_pnl - exit_fee
                    equity += net_pnl
                    ret_pct = (entry_price - exec_tp_price) / entry_price

                    trades.append(
                        TradeRecord(
                            entry_time=entry_time,
                            exit_time=idx,
                            entry_price=entry_price,
                            exit_price=exec_tp_price,
                            units=tp_units,
                            dollar_risk=dollar_risk * take_profit_ratio,
                            pnl=net_pnl,
                            return_pct=ret_pct,
                            exit_reason=f"Take Profit ({int(take_profit_ratio*100)}% Scale-Out)",
                            side="SHORT",
                            strategy_id="momentum",
                        )
                    )
                    units -= tp_units
                    tp_hit = True
                    trailing_stop = min(trailing_stop, entry_price)

                # Trailing stop check
                if high >= trailing_stop:
                    exec_exit_price = max(trailing_stop, close) * (1.0 + slippage_pct)
                    exit_fee = (units * exec_exit_price) * fee_pct
                    total_fees += exit_fee
                    gross_pnl = (entry_price - exec_exit_price) * units
                    net_pnl = gross_pnl - exit_fee
                    equity += net_pnl
                    ret_pct = (entry_price - exec_exit_price) / entry_price

                    trades.append(
                        TradeRecord(
                            entry_time=entry_time,
                            exit_time=idx,
                            entry_price=entry_price,
                            exit_price=exec_exit_price,
                            units=units,
                            dollar_risk=dollar_risk * (1.0 - (take_profit_ratio if tp_hit else 0.0)),
                            pnl=net_pnl,
                            return_pct=ret_pct,
                            exit_reason="ATR Trailing Stop",
                            side="SHORT",
                            strategy_id="momentum",
                        )
                    )
                    in_position = False
                    units = 0.0
                else:
                    trailing_stop = risk_engine.update_trailing_stop(
                        current_price=close,
                        current_atr=atr,
                        atr_multiplier=atr_multiplier,
                        existing_stop=trailing_stop,
                        is_short=True,
                    )

        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
        if dd > max_drawdown:
            max_drawdown = dd

        equity_curve.append(equity)

    total_trades = len(trades)
    winning_trades = sum(1 for t in trades if t.pnl > 0)
    losing_trades = sum(1 for t in trades if t.pnl <= 0)
    win_rate = (winning_trades / total_trades) if total_trades > 0 else 0.0

    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

    total_return_pct = ((equity - initial_capital) / initial_capital) * 100.0

    eq_series = pd.Series(equity_curve)
    pct_returns = eq_series.pct_change().dropna()
    mean_ret = pct_returns.mean()
    std_ret = pct_returns.std()
    bars_per_year = _estimate_bars_per_year(data)
    sharpe = (mean_ret / std_ret * np.sqrt(bars_per_year)) if std_ret > 0 else 0.0

    return BacktestResult(
        initial_capital=initial_capital,
        final_equity=equity,
        total_return_pct=total_return_pct,
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate=win_rate,
        profit_factor=profit_factor,
        max_drawdown_pct=max_drawdown * 100.0,
        sharpe_ratio=sharpe,
        total_fees_paid=total_fees,
        trades=trades,
        equity_curve=equity_curve,
    )


def run_mean_reversion_backtest(
    df: pd.DataFrame,
    initial_capital: float = 10000.0,
    account_risk_pct: float = 0.02,
    bb_period: int = 20,
    bb_std: float = 2.0,
    rsi_period: int = 14,
    rsi_oversold: float = 30.0,
    rsi_overbought: float = 70.0,
    atr_period: int = 14,
    atr_stop_mult: float = 2.0,
    adx_period: int = 14,
    adx_threshold: float = 25.0,
    max_hold_bars: int = 48,
    scale_out_ratio: float = 0.5,
    allow_short: bool = False,
    fee_pct: float = 0.0006,
    slippage_pct: float = 0.0004,
) -> BacktestResult:
    """Run Mean Reversion (Bollinger Bands + RSI + ADX filter) backtest simulation."""
    data = compute_mean_reversion_indicators(
        df,
        bb_period=bb_period,
        bb_std=bb_std,
        rsi_period=rsi_period,
        atr_period=atr_period,
        adx_period=adx_period,
    )

    risk_engine = RiskEngine(default_risk_pct=account_risk_pct)

    equity = initial_capital
    peak_equity = initial_capital
    max_drawdown = 0.0
    total_fees = 0.0

    in_position = False
    pos_side = "LONG"
    entry_price = 0.0
    entry_time = None
    bars_held = 0
    units = 0.0
    dollar_risk = 0.0
    stop_loss = 0.0
    tp_hit = False

    trades: List[TradeRecord] = []
    equity_curve: List[float] = [equity]

    for idx, row in data.iterrows():
        close = row["close"]
        high = row["high"]
        low = row["low"]
        atr = row["atr"]
        rsi = row["rsi"]
        bb_sma = row["bb_sma"]
        bb_upper = row["bb_upper"]
        bb_lower = row["bb_lower"]
        adx = row["adx"]

        if pd.isna(atr) or pd.isna(rsi) or pd.isna(bb_sma) or pd.isna(adx):
            equity_curve.append(equity)
            continue

        if not in_position:
            # Check range-bound regime
            if adx <= adx_threshold:
                # Long entry: oversold bounce setup
                if close <= bb_lower and rsi < rsi_oversold:
                    pos_side = "LONG"
                    exec_entry_price = close * (1.0 + slippage_pct)
                    units, initial_stop, dollar_risk = risk_engine.calculate_position(
                        equity=equity,
                        entry_price=exec_entry_price,
                        atr=atr,
                        atr_multiplier=atr_stop_mult,
                        risk_pct=account_risk_pct,
                        is_short=False,
                    )
                    max_allowed_units = (equity * 0.95) / exec_entry_price
                    units = min(units, max_allowed_units)

                    if units > 0:
                        entry_fee = (units * exec_entry_price) * fee_pct
                        total_fees += entry_fee
                        equity -= entry_fee
                        in_position = True
                        entry_price = exec_entry_price
                        entry_time = idx
                        bars_held = 0
                        stop_loss = initial_stop
                        tp_hit = False

                # Short entry: overbought reversal setup
                elif allow_short and close >= bb_upper and rsi > rsi_overbought:
                    pos_side = "SHORT"
                    exec_entry_price = close * (1.0 - slippage_pct)
                    units, initial_stop, dollar_risk = risk_engine.calculate_position(
                        equity=equity,
                        entry_price=exec_entry_price,
                        atr=atr,
                        atr_multiplier=atr_stop_mult,
                        risk_pct=account_risk_pct,
                        is_short=True,
                    )
                    max_allowed_units = (equity * 0.95) / exec_entry_price
                    units = min(units, max_allowed_units)

                    if units > 0:
                        entry_fee = (units * exec_entry_price) * fee_pct
                        total_fees += entry_fee
                        equity -= entry_fee
                        in_position = True
                        entry_price = exec_entry_price
                        entry_time = idx
                        bars_held = 0
                        stop_loss = initial_stop
                        tp_hit = False

        else:
            bars_held += 1
            exit_triggered = False
            exit_price = 0.0
            exit_reason = ""

            if pos_side == "LONG":
                # 0. Partial Take-Profit check (halfway to SMA)
                halfway = entry_price + (bb_sma - entry_price) * 0.5
                if scale_out_ratio > 0 and not tp_hit and high >= halfway:
                    exec_tp_price = halfway * (1.0 - slippage_pct)
                    tp_units = units * scale_out_ratio
                    exit_fee = (tp_units * exec_tp_price) * fee_pct
                    total_fees += exit_fee
                    gross_pnl = (exec_tp_price - entry_price) * tp_units
                    net_pnl = gross_pnl - exit_fee
                    equity += net_pnl
                    ret_pct = (exec_tp_price - entry_price) / entry_price

                    trades.append(
                        TradeRecord(
                            entry_time=entry_time,
                            exit_time=idx,
                            entry_price=entry_price,
                            exit_price=exec_tp_price,
                            units=tp_units,
                            dollar_risk=dollar_risk * scale_out_ratio,
                            pnl=net_pnl,
                            return_pct=ret_pct,
                            exit_reason=f"Halfway Take Profit ({int(scale_out_ratio*100)}% Scale-Out)",
                            side="LONG",
                            strategy_id="mean_reversion",
                        )
                    )
                    units -= tp_units
                    tp_hit = True

                # 1. Stop loss hit
                if low <= stop_loss:
                    exit_triggered = True
                    exit_price = min(stop_loss, close) * (1.0 - slippage_pct)
                    exit_reason = "Stop Loss Hit"
                # 2. Mean target (SMA) reached
                elif high >= bb_sma:
                    exit_triggered = True
                    exit_price = bb_sma * (1.0 - slippage_pct)
                    exit_reason = "Mean Target Hit (SMA)"
                # 3. Max holding period elapsed
                elif max_hold_bars > 0 and bars_held >= max_hold_bars:
                    exit_triggered = True
                    exit_price = close * (1.0 - slippage_pct)
                    exit_reason = f"Max Hold Bars ({max_hold_bars})"

                if exit_triggered:
                    exit_fee = (units * exit_price) * fee_pct
                    total_fees += exit_fee
                    gross_pnl = (exit_price - entry_price) * units
                    net_pnl = gross_pnl - exit_fee
                    equity += net_pnl
                    ret_pct = (exit_price - entry_price) / entry_price

                    trades.append(
                        TradeRecord(
                            entry_time=entry_time,
                            exit_time=idx,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            units=units,
                            dollar_risk=dollar_risk * (1.0 - (scale_out_ratio if tp_hit else 0.0)),
                            pnl=net_pnl,
                            return_pct=ret_pct,
                            exit_reason=exit_reason,
                            side="LONG",
                            strategy_id="mean_reversion",
                        )
                    )
                    in_position = False
                    units = 0.0

            elif pos_side == "SHORT":
                # 0. Partial Take-Profit check (halfway to SMA)
                halfway = entry_price - (entry_price - bb_sma) * 0.5
                if scale_out_ratio > 0 and not tp_hit and low <= halfway:
                    exec_tp_price = halfway * (1.0 + slippage_pct)
                    tp_units = units * scale_out_ratio
                    exit_fee = (tp_units * exec_tp_price) * fee_pct
                    total_fees += exit_fee
                    gross_pnl = (entry_price - exec_tp_price) * tp_units
                    net_pnl = gross_pnl - exit_fee
                    equity += net_pnl
                    ret_pct = (entry_price - exec_tp_price) / entry_price

                    trades.append(
                        TradeRecord(
                            entry_time=entry_time,
                            exit_time=idx,
                            entry_price=entry_price,
                            exit_price=exec_tp_price,
                            units=tp_units,
                            dollar_risk=dollar_risk * scale_out_ratio,
                            pnl=net_pnl,
                            return_pct=ret_pct,
                            exit_reason=f"Halfway Take Profit ({int(scale_out_ratio*100)}% Scale-Out)",
                            side="SHORT",
                            strategy_id="mean_reversion",
                        )
                    )
                    units -= tp_units
                    tp_hit = True

                # 1. Stop loss hit
                if high >= stop_loss:
                    exit_triggered = True
                    exit_price = max(stop_loss, close) * (1.0 + slippage_pct)
                    exit_reason = "Stop Loss Hit"
                # 2. Mean target reached
                elif low <= bb_sma:
                    exit_triggered = True
                    exit_price = bb_sma * (1.0 + slippage_pct)
                    exit_reason = "Mean Target Hit (SMA)"
                # 3. Max hold bars
                elif max_hold_bars > 0 and bars_held >= max_hold_bars:
                    exit_triggered = True
                    exit_price = close * (1.0 + slippage_pct)
                    exit_reason = f"Max Hold Bars ({max_hold_bars})"

                if exit_triggered:
                    exit_fee = (units * exit_price) * fee_pct
                    total_fees += exit_fee
                    gross_pnl = (entry_price - exit_price) * units
                    net_pnl = gross_pnl - exit_fee
                    equity += net_pnl
                    ret_pct = (entry_price - exit_price) / entry_price

                    trades.append(
                        TradeRecord(
                            entry_time=entry_time,
                            exit_time=idx,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            units=units,
                            dollar_risk=dollar_risk * (1.0 - (scale_out_ratio if tp_hit else 0.0)),
                            pnl=net_pnl,
                            return_pct=ret_pct,
                            exit_reason=exit_reason,
                            side="SHORT",
                            strategy_id="mean_reversion",
                        )
                    )
                    in_position = False
                    units = 0.0

        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
        if dd > max_drawdown:
            max_drawdown = dd

        equity_curve.append(equity)

    total_trades = len(trades)
    winning_trades = sum(1 for t in trades if t.pnl > 0)
    losing_trades = sum(1 for t in trades if t.pnl <= 0)
    win_rate = (winning_trades / total_trades) if total_trades > 0 else 0.0

    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

    total_return_pct = ((equity - initial_capital) / initial_capital) * 100.0

    eq_series = pd.Series(equity_curve)
    pct_returns = eq_series.pct_change().dropna()
    mean_ret = pct_returns.mean()
    std_ret = pct_returns.std()
    bars_per_year = _estimate_bars_per_year(data)
    sharpe = (mean_ret / std_ret * np.sqrt(bars_per_year)) if std_ret > 0 else 0.0

    return BacktestResult(
        initial_capital=initial_capital,
        final_equity=equity,
        total_return_pct=total_return_pct,
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate=win_rate,
        profit_factor=profit_factor,
        max_drawdown_pct=max_drawdown * 100.0,
        sharpe_ratio=sharpe,
        total_fees_paid=total_fees,
        trades=trades,
        equity_curve=equity_curve,
    )
