"""Unit tests for the live paper trading portfolio and order manager."""

import pytest
from core.paper_account import PaperAccount


def test_paper_account_lifecycle(tmp_path, monkeypatch):
    # Mock data directory to temporary path
    monkeypatch.setattr("core.paper_account.PORTFOLIO_FILE", tmp_path / "test_portfolio.json")

    account = PaperAccount(initial_capital=10000.0)
    assert account.cash == 10000.0
    assert account.position is None

    # Test open position
    pos = account.open_position(
        symbol="BTC/USDT",
        entry_price=60000.0,
        units=0.1,  # $6,000 order
        stop_loss=58000.0,
        dollar_risk=200.0,
        fee_pct=0.0006,  # $3.60 fee
    )

    assert account.position is not None
    assert pos["units"] == 0.1
    assert pos["trailing_stop"] == 58000.0
    assert account.cash == pytest.approx(10000.0 - 6003.60, 0.01)

    # Test trailing stop ratchets up
    account.update_trailing_stop(new_stop=59000.0, current_price=62000.0)
    assert account.position["trailing_stop"] == 59000.0

    # Test trailing stop does not move down
    account.update_trailing_stop(new_stop=58500.0, current_price=61000.0)
    assert account.position["trailing_stop"] == 59000.0

    # Test close position at profit ($63,000)
    trade = account.close_position(exit_price=63000.0, fee_pct=0.0006)
    assert account.position is None
    assert len(account.state["closed_trades"]) == 1
    # Gross PnL: (63000 - 60000) * 0.1 = +$300. Net of fees: ~$292.62
    assert trade["pnl"] > 290.0
    assert account.cash > 10000.0
