"""Event-driven backtesting engine with realistic fees, slippage, and performance metrics."""

from dataclasses import dataclass, field
from typing import List, Dict, Any
import numpy as np
import pandas as pd

from core.risk_engine import RiskEngine
from core.strategy import compute_indicators


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
    trades: List[TradeRecord] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)


def run_backtest(
    df: pd.DataFrame,
    initial_capital: float = 10000.0,
    account_risk_pct: float = 0.02,
    breakout_lookback: int = 24,
    ema_trend_filter: int = 200,
    atr_period: int = 14,
    atr_multiplier: float = 2.5,
    fee_pct: float = 0.0006,
    slippage_pct: float = 0.0004,
) -> BacktestResult:
    """
    Run a full backtest with realistic fees, slippage, and dynamic ATR trailing stops.
    """
    # 1. Compute indicators
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

    in_position = False
    entry_price = 0.0
    entry_time = None
    units = 0.0
    dollar_risk = 0.0
    trailing_stop = 0.0

    trades: List[TradeRecord] = []
    equity_curve: List[float] = [equity]

    # Iterate bar by bar
    for idx, row in data.iterrows():
        close = row["close"]
        high = row["high"]
        low = row["low"]
        atr = row["atr"]

        if pd.isna(atr) or pd.isna(row["ema_filter"]):
            equity_curve.append(equity)
            continue

        if not in_position:
            # Check for breakout entry signal
            if row["entry_signal"]:
                # Account for entry slippage and fee
                exec_entry_price = close * (1.0 + slippage_pct)
                units, initial_stop, dollar_risk = risk_engine.calculate_position(
                    equity=equity,
                    entry_price=exec_entry_price,
                    atr=atr,
                    atr_multiplier=atr_multiplier,
                    risk_pct=account_risk_pct,
                )

                # Cap position size so it doesn't exceed available equity
                max_allowed_units = (equity * 0.95) / exec_entry_price
                units = min(units, max_allowed_units)

                if units > 0:
                    entry_fee = (units * exec_entry_price) * fee_pct
                    equity -= entry_fee

                    in_position = True
                    entry_price = exec_entry_price
                    entry_time = idx
                    trailing_stop = initial_stop
        else:
            # In position: check if trailing stop was hit
            if low <= trailing_stop:
                # Stopped out
                exec_exit_price = min(trailing_stop, close) * (1.0 - slippage_pct)
                exit_fee = (units * exec_exit_price) * fee_pct
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
                        dollar_risk=dollar_risk,
                        pnl=net_pnl,
                        return_pct=ret_pct,
                        exit_reason="ATR Trailing Stop",
                    )
                )

                in_position = False
                units = 0.0
            else:
                # Ratchet the trailing stop upward
                trailing_stop = risk_engine.update_trailing_stop(
                    current_price=close,
                    current_atr=atr,
                    atr_multiplier=atr_multiplier,
                    existing_stop=trailing_stop,
                )

        # Track drawdown
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
        if dd > max_drawdown:
            max_drawdown = dd

        equity_curve.append(equity)

    # Performance calculation
    total_trades = len(trades)
    winning_trades = sum(1 for t in trades if t.pnl > 0)
    losing_trades = sum(1 for t in trades if t.pnl <= 0)
    win_rate = (winning_trades / total_trades) if total_trades > 0 else 0.0

    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

    total_return_pct = ((equity - initial_capital) / initial_capital) * 100.0

    # Annualized Sharpe (hourly bars)
    eq_series = pd.Series(equity_curve)
    pct_returns = eq_series.pct_change().dropna()
    mean_ret = pct_returns.mean()
    std_ret = pct_returns.std()
    sharpe = (mean_ret / std_ret * np.sqrt(24 * 365)) if std_ret > 0 else 0.0

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
        trades=trades,
        equity_curve=equity_curve,
    )
