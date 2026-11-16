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
    allow_long: bool = True,
    allow_short: bool = True,
    fee_pct: float = 0.0006,
    slippage_pct: float = 0.0004,
    fill_mode: str = "close",
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
    pending_signal = None  # (side, signal_atr) for next_open fill_mode

    trades: List[TradeRecord] = []
    equity_curve: List[float] = [equity]

    for idx, row in data.iterrows():
        close = row["close"]
        high = row["high"]
        low = row["low"]
        row_open = row.get("open", close)
        atr = row["atr"]

        if pd.isna(atr) or pd.isna(row["ema_filter"]):
            equity_curve.append(equity)
            continue

        # Execute pending signal from previous bar under next_open fill mode
        if not in_position and fill_mode == "next_open" and pending_signal is not None:
            sig_side, sig_atr = pending_signal
            pending_signal = None
            if sig_side == "LONG" and allow_long:
                pos_side = "LONG"
                exec_entry_price = row_open * (1.0 + slippage_pct)
                units, initial_stop, dollar_risk = risk_engine.calculate_position(
                    equity=equity,
                    entry_price=exec_entry_price,
                    atr=sig_atr,
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
                        tp_target = entry_price + (take_profit_atr_mult * sig_atr)
            elif sig_side == "SHORT" and allow_short:
                pos_side = "SHORT"
                exec_entry_price = row_open * (1.0 - slippage_pct)
                units, initial_stop, dollar_risk = risk_engine.calculate_position(
                    equity=equity,
                    entry_price=exec_entry_price,
                    atr=sig_atr,
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
                        tp_target = entry_price - (take_profit_atr_mult * sig_atr)

        if not in_position:
            # Check Long signal
            if allow_long and row.get("long_signal", row.get("entry_signal", False)):
                if fill_mode == "next_open":
                    pending_signal = ("LONG", atr)
                else:
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
                if fill_mode == "next_open":
                    pending_signal = ("SHORT", atr)
                else:
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
    allow_long: bool = True,
    fee_pct: float = 0.0006,
    slippage_pct: float = 0.0004,
    fill_mode: str = "close",
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
    pending_signal = None  # (side, signal_atr) for next_open fill_mode

    trades: List[TradeRecord] = []
    equity_curve: List[float] = [equity]

    for idx, row in data.iterrows():
        close = row["close"]
        high = row["high"]
        low = row["low"]
        row_open = row.get("open", close)
        atr = row["atr"]
        rsi = row["rsi"]
        bb_sma = row["bb_sma"]
        bb_upper = row["bb_upper"]
        bb_lower = row["bb_lower"]
        adx = row["adx"]

        if pd.isna(atr) or pd.isna(rsi) or pd.isna(bb_sma) or pd.isna(adx):
            equity_curve.append(equity)
            continue

        # Execute pending signal from previous bar under next_open fill mode
        if not in_position and fill_mode == "next_open" and pending_signal is not None:
            sig_side, sig_atr = pending_signal
            pending_signal = None
            if sig_side == "LONG" and allow_long:
                pos_side = "LONG"
                exec_entry_price = row_open * (1.0 + slippage_pct)
                units, initial_stop, dollar_risk = risk_engine.calculate_position(
                    equity=equity,
                    entry_price=exec_entry_price,
                    atr=sig_atr,
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
            elif sig_side == "SHORT" and allow_short:
                pos_side = "SHORT"
                exec_entry_price = row_open * (1.0 - slippage_pct)
                units, initial_stop, dollar_risk = risk_engine.calculate_position(
                    equity=equity,
                    entry_price=exec_entry_price,
                    atr=sig_atr,
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

        if not in_position:
            # Check range-bound regime
            if adx <= adx_threshold:
                # Long entry: oversold bounce setup
                if allow_long and close <= bb_lower and rsi < rsi_oversold:
                    if fill_mode == "next_open":
                        pending_signal = ("LONG", atr)
                    else:
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
                    if fill_mode == "next_open":
                        pending_signal = ("SHORT", atr)
                    else:
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
            round_trip_cost_pct = 2.0 * (fee_pct + slippage_pct)

            if pos_side == "LONG":
                min_long_profit = entry_price * (1.0 + round_trip_cost_pct)
                # 0. Partial Take-Profit check (halfway to SMA, only when profitable above friction)
                halfway = entry_price + (bb_sma - entry_price) * 0.5
                if scale_out_ratio > 0 and not tp_hit and bb_sma > min_long_profit and halfway >= min_long_profit and high >= halfway:
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
                # 2. Mean target (SMA) reached (only when SMA is profitable above entry + friction)
                elif bb_sma >= min_long_profit and high >= bb_sma:
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
                max_short_profit = entry_price * (1.0 - round_trip_cost_pct)
                # 0. Partial Take-Profit check (halfway to SMA, only when profitable below friction)
                halfway = entry_price - (entry_price - bb_sma) * 0.5
                if scale_out_ratio > 0 and not tp_hit and bb_sma < max_short_profit and halfway <= max_short_profit and low <= halfway:
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
                # 2. Mean target reached (only when SMA is profitable below entry - friction)
                elif bb_sma <= max_short_profit and low <= bb_sma:
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


def run_pairs_backtest(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    lookback: int = 48,
    entry_z: float = 2.0,
    exit_z: float = 0.5,
    stop_z: float = 3.5,
    min_edge_cost_ratio: float = 0.0,
    allocation_pct: float = 0.30,
    initial_capital: float = 10000.0,
    fee_pct: float = 0.0006,
    slippage_pct: float = 0.0004,
) -> BacktestResult:
    """Market-neutral Stat-Arb pairs backtest (rolling beta + rolling Z-score).

    Mirrors the live ``StatArbPairsStrategy`` decision rules:
      * Enter LONG_A_SHORT_B when z <= -entry_z, SHORT_A_LONG_B when z >= entry_z.
      * Optional cost gate: expected reversion edge must be >= ratio x round-trip friction.
      * Exit at |z| <= exit_z (target) or |z| >= stop_z (stop).
    Fees and slippage are charged on both legs, both sides. Equity is marked to market
    every bar so max drawdown reflects open-position risk.
    """
    common_idx = df_a.index.intersection(df_b.index)
    series_a = df_a.loc[common_idx, "close"].astype(float)
    series_b = df_b.loc[common_idx, "close"].astype(float)
    lookback = int(lookback)

    equity = initial_capital
    peak_equity = initial_capital
    max_drawdown = 0.0
    total_fees = 0.0
    trades: List[TradeRecord] = []
    equity_curve: List[float] = [equity]
    position: Optional[Dict[str, Any]] = None
    cost_per_side = fee_pct + slippage_pct

    if len(common_idx) < lookback + 10:
        return BacktestResult(
            initial_capital=initial_capital, final_equity=equity, total_return_pct=0.0,
            total_trades=0, winning_trades=0, losing_trades=0, win_rate=0.0,
            profit_factor=0.0, max_drawdown_pct=0.0, sharpe_ratio=0.0,
            equity_curve=equity_curve,
        )

    arr_a = series_a.values
    arr_b = series_b.values
    prev_z = 0.0

    for i in range(lookback, len(common_idx)):
        win_a = arr_a[i - lookback:i]
        win_b = arr_b[i - lookback:i]
        var_b = float(np.var(win_b))
        beta = float(np.cov(win_a, win_b)[0, 1] / var_b) if var_b > 0 else 1.0
        if beta <= 0.01:
            prev_z = 0.0
            continue
        beta = min(beta, 10.0)
        spread_win = win_a - beta * win_b
        mu = float(spread_win.mean())
        sigma = float(spread_win.std(ddof=1))
        cur_a = float(arr_a[i])
        cur_b = float(arr_b[i])
        z = (cur_a - beta * cur_b - mu) / sigma if sigma > 0 else 0.0

        unrealized = 0.0
        if position is not None:
            sign = 1.0 if position["type"] == "LONG_A_SHORT_B" else -1.0
            pnl_a = sign * (cur_a - position["entry_a"]) * position["units_a"]
            pnl_b = -sign * (cur_b - position["entry_b"]) * position["units_b"]
            unrealized = pnl_a + pnl_b
            exit_notional = cur_a * position["units_a"] + cur_b * position["units_b"]
            exit_cost = exit_notional * cost_per_side
            net = unrealized - position["entry_cost"] - exit_cost

            locked_sp = cur_a - position["beta"] * cur_b
            locked_z = (locked_sp - position["mu"]) / position["sigma"] if position["sigma"] > 0 else z
            bars_held = i - position["entry_bar"]

            reason = ""
            if ((sign > 0 and locked_z >= -exit_z) or (sign < 0 and locked_z <= exit_z) or abs(z) <= exit_z) and net > 0.0:
                reason = "TARGET_REVERSION"
            elif abs(locked_z) >= stop_z or abs(z) >= stop_z or bars_held >= 72:
                reason = "STOP_Z"
            if reason:
                total_fees += exit_cost
                equity += unrealized - exit_cost
                trades.append(TradeRecord(
                    entry_time=position["entry_time"], exit_time=common_idx[i],
                    entry_price=position["entry_a"], exit_price=cur_a,
                    units=position["units_a"], dollar_risk=position["notional"],
                    pnl=net, return_pct=(net / position["notional"]) * 100.0 if position["notional"] else 0.0,
                    exit_reason=reason,
                    side="LONG" if sign > 0 else "SHORT",
                    strategy_id="stat_arb_pairs",
                ))
                position = None
                unrealized = 0.0

        elif position is None and sigma > 0:
            p_corr = float(np.corrcoef(win_a, win_b)[0, 1]) if np.std(win_a) > 0 and np.std(win_b) > 0 else 0.0
            ra = np.diff(win_a) / np.maximum(1e-12, win_a[:-1])
            rb = np.diff(win_b) / np.maximum(1e-12, win_b[:-1])
            r_corr = float(np.corrcoef(ra, rb)[0, 1]) if len(ra) > 5 and np.std(ra) > 1e-12 and np.std(rb) > 1e-12 else p_corr

            if p_corr >= 0.70 and r_corr >= 0.55:
                hook_floor = max(exit_z + 0.5, 1.4)
                long_hook = (-stop_z < prev_z <= -entry_z) and (prev_z < z <= -hook_floor)
                short_hook = (entry_z <= prev_z < stop_z) and (hook_floor <= z < prev_z)
                direct_trig = entry_z <= abs(z) < stop_z
                trig = (long_hook or short_hook) if len(common_idx) >= 120 else direct_trig
                if trig:
                    accept = True
                    if min_edge_cost_ratio > 0:
                        edge = max(0.0, abs(z) - exit_z) * sigma
                        rt_cost = 2.0 * cost_per_side * (cur_a + beta * cur_b)
                        accept = rt_cost > 0 and (edge / rt_cost) >= min_edge_cost_ratio
                    if accept:
                        notional = equity * allocation_pct
                        units_a = notional / (cur_a + beta * cur_b)
                        units_b = beta * units_a
                        entry_cost = notional * cost_per_side
                        total_fees += entry_cost
                        equity -= entry_cost
                        is_long_a = long_hook if len(common_idx) >= 120 else (z <= -entry_z)
                        position = {
                            "type": "LONG_A_SHORT_B" if is_long_a else "SHORT_A_LONG_B",
                            "entry_a": cur_a, "entry_b": cur_b,
                            "units_a": units_a, "units_b": units_b,
                            "beta": beta, "mu": mu, "sigma": sigma,
                            "entry_cost": entry_cost, "notional": notional,
                            "entry_time": common_idx[i], "entry_bar": i,
                        }
        prev_z = z

        mtm = equity + unrealized
        if mtm > peak_equity:
            peak_equity = mtm
        dd = (peak_equity - mtm) / peak_equity if peak_equity > 0 else 0.0
        max_drawdown = max(max_drawdown, dd)
        equity_curve.append(mtm)

    total_trades = len(trades)
    winning = sum(1 for t in trades if t.pnl > 0)
    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
    final_equity = equity_curve[-1]
    pct_returns = pd.Series(equity_curve).pct_change().dropna()
    std_ret = pct_returns.std()
    bars_per_year = _estimate_bars_per_year(pd.DataFrame(index=common_idx))
    sharpe = (pct_returns.mean() / std_ret * np.sqrt(bars_per_year)) if std_ret and std_ret > 0 else 0.0

    return BacktestResult(
        initial_capital=initial_capital,
        final_equity=final_equity,
        total_return_pct=((final_equity - initial_capital) / initial_capital) * 100.0,
        total_trades=total_trades,
        winning_trades=winning,
        losing_trades=total_trades - winning,
        win_rate=(winning / total_trades) if total_trades else 0.0,
        profit_factor=profit_factor,
        max_drawdown_pct=max_drawdown * 100.0,
        sharpe_ratio=float(sharpe),
        total_fees_paid=total_fees,
        trades=trades,
        equity_curve=equity_curve,
    )


def run_basis_yield_backtest(
    df: pd.DataFrame,
    hurdle_apr: float = 8.0,
    max_basis_allocation_pct: float = 0.30,
    initial_capital: float = 10000.0,
    fee_pct: float = 0.0006,
    slippage_pct: float = 0.0004,
) -> BacktestResult:
    """Delta-neutral perpetual basis / funding-rate yield backtest on 1h bars.

    Models 8-hour funding epochs from spot basis premium relative to its 24-bar EMA
    plus delta-hedging rebalance friction proportional to realized 8h spot movement
    and entry/exit leg costs when toggling the carry position on/off.
    """
    if df is None or len(df) < 32:
        return BacktestResult(
            initial_capital=initial_capital, final_equity=initial_capital, total_return_pct=0.0,
            total_trades=0, winning_trades=0, losing_trades=0, win_rate=0.0,
            profit_factor=0.0, max_drawdown_pct=0.0, sharpe_ratio=0.0,
            equity_curve=[initial_capital],
        )

    close = df["close"].astype(float)
    ema24 = close.ewm(span=24, adjust=False).mean()
    basis_prem = ((close - ema24) / ema24).clip(-0.05, 0.05)

    equity = initial_capital
    peak_equity = initial_capital
    max_drawdown = 0.0
    total_fees = 0.0
    trades: List[TradeRecord] = []
    equity_curve: List[float] = [equity]
    cost_per_side = fee_pct + slippage_pct
    in_carry = False

    closes = close.values
    prems = basis_prem.values
    idx = df.index

    for i in range(24, len(df), 8):
        # Implied 8h funding rate: 1bp baseline + basis premium sensitivity
        rate_8h = 0.0001 + 0.012 * float(prems[i])
        annualized_apr = rate_8h * 3.0 * 365.0 * 100.0
        move_8h = abs(closes[i] - closes[i - 8]) / closes[i - 8] if closes[i - 8] > 0 else 0.0

        if annualized_apr >= hurdle_apr:
            alloc_pct = min(max_basis_allocation_pct, 0.25 if annualized_apr >= 25.0 else 0.15)
            notional = equity * alloc_pct
            # Entry cost if opening carry + ongoing delta-rebalance friction during volatile 8h swings
            entry_cost = (notional * cost_per_side * 0.5) if not in_carry else 0.0
            rebal_cost = notional * cost_per_side * (move_8h * 2.5)
            friction = entry_cost + rebal_cost
            gross_yield = notional * rate_8h
            net_pnl = gross_yield - friction
            total_fees += friction
            equity += net_pnl
            in_carry = True
            trades.append(TradeRecord(
                entry_time=idx[i - 8], exit_time=idx[i],
                entry_price=float(closes[i - 8]), exit_price=float(closes[i]),
                units=notional, dollar_risk=friction,
                pnl=net_pnl, return_pct=(net_pnl / notional) * 100.0 if notional > 0 else 0.0,
                exit_reason=f"BASIS_YIELD ({annualized_apr:.1f}% APR)",
                side="LONG", strategy_id="basis_yield",
            ))
        else:
            if in_carry:
                exit_cost = (equity * min(max_basis_allocation_pct, 0.15)) * cost_per_side * 0.25
                total_fees += exit_cost
                equity -= exit_cost
                in_carry = False

        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
        max_drawdown = max(max_drawdown, dd)
        equity_curve.append(equity)

    total_trades = len(trades)
    winning = sum(1 for t in trades if t.pnl > 0)
    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
    pct_returns = pd.Series(equity_curve).pct_change().dropna()
    std_ret = pct_returns.std()
    sharpe = (pct_returns.mean() / std_ret * np.sqrt(365.25 * 3.0)) if std_ret and std_ret > 0 else 0.0

    return BacktestResult(
        initial_capital=initial_capital,
        final_equity=equity,
        total_return_pct=((equity - initial_capital) / initial_capital) * 100.0,
        total_trades=total_trades,
        winning_trades=winning,
        losing_trades=total_trades - winning,
        win_rate=(winning / total_trades) if total_trades else 0.0,
        profit_factor=profit_factor,
        max_drawdown_pct=max_drawdown * 100.0,
        sharpe_ratio=float(sharpe),
        total_fees_paid=total_fees,
        trades=trades,
        equity_curve=equity_curve,
    )


def _run_signal_array_backtest(
    df: pd.DataFrame,
    entries: np.ndarray,
    atr_arr: np.ndarray,
    initial_capital: float = 10000.0,
    account_risk_pct: float = 0.02,
    stop_atr_mult: float = 2.0,
    tp_atr_mult: float = 2.5,
    fee_pct: float = 0.0002,
    slippage_pct: float = 0.0001,
) -> BacktestResult:
    """Shared causal long-only backtest simulator for boolean entry signal arrays."""
    equity = float(initial_capital)
    peak = equity
    max_dd = 0.0
    total_fees = 0.0
    trades: List[TradeRecord] = []
    equity_curve: List[float] = [equity]
    pos = None

    closes = df["close"].astype(float).values
    highs = df["high"].astype(float).values
    lows = df["low"].astype(float).values

    for i in range(1, len(df)):
        if pos is not None:
            ep, units, stop, tp, e_time, e_fee = pos
            hit_stop = lows[i] <= stop
            hit_tp = highs[i] >= tp
            if hit_stop or hit_tp:
                xp = (stop if hit_stop else tp) * (1.0 - slippage_pct)
                x_fee = xp * units * fee_pct
                total_fees += x_fee
                pnl = (xp - ep) * units - e_fee - x_fee
                equity += pnl
                trades.append(TradeRecord(
                    entry_time=e_time,
                    exit_time=df.index[i],
                    entry_price=ep,
                    exit_price=xp,
                    units=units,
                    pnl=pnl,
                    return_pct=((xp - ep) / ep * 100.0) if ep > 0 else 0.0,
                    exit_reason="Take Profit" if hit_tp else "Stop Loss",
                    side="LONG",
                    fees=e_fee + x_fee,
                ))
                pos = None

        if pos is None and bool(entries[i - 1]) and atr_arr[i - 1] > 0:
            ep = closes[i - 1] * (1.0 + slippage_pct)
            dist = float(atr_arr[i - 1]) * stop_atr_mult
            risk_clamped = max(0.01, min(0.05, float(account_risk_pct)))
            units = (equity * risk_clamped) / dist
            if units * ep > equity * 0.35:
                units = (equity * 0.35) / ep
            if units > 0:
                e_fee = ep * units * fee_pct
                total_fees += e_fee
                pos = (ep, units, max(0.0, ep - dist), ep + float(atr_arr[i - 1]) * tp_atr_mult, df.index[i - 1], e_fee)

        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        equity_curve.append(equity)

    total_trades = len(trades)
    winning = sum(1 for t in trades if t.pnl > 0)
    gp = sum(t.pnl for t in trades if t.pnl > 0)
    gl = abs(sum(t.pnl for t in trades if t.pnl < 0))
    pf = (gp / gl) if gl > 0 else (99.0 if gp > 0 else 0.0)
    t_rets = [t.pnl / initial_capital for t in trades]
    sharpe = (
        float(np.mean(t_rets) / np.std(t_rets) * np.sqrt(len(t_rets)))
        if len(t_rets) > 1 and np.std(t_rets) > 0
        else (1.0 if gp > 0 else 0.0)
    )
    return BacktestResult(
        initial_capital=initial_capital,
        final_equity=equity,
        total_return_pct=((equity - initial_capital) / initial_capital) * 100.0,
        total_trades=total_trades,
        winning_trades=winning,
        losing_trades=total_trades - winning,
        win_rate=(winning / total_trades) if total_trades else 0.0,
        profit_factor=pf,
        max_drawdown_pct=max_dd * 100.0,
        sharpe_ratio=sharpe,
        total_fees_paid=total_fees,
        trades=trades,
        equity_curve=equity_curve,
    )


def run_volatility_squeeze_backtest(
    df: pd.DataFrame,
    initial_capital: float = 10000.0,
    account_risk_pct: float = 0.02,
    stop_atr_mult: float = 2.0,
    tp_atr_mult: float = 2.5,
    fee_pct: float = 0.0002,
    slippage_pct: float = 0.0001,
) -> BacktestResult:
    """Backtest Volatility Squeeze Breakout strategy."""
    from strategies.volatility_squeeze import compute_squeeze_indicators
    if df is None or len(df) < 60:
        return BacktestResult(initial_capital=initial_capital, final_equity=initial_capital)
    ind = compute_squeeze_indicators(df)
    return _run_signal_array_backtest(
        df=df,
        entries=ind["squeeze_breakout"].fillna(False).values,
        atr_arr=ind["atr"].fillna(0.0).values,
        initial_capital=initial_capital,
        account_risk_pct=account_risk_pct,
        stop_atr_mult=stop_atr_mult,
        tp_atr_mult=tp_atr_mult,
        fee_pct=fee_pct,
        slippage_pct=slippage_pct,
    )


def run_trend_pullback_backtest(
    df: pd.DataFrame,
    initial_capital: float = 10000.0,
    account_risk_pct: float = 0.02,
    rsi_pullback_max: float = 44.0,
    stop_atr_mult: float = 2.0,
    tp_atr_mult: float = 2.5,
    fee_pct: float = 0.0002,
    slippage_pct: float = 0.0001,
) -> BacktestResult:
    """Backtest Trend Pullback continuation strategy."""
    from strategies.trend_pullback import compute_pullback_indicators
    if df is None or len(df) < 60:
        return BacktestResult(initial_capital=initial_capital, final_equity=initial_capital)
    ind = compute_pullback_indicators(df, rsi_pullback_max=rsi_pullback_max)
    return _run_signal_array_backtest(
        df=df,
        entries=ind["pullback_entry"].fillna(False).values,
        atr_arr=ind["atr"].fillna(0.0).values,
        initial_capital=initial_capital,
        account_risk_pct=account_risk_pct,
        stop_atr_mult=stop_atr_mult,
        tp_atr_mult=tp_atr_mult,
        fee_pct=fee_pct,
        slippage_pct=slippage_pct,
    )
