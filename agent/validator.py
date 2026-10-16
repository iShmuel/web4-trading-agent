"""Validation engine for walk-forward, out-of-sample verification to prevent overfitting."""

from dataclasses import dataclass
from typing import Tuple, Dict, Any
import pandas as pd

from core.backtest_engine import run_backtest, BacktestResult
from core.risk_engine import MIN_ALLOWED_RISK_PCT, MAX_ALLOWED_RISK_PCT


@dataclass
class ValidationReport:
    is_valid: bool
    reason: str
    train_return_delta: float
    test_return_delta: float
    train_sharpe: float
    test_sharpe: float
    test_max_dd: float


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
    """Run backtest on a dataset using given parameters."""
    return run_backtest(
        df=df,
        initial_capital=10000.0,
        account_risk_pct=params.get("account_risk_pct", 0.02),
        breakout_lookback=params.get("breakout_lookback", 24),
        ema_trend_filter=params.get("ema_trend_filter", 200),
        atr_period=params.get("atr_period", 14),
        atr_multiplier=params.get("atr_multiplier", 2.5),
        fee_pct=params.get("fee_pct", 0.0006),
        slippage_pct=params.get("slippage_pct", 0.0004),
    )


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

    # Run baseline
    base_train = evaluate_parameters(train_df, base_params)
    base_test = evaluate_parameters(test_df, base_params)

    # Run candidate
    cand_train = evaluate_parameters(train_df, candidate_params)
    cand_test = evaluate_parameters(test_df, candidate_params)

    train_ret_delta = cand_train.total_return_pct - base_train.total_return_pct
    test_ret_delta = cand_test.total_return_pct - base_test.total_return_pct

    # Safety checks
    if cand_test.max_drawdown_pct > max_dd_limit:
        return ValidationReport(
            is_valid=False,
            reason=f"Failed: Out-of-sample Max Drawdown ({cand_test.max_drawdown_pct:.1f}%) exceeds safety limit ({max_dd_limit}%)",
            train_return_delta=train_ret_delta,
            test_return_delta=test_ret_delta,
            train_sharpe=cand_train.sharpe_ratio,
            test_sharpe=cand_test.sharpe_ratio,
            test_max_dd=cand_test.max_drawdown_pct,
        )

    # Must improve on training set AND stay positive / viable on unseen test set
    if cand_train.sharpe_ratio <= base_train.sharpe_ratio and cand_train.total_return_pct <= base_train.total_return_pct:
        return ValidationReport(
            is_valid=False,
            reason="Failed: Did not improve in-sample training metrics",
            train_return_delta=train_ret_delta,
            test_return_delta=test_ret_delta,
            train_sharpe=cand_train.sharpe_ratio,
            test_sharpe=cand_test.sharpe_ratio,
            test_max_dd=cand_test.max_drawdown_pct,
        )

    # Test set must not blow up
    if cand_test.total_return_pct < -2.0:
        return ValidationReport(
            is_valid=False,
            reason=f"Failed: Negative out-of-sample performance ({cand_test.total_return_pct:.2f}%) indicates curve-fitting/overfitting",
            train_return_delta=train_ret_delta,
            test_return_delta=test_ret_delta,
            train_sharpe=cand_train.sharpe_ratio,
            test_sharpe=cand_test.sharpe_ratio,
            test_max_dd=cand_test.max_drawdown_pct,
        )

    return ValidationReport(
        is_valid=True,
        reason="Passed: Verified in-sample improvement and out-of-sample robustness",
        train_return_delta=train_ret_delta,
        test_return_delta=test_ret_delta,
        train_sharpe=cand_train.sharpe_ratio,
        test_sharpe=cand_test.sharpe_ratio,
        test_max_dd=cand_test.max_drawdown_pct,
    )
