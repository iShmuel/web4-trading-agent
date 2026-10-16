"""Unit tests for the self-improvement validator and anti-overfitting rules."""

import pandas as pd
import pytest

from agent.validator import split_data, validate_improvement


def test_split_data_integrity():
    # Create sample DataFrame
    df = pd.DataFrame({"close": range(100)})
    train, test = split_data(df, train_ratio=0.7)

    assert len(train) == 70
    assert len(test) == 30
    # Verify no chronological overlap
    assert train["close"].iloc[-1] < test["close"].iloc[0]


def test_validator_rejects_excessive_risk():
    train_df = pd.DataFrame({"close": range(100)})
    test_df = pd.DataFrame({"close": range(100)})

    base_params = {"account_risk_pct": 0.02}
    # Candidate with 8% risk (above 5% maximum)
    illegal_candidate = {"account_risk_pct": 0.08}

    report = validate_improvement(base_params, illegal_candidate, train_df, test_df)
    assert report.is_valid is False
    assert "violates 1%-5% boundary" in report.reason
