"""TDD Test Suite for the 5 Institutional AGY Skill Upgrades:
1. NYSE Market Clock, Holiday Calendar & 9:30 AM Opening-Bell Gap Buffer (trader-alerts)
2. SEC EDGAR 8-K Real-Time Filing Veto & Earnings Shock Guard (trader-news)
3. SEC XBRL 10-Q/10-K Fundamental Quality Gate & Screener Rules (trader-analyst + trader-screener)
4. Smart-Money 13F Whale & Congressional Pelosi PTR Conviction Radar (trader-copycat + trader-pelosi)
5. Per-Ticker Repeat-Loss / 30-Day Wash-Sale Guard & Drift-Band Rebalancer (trader-tax + trader-rebalance)
"""

from datetime import datetime, timezone, timedelta
import pandas as pd
import pytest

from core.skill_intelligence_gate import (
    NyseMarketClock,
    SecEdgar8KMonitor,
    SecFundamentalGate,
    SmartMoneyRadar,
    WashSaleAndLossStreakGuard,
    PortfolioDriftRebalancer,
)
from core.paper_account import PaperAccount
from core.coordinator import PortfolioCoordinator


# =====================================================================
# 1. NYSE Market Clock & Opening-Bell Buffer Tests (trader-alerts)
# =====================================================================
def test_nyse_clock_regular_hours_and_opening_bell_buffer():
    clock = NyseMarketClock(opening_bell_buffer_minutes=15)

    # Tuesday 2026-10-13 at 9:35 AM EDT (13:35 UTC) -> Inside 15m opening bell buffer
    dt_opening_chop = datetime(2026, 10, 13, 13, 35, tzinfo=timezone.utc)
    verdict_chop = clock.evaluate_equity_session("SPY", dt_opening_chop, enforce_market_hours=True)
    assert verdict_chop.is_market_open is True
    assert verdict_chop.in_opening_bell_buffer is True
    assert verdict_chop.allow_new_entry is False
    assert "Opening-Bell" in verdict_chop.reason

    # Tuesday 2026-10-13 at 10:30 AM EDT (14:30 UTC) -> Normal open trading session
    dt_normal = datetime(2026, 10, 13, 14, 30, tzinfo=timezone.utc)
    verdict_normal = clock.evaluate_equity_session("NVDA", dt_normal, enforce_market_hours=True)
    assert verdict_normal.is_market_open is True
    assert verdict_normal.in_opening_bell_buffer is False
    assert verdict_normal.allow_new_entry is True

    # Crypto symbols always bypass NYSE clock restrictions 24/7
    verdict_crypto = clock.evaluate_equity_session("BTC/USDT", dt_opening_chop, enforce_market_hours=True)
    assert verdict_crypto.allow_new_entry is True


def test_nyse_clock_holidays_and_early_closes():
    clock = NyseMarketClock(opening_bell_buffer_minutes=15)

    # Thanksgiving 2026-11-26 at 11:00 AM EST (16:00 UTC) -> Closed for NYSE Holiday
    dt_thanksgiving = datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc)
    v_hol = clock.evaluate_equity_session("AAPL", dt_thanksgiving, enforce_market_hours=True)
    assert v_hol.is_market_open is False
    assert v_hol.allow_new_entry is False
    assert "Thanksgiving" in v_hol.reason

    # Day after Thanksgiving 2026-11-27 at 2:00 PM EST (19:00 UTC) -> Closed after 1:00 PM Early Close
    dt_early_after = datetime(2026, 11, 27, 19, 0, tzinfo=timezone.utc)
    v_early = clock.evaluate_equity_session("AAPL", dt_early_after, enforce_market_hours=True)
    assert v_early.is_market_open is False
    assert v_early.allow_new_entry is False


