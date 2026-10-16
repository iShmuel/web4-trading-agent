"""Unit tests for the multi-asset paper trading portfolio and order manager."""

import pytest
from core.paper_account import PaperAccount


def test_multi_asset_portfolio_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paper_account.PORTFOLIO_FILE", tmp_path / "test_portfolio.json")

    account = PaperAccount(initial_capital=10000.0)
    assert account.cash == 10000.0
    assert len(account.positions) == 0

    # 1. Open Position 1: BTC/USDT
    pos1 = account.open_position(
        symbol="BTC/USDT",
        entry_price=60000.0,
        units=0.05,  # $3,000 order
        stop_loss=58000.0,
        dollar_risk=100.0,
        max_concurrent_positions=2,
    )
    assert account.has_position("BTC/USDT")
    assert len(account.positions) == 1

    # 2. Open Position 2: ETH/USDT simultaneously
    pos2 = account.open_position(
        symbol="ETH/USDT",
        entry_price=3000.0,
        units=1.0,  # $3,000 order
        stop_loss=2900.0,
        dollar_risk=100.0,
        max_concurrent_positions=2,
    )
    assert account.has_position("ETH/USDT")
    assert len(account.positions) == 2

    # 3. Position limit enforcement: opening 3rd position must fail when limit is 2
    with pytest.raises(RuntimeError, match="Position limit reached"):
        account.open_position(
            symbol="SOL/USDT",
            entry_price=150.0,
            units=10.0,
            stop_loss=140.0,
            dollar_risk=100.0,
            max_concurrent_positions=2,
        )

    # 4. Total equity calculation
    current_prices = {"BTC/USDT": 62000.0, "ETH/USDT": 3100.0}
    total_eq = account.get_total_equity(current_prices)
    # Cash was ~10000 - 3000 - 3000 = ~4000. BTC value: 0.05 * 62000 = 3100. ETH: 1 * 3100 = 3100.
    # Total equity should be ~$10,200
    assert total_eq > 10100.0

    # 5. Trailing stop ratcheting for BTC only
    account.update_trailing_stop("BTC/USDT", new_stop=59000.0, current_price=62000.0)
    assert account.get_position("BTC/USDT")["trailing_stop"] == 59000.0
    assert account.get_position("ETH/USDT")["trailing_stop"] == 2900.0  # untouched

    # 6. Close BTC at profit
    trade_btc = account.close_position("BTC/USDT", exit_price=62500.0)
    assert not account.has_position("BTC/USDT")
    assert account.has_position("ETH/USDT")
    assert len(account.positions) == 1
    assert trade_btc["pnl"] > 100.0
