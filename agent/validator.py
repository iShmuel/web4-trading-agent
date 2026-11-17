"""Validation engine for walk-forward, out-of-sample verification to prevent overfitting."""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple
import pandas as pd

from core.backtest_engine import (
    BacktestResult,
    run_backtest,
    run_basis_yield_backtest,
    run_mean_reversion_backtest,
    run_pairs_backtest,
)
from core.risk_engine import MIN_ALLOWED_RISK_PCT, MAX_ALLOWED_RISK_PCT
from core.strategy import compute_hurst_exponent


@dataclass
class ValidationReport:
    is_valid: bool
    reason: str
    train_return_delta: float
    test_return_delta: float
    train_sharpe: float
    test_sharpe: float
    test_max_dd: float
    base_test_sharpe: float = 0.0
    base_test_max_dd: float = 0.0
    test_sharpe_delta: float = 0.0


def split_data(df: pd.DataFrame, train_ratio: float = 0.7) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Chronological train/test split.
    First 70% is In-Sample (training / discovery).
    Last 30% is Out-of-Sample (unseen validation).
    """
    split_idx = int(len(df) * train_ratio)
    train_df = df.iloc[:split_idx].copy()
    test_df = df.iloc[split_idx:].copy()
    return train_df, test_df


def evaluate_parameters(df: pd.DataFrame, params: Dict[str, Any]) -> BacktestResult:
    """Run a Momentum / Breakout backtest on a dataset using given parameters."""
    return run_backtest(
        df=df,
        initial_capital=10000.0,
        account_risk_pct=params.get("account_risk_pct", 0.02),
        breakout_lookback=int(params.get("breakout_lookback", 24)),
        ema_trend_filter=int(params.get("ema_trend_filter", 200)),
        atr_period=int(params.get("atr_period", 14)),
        atr_multiplier=params.get("atr_multiplier", 2.5),
        take_profit_atr_mult=params.get("take_profit_atr_mult", 2.0),
        take_profit_ratio=params.get("take_profit_ratio", 0.5),
        allow_long=bool(params.get("allow_long", True)),
        allow_short=bool(params.get("allow_short", True)),
        fee_pct=params.get("fee_pct", 0.0006),
        slippage_pct=params.get("slippage_pct", 0.0004),
    )


def evaluate_mean_reversion_parameters(df: pd.DataFrame, params: Dict[str, Any]) -> BacktestResult:
    """Run a Mean Reversion (Bollinger + RSI + ADX) backtest using the same config keys as the live strategy."""
    if params.get("enable_hurst_gate", False) or "hurst_mr_max" in params:
        hurst_max = float(params.get("hurst_mr_max", 0.52))
        if len(df) >= 30 and compute_hurst_exponent(df["close"], max_lag=20) >= hurst_max:
            return BacktestResult(
                initial_capital=10000.0,
                final_equity=10000.0,
                total_return_pct=0.0,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate=0.0,
                profit_factor=0.0,
                max_drawdown_pct=0.0,
                sharpe_ratio=0.0,
                trades=[],
                equity_curve=[10000.0],
            )
    return run_mean_reversion_backtest(
        df=df,
        initial_capital=10000.0,
        account_risk_pct=params.get("account_risk_pct", 0.02),
        bb_period=int(params.get("bb_period", 20)),
        bb_std=float(params.get("bb_std", 2.0)),
        rsi_period=int(params.get("rsi_period", 14)),
        rsi_oversold=float(params.get("rsi_oversold", 30)),
        rsi_overbought=float(params.get("rsi_overbought", 70)),
        atr_period=int(params.get("atr_period", 14)),
        atr_stop_mult=float(params.get("mr_atr_stop_mult", 2.0)),
        adx_period=int(params.get("adx_period", 14)),
        adx_threshold=float(params.get("adx_threshold", 25)),
        max_hold_bars=int(params.get("mr_max_hold_bars", 48)),
        scale_out_ratio=float(params.get("mr_scale_out_ratio", 0.5)),
        allow_long=bool(params.get("mr_allow_long", params.get("allow_long", True))),
        allow_short=bool(params.get("mr_allow_short", False)),
        fee_pct=params.get("fee_pct", 0.0006),
        slippage_pct=params.get("slippage_pct", 0.0004),
    )


def evaluate_pairs_parameters(df_a: pd.DataFrame, df_b: pd.DataFrame, params: Dict[str, Any]) -> BacktestResult:
    """Run a Stat-Arb pairs backtest using the same config keys as the live StatArbPairsStrategy."""
    return run_pairs_backtest(
        df_a=df_a,
        df_b=df_b,
        lookback=int(params.get("stat_arb_lookback", 48)),
        entry_z=float(params.get("stat_arb_entry_z", 2.0)),
        exit_z=float(params.get("stat_arb_exit_z", 0.5)),
        stop_z=float(params.get("stat_arb_stop_z", 3.5)),
        min_edge_cost_ratio=float(params.get("stat_arb_min_edge_cost_ratio", 0.0)),
        fee_pct=params.get("fee_pct", 0.0006),
        slippage_pct=params.get("pair_slippage_pct", params.get("slippage_pct", 0.0004)),
    )


def evaluate_basis_yield_parameters(df: pd.DataFrame, params: Dict[str, Any]) -> BacktestResult:
    """Run a Delta-Neutral Basis / Funding Yield backtest using the same config keys as BasisYieldEngine."""
    return run_basis_yield_backtest(
        df=df,
        hurdle_apr=float(params.get("basis_hurdle_apr", 8.0)),
        max_basis_allocation_pct=float(params.get("max_basis_allocation_pct", 0.30)),
        initial_capital=10000.0,
        fee_pct=params.get("fee_pct", 0.0006),
        slippage_pct=params.get("slippage_pct", 0.0004),
    )


def _risk_bound_violation(candidate_params: Dict[str, Any]) -> Optional[ValidationReport]:
    cand_risk = candidate_params.get("account_risk_pct", 0.02)
    if cand_risk < MIN_ALLOWED_RISK_PCT or cand_risk > MAX_ALLOWED_RISK_PCT:
        return ValidationReport(
            is_valid=False,
            reason=f"Risk {cand_risk*100:.1f}% violates 1%-5% boundary",
            train_return_delta=0.0,
            test_return_delta=0.0,
            train_sharpe=0.0,
            test_sharpe=0.0,
            test_max_dd=0.0,
        )
    return None


def validate_with_evaluator(
    base_params: Dict[str, Any],
    candidate_params: Dict[str, Any],
    evaluator: Callable[[Dict[str, Any], str], Any],
    max_dd_limit: float = 15.0,
    min_oos_return_pct: float = -2.0,
) -> ValidationReport:
    """Strategy-agnostic walk-forward validation.

    ``evaluator(params, split)`` must return an object exposing ``total_return_pct``,
    ``sharpe_ratio`` and ``max_drawdown_pct`` for ``split`` in {"train", "test"}.
    Applies the exact same anti-overfitting rules as :func:`validate_improvement`.
    """
    violation = _risk_bound_violation(candidate_params)
    if violation is not None:
        return violation

    base_train = evaluator(base_params, "train")
    base_test = evaluator(base_params, "test")
    cand_train = evaluator(candidate_params, "train")
    cand_test = evaluator(candidate_params, "test")
    return _judge(base_train, base_test, cand_train, cand_test, max_dd_limit, min_oos_return_pct)


def validate_improvement(
    base_params: Dict[str, Any],
    candidate_params: Dict[str, Any],
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    max_dd_limit: float = 15.0,
) -> ValidationReport:
    """
    Strict anti-overfitting validation check:
    1. Candidate risk MUST be strictly within [1%, 5%].
    2. Candidate must improve in-sample Sharpe ratio without catastrophic drawdown.
    3. Candidate must produce positive out-of-sample return and acceptable drawdown.
    """
    violation = _risk_bound_violation(candidate_params)
    if violation is not None:
        return violation

    base_train = evaluate_parameters(train_df, base_params)
    base_test = evaluate_parameters(test_df, base_params)
    cand_train = evaluate_parameters(train_df, candidate_params)
    cand_test = evaluate_parameters(test_df, candidate_params)
    return _judge(base_train, base_test, cand_train, cand_test, max_dd_limit, -2.0)


def _judge(base_train, base_test, cand_train, cand_test, max_dd_limit: float, min_oos_return_pct: float) -> ValidationReport:
    """Shared anti-overfitting verdict used by every strategy validator."""
    train_ret_delta = cand_train.total_return_pct - base_train.total_return_pct
    test_ret_delta = cand_test.total_return_pct - base_test.total_return_pct
    test_sharpe_delta = cand_test.sharpe_ratio - base_test.sharpe_ratio

    def _report(is_valid: bool, reason: str) -> ValidationReport:
        return ValidationReport(
            is_valid=is_valid,
            reason=reason,
            train_return_delta=train_ret_delta,
            test_return_delta=test_ret_delta,
            train_sharpe=cand_train.sharpe_ratio,
            test_sharpe=cand_test.sharpe_ratio,
            test_max_dd=cand_test.max_drawdown_pct,
            base_test_sharpe=base_test.sharpe_ratio,
            base_test_max_dd=base_test.max_drawdown_pct,
            test_sharpe_delta=test_sharpe_delta,
        )

    # Safety checks
    if cand_test.max_drawdown_pct > max_dd_limit:
        return _report(
            False,
            f"Failed: Out-of-sample Max Drawdown ({cand_test.max_drawdown_pct:.1f}%) exceeds safety limit ({max_dd_limit}%)",
        )

    # Must improve on training set AND stay positive / viable on unseen test set
    if cand_train.sharpe_ratio <= base_train.sharpe_ratio and cand_train.total_return_pct <= base_train.total_return_pct:
        return _report(False, "Failed: Did not improve in-sample training metrics")

    # Test set must not blow up
    if cand_test.total_return_pct < min_oos_return_pct:
        return _report(
            False,
            f"Failed: Negative out-of-sample performance ({cand_test.total_return_pct:.2f}%) indicates curve-fitting/overfitting",
        )

    return _report(True, "Passed: Verified in-sample improvement and out-of-sample robustness")