# =====================================================================
# 2. SEC EDGAR 8-K Real-Time Filing Veto Tests (trader-news)
# =====================================================================
def test_sec_8k_monitor_blocks_adverse_filings_and_earnings_shocks():
    monitor = SecEdgar8KMonitor(earnings_cooldown_hours=24.0, adverse_cooldown_hours=48.0)
    now = datetime(2026, 10, 29, 14, 0, tzinfo=timezone.utc)

    # Simulate AMD filing an 8-K Item 2.02 (Earnings Results) 4 hours ago
    amd_filings = [
        {
            "symbol": "AMD",
            "form": "8-K",
            "items": ["2.02", "9.01"],
            "filed_at": (now - timedelta(hours=4)).isoformat(),
            "title": "8-K - Current report, items 2.02 and 9.01",
        }
    ]
    verdict_amd = monitor.evaluate_symbol("AMD", now_utc=now, filings_override=amd_filings)
    assert verdict_amd.approved is False
    assert "2.02" in verdict_amd.reason
    assert "earnings results" in verdict_amd.reason.lower()

    # Simulate a company filing Item 4.02 (Past financials no longer reliable / restatement)
    smci_filings = [
        {
            "symbol": "SMCI",
            "form": "8-K",
            "items": ["4.02"],
            "filed_at": (now - timedelta(hours=30)).isoformat(),
            "title": "8-K - Item 4.02 Non-Reliance on Previously Issued Financial Statements",
        }
    ]
    verdict_smci = monitor.evaluate_symbol("SMCI", now_utc=now, filings_override=smci_filings)
    assert verdict_smci.approved is False
    assert "4.02" in verdict_smci.reason

    # Filing older than 72 hours should pass
    old_filings = [
        {
            "symbol": "AAPL",
            "form": "8-K",
            "items": ["2.02"],
            "filed_at": (now - timedelta(hours=72)).isoformat(),
            "title": "8-K - Earnings",
        }
    ]
    verdict_old = monitor.evaluate_symbol("AAPL", now_utc=now, filings_override=old_filings)
    assert verdict_old.approved is True


# =====================================================================
# 3. SEC XBRL Fundamental Quality Gate Tests (trader-analyst + screener)
# =====================================================================
def test_sec_fundamental_gate_passes_strong_and_blocks_toxic_equities():
    gate = SecFundamentalGate(
        min_revenue_growth_yoy=-5.0,
        min_operating_margin_pct=0.0,
    )

    # NVDA: High revenue growth, strong operating margin -> PASS
    v_nvda = gate.evaluate_equity(
        "NVDA",
        metrics_override={"revenue_growth_yoy": 58.0, "operating_margin": 61.5, "eps_diluted": 2.95, "pe": 34.0},
    )
    assert v_nvda.passed is True
    assert v_nvda.quality_score > 0.7

    # Unprofitable hype stock with negative operating margin and shrinking revenue -> FAIL
    v_junk = gate.evaluate_equity(
        "JUNK",
        metrics_override={"revenue_growth_yoy": -12.4, "operating_margin": -18.5, "eps_diluted": -0.85},
    )
    assert v_junk.passed is False
    assert "operating_margin" in v_junk.reason or "revenue_growth_yoy" in v_junk.reason

    # Macro ETFs (SPY, GLD, SLV, XLE, TLT) and Crypto automatically pass
    assert gate.evaluate_equity("GLD").passed is True
    assert gate.evaluate_equity("BTC/USDT").passed is True


# =====================================================================
# 4. Smart-Money 13F Whale & Congressional Pelosi PTR Radar Tests
# =====================================================================
def test_smart_money_radar_boosts_whale_and_pelosi_accumulation():
    radar = SmartMoneyRadar()

    # NVDA has heavy 13F super-investor ownership + Pelosi PTR call/share accumulation
    sig_nvda = radar.evaluate_symbol("NVDA")
    assert sig_nvda.whale_13f_bullish is True
    assert sig_nvda.congress_ptr_bullish is True
    assert sig_nvda.confluence_delta > 0.0
    assert sig_nvda.risk_multiplier >= 1.05

    # Custom override showing heavy institutional distribution (selling out)
    sig_dump = radar.evaluate_symbol(
        "DUMP",
        override_data={"13f_net_buyers": 0, "13f_net_sellers": 5, "ptr_buys": 0, "ptr_sells": 2},
    )
    assert sig_dump.confluence_delta < 0.0
    assert sig_dump.risk_multiplier < 1.0


