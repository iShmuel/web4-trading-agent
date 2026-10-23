"""Unit tests for advanced PaperAccount features: Short selling, scale-out TP, atomic persistence and backup recovery."""

import json
import pytest
from core.paper_account import PaperAccount


def test_short_selling_profitable_lifecycle(tmp_path, monkeypatch):
    port_file = tmp_path / "paper_portfolio.json"
    monkeypatch.setattr("core.paper_account.PORTFOLIO_FILE", port_file)
    monkeypatch.setattr("core.paper_account.BACKUP_FILE", tmp_path / "paper_portfolio.json.bak")

    account = PaperAccount(initial_capital=10000.0)

    # 1. Open SHORT position: sell 2 units of ETH at $3,000
    pos = account.open_position(
        symbol="ETH/USDT",
        entry_price=3000.0,
        units=2.0,  # $6,000 order
        stop_loss=3200.0,
        dollar_risk=400.0,
        side="SHORT",
        strategy_id="momentum",
        fee_pct=0.0006,
    )

    assert account.has_position("ETH/USDT")
    assert pos["side"] == "SHORT"
    # Cash should have deducted $6,000 + $3.60 fee = $6,003.60
    assert pytest.approx(account.cash, 0.01) == (10000.0 - 6003.60)

    # 2. Total equity with price dropping to $2,800 (unrealized profit = $400)
    current_prices = {"ETH/USDT": 2800.0}
    eq = account.get_total_equity(current_prices)
    # Cash (~3996.40) + Short position value (2 * (6000 - 2800) = 2 * 3200 = 6400) = ~10396.40
    assert eq > 10390.0

    # 3. Close SHORT at $2,800 (gain of $200 per unit = $400 gross profit)
    trade = account.close_position("ETH/USDT", exit_price=2800.0, fee_pct=0.0006)
    assert trade["side"] == "SHORT"
    assert trade["pnl"] > 390.0  # ~$400 minus ~7.00 fees
    assert not account.has_position("ETH/USDT")
    assert account.cash > 10390.0


def test_short_selling_loss_lifecycle(tmp_path, monkeypatch):
    port_file = tmp_path / "paper_portfolio.json"
    monkeypatch.setattr("core.paper_account.PORTFOLIO_FILE", port_file)
    monkeypatch.setattr("core.paper_account.BACKUP_FILE", tmp_path / "paper_portfolio.json.bak")

    account = PaperAccount(initial_capital=10000.0)

    account.open_position(
        symbol="SOL/USDT",
        entry_price=100.0,
        units=10.0,  # $1,000 order
        stop_loss=110.0,
        dollar_risk=100.0,
        side="SHORT",
        fee_pct=0.0006,
    )

    # Price rises to 110.0 (stopped out)
    trade = account.close_position("SOL/USDT", exit_price=110.0, fee_pct=0.0006)
    assert trade["side"] == "SHORT"
    assert trade["pnl"] < -100.0  # -$100 gross loss minus fees
    assert account.cash < 9900.0


def test_atomic_persistence_and_backup_recovery(tmp_path, monkeypatch):
    port_file = tmp_path / "paper_portfolio.json"
    bak_file = tmp_path / "paper_portfolio.json.bak"
    monkeypatch.setattr("core.paper_account.PORTFOLIO_FILE", port_file)
    monkeypatch.setattr("core.paper_account.BACKUP_FILE", bak_file)

    account = PaperAccount(initial_capital=10000.0)
    account.open_position(
        symbol="BTC/USDT",
        entry_price=50000.0,
        units=0.05,
        stop_loss=48000.0,
        dollar_risk=100.0,
        side="LONG",
    )

    # Update state so backup file captures the active position
    account.update_trailing_stop("BTC/USDT", new_stop=49000.0, current_price=51000.0)

    # Both main file and backup file should exist
    assert port_file.exists()
    assert bak_file.exists()

    # Corrupt the main portfolio file
    with open(port_file, "w") as f:
        f.write("{ INVALID JSON CORRUPTION !!!")

    # Reload account: should safely recover from backup without crashing or losing positions!
    recovered_account = PaperAccount(initial_capital=10000.0)
    assert recovered_account.has_position("BTC/USDT")
    assert pytest.approx(recovered_account.cash, 0.01) == account.cash