# =====================================================================
# 5. Wash-Sale / Repeat-Loss Guard & Drift Rebalancer Tests
# =====================================================================
def test_wash_sale_and_repeat_loss_guard_blocks_repeat_bleed():
    guard = WashSaleAndLossStreakGuard(
        max_consecutive_losses=2,
        crypto_cooldown_hours=48.0,
        equity_wash_sale_days=30,
        wash_sale_min_loss_usd=50.0,
    )
    now = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)

    # Simulate 2 consecutive recent losses on SOL/USDT
    closed_trades = [
        {
            "symbol": "SOL/USDT",
            "pnl": -79.31,
            "exit_time": (now - timedelta(hours=20)).isoformat(),
        },
        {
            "symbol": "SOL/USDT",
            "pnl": -38.05,
            "exit_time": (now - timedelta(hours=6)).isoformat(),
        },
    ]
    v_sol = guard.check_entry_allowed("SOL/USDT", closed_trades, now_utc=now)
    assert v_sol.allowed is False
    assert "consecutive losses" in v_sol.reason.lower()

    # Simulate an equity loss > $50 on GOOGL 12 days ago -> Buying GOOG (same share-class family) triggers Wash-Sale guard
    equity_trades = [
        {
            "symbol": "GOOGL",
            "pnl": -85.00,
            "exit_time": (now - timedelta(days=12)).isoformat(),
        }
    ]
    v_goog = guard.check_entry_allowed("GOOG", equity_trades, now_utc=now, enable_wash_sale=True)
    assert v_goog.allowed is False
    assert "wash-sale" in v_goog.reason.lower()


def test_portfolio_drift_rebalancer_trims_overweight_winner(tmp_path):
    account = PaperAccount(initial_capital=10000.0, portfolio_file=tmp_path / "test_portfolio.json")
    # Open a position in NVDA costing $2,000 (10 shares @ $200)
    account.open_position(
        symbol="NVDA",
        entry_price=200.0,
        units=10.0,
        stop_loss=180.0,
        dollar_risk=200.0,
        max_concurrent_positions=5,
    )
    # Price surges to $450 -> NVDA value = $4,500, Cash = $8,000 -> Total Equity = $12,500
    # NVDA weight = 4500 / 12500 = 36.0% (exceeds max_position_pct 25% + band 5% = 30%)
    rebalancer = PortfolioDriftRebalancer(max_position_pct=25.0, rebalance_band_pct=5.0)
    actions = rebalancer.rebalance_overweight_positions(account, current_prices={"NVDA": 450.0})

    assert len(actions) == 1
    assert actions[0]["symbol"] == "NVDA"
    assert actions[0]["trimmed_units"] > 0
    assert actions[0]["realized_pnl"] > 0
    # Remaining NVDA position value should now be ~25% of total equity ($3,125 / $450 = ~6.944 shares)
    rem_units = account.positions["NVDA"]["units"]
    assert abs((rem_units * 450.0) / 12500.0 - 0.25) < 0.01


def test_coordinator_integrates_skill_gates_end_to_end(tmp_path):
    """Verify PortfolioCoordinator blocks an equity failing SEC XBRL fundamentals (GRAB) via wired skill gates."""
    from core.risk_engine import RiskEngine
    account = PaperAccount(initial_capital=10000.0, portfolio_file=tmp_path / "coord_test_port.json")
    risk_eng = RiskEngine(default_risk_pct=0.02, max_open_positions=5)
    cfg = {
        "account_risk_pct": 0.02,
        "max_concurrent_positions": 5,
        "enable_sec_fundamental_gate": True,
        "enable_wash_and_streak_guard": True,
        "enable_nyse_session_gate": True,
        "enable_drift_rebalancer": True,
    }
    coord = PortfolioCoordinator(config=cfg, account=account, risk_engine=risk_eng)
    # Verify all 5 skill intelligence engines are attached to coordinator
    assert hasattr(coord, "nyse_clock")
    assert hasattr(coord, "sec_8k_monitor")
    assert hasattr(coord, "sec_fundamental_gate")
    assert hasattr(coord, "smart_money_radar")
    assert hasattr(coord, "wash_and_streak_guard")
    assert hasattr(coord, "drift_rebalancer")

    # Verify GRAB fails SEC fundamental gate while NVDA passes
    assert coord.sec_fundamental_gate.evaluate_equity("GRAB").passed is False
    assert coord.sec_fundamental_gate.evaluate_equity("NVDA").passed is True

