"""Unit tests for Institutional Alpha Suite:
1. Polymarket Leading Macro Event Radar (Zero-key Gamma API parser & risk multiplier)
2. FRED & VIX Macro Regime Radar (Credit spread blowout & volatility regime gates)
3. Institutional Pre-Flight Execution Safety Guards (Spread, staleness, liquidity depth)
"""

from datetime import datetime, timezone, timedelta
import pytest
import pandas as pd
import numpy as np

from core.polymarket_radar import (
    PolymarketRadar,
    MacroEventRisk,
    parse_polymarket_events,
)
from core.fred_radar import (
    FredMacroRadar,
    MacroRegimeReport,
    evaluate_macro_vix_regime,
)
from core.preflight import (
    PreFlightChecker,
    PreFlightVerdict,
    validate_order_preflight,
)
from core.risk_engine import RiskEngine
from core.coordinator import PortfolioCoordinator
from core.paper_account import PaperAccount
from core.signals import TradingSignal, SignalDirection, SignalStatus


# =====================================================================
# 1. POLYMARKET MACRO EVENT RADAR TESTS
# =====================================================================

def test_polymarket_parse_and_risk_evaluation():
    # Synthetic Gamma API response for macro binary events
    sample_events = [
        {
            "title": "Fed interest rate decision in next meeting",
            "active": True,
            "closed": False,
            "markets": [
                {
                    "question": "Will the Fed cut rates by 50 bps?",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.15", "0.85"]',
                    "volume": "500000",
                },
                {
                    "question": "Will the Fed hike interest rates?",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.05", "0.95"]',
                    "volume": "350000",
                },
            ],
        },
        {
            "title": "US Recession in 2026",
            "active": True,
            "closed": False,
            "markets": [
                {
                    "question": "Will the US enter a recession in 2026?",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.22", "0.78"]',
                    "volume": "1200000",
                }
            ],
        },
    ]

    risk = parse_polymarket_events(sample_events)
    assert isinstance(risk, MacroEventRisk)
    assert risk.risk_level == "LOW"
    assert risk.recession_probability == 0.22
    assert risk.rate_hike_probability == 0.05
    assert risk.recommended_risk_multiplier == 1.0


def test_polymarket_detects_elevated_recession_or_hike_risk():
    # Shock scenario: Recession odds spike to 72%
    shock_events = [
        {
            "title": "US Recession in 2026",
            "active": True,
            "closed": False,
            "markets": [
                {
                    "question": "Will the US enter a recession in 2026?",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.72", "0.28"]',
                    "volume": "2500000",
                }
            ],
        }
    ]

    risk = parse_polymarket_events(shock_events)
    assert risk.risk_level == "HIGH"
    assert risk.recession_probability == 0.72
    # Recommended risk multiplier must scale down to preserve capital
    assert risk.recommended_risk_multiplier <= 0.60
    assert "Recession" in risk.active_event_warning


def test_polymarket_graceful_offline_fallback():
    # If network/API is down or returns empty, must degrade gracefully without crashing
    radar = PolymarketRadar(mock_events=[])
    risk = radar.evaluate()
    assert risk.risk_level == "LOW"
    assert risk.recommended_risk_multiplier == 1.0
    assert risk.is_fallback is True


# =====================================================================
# 2. FRED & VIX MACRO REGIME RADAR TESTS
# =====================================================================

def test_vix_regime_stable():
    # Low volatility: VIX = 14.5
    report = evaluate_macro_vix_regime(vix=14.5, credit_spread_bps=320.0)
    assert report.regime == "STABLE"
    assert report.allow_long_breakouts is True
    assert report.risk_scale_factor == 1.0


def test_vix_regime_volatility_expansion():
    # Elevated volatility: VIX = 24.0
    report = evaluate_macro_vix_regime(vix=24.0, credit_spread_bps=360.0)
    assert report.regime == "VOLATILITY_EXPANSION"
    assert report.allow_long_breakouts is True
    # Risk factor should be mildly throttled to 0.75x
    assert report.risk_scale_factor == 0.75


def test_vix_and_credit_spread_crash_defense():
    # Panic conditions: VIX = 35.0, High Yield spread blowing out to 580 bps
    report = evaluate_macro_vix_regime(vix=35.0, credit_spread_bps=580.0)
    assert report.regime == "CRASH_DEFENSE"
    # Long breakouts MUST be blocked in market crisis
    assert report.allow_long_breakouts is False
    assert report.risk_scale_factor <= 0.50


# =====================================================================
# 3. PRE-FLIGHT EXECUTION SAFETY GUARDS TESTS
# =====================================================================

def test_preflight_passes_healthy_order():
    now = datetime.now(timezone.utc)
    verdict = validate_order_preflight(
        symbol="BTC/USDT",
        side="BUY",
        price=80000.0,
        bid=79990.0,
        ask=80010.0,  # Spread: 20 / 80000 = 0.025% (well under 0.15% limit)
        last_quote_time=now - timedelta(seconds=15),  # 15s old tick (fresh)
        hourly_volume=500.0,  # 500 BTC volume
        proposed_units=0.5,   # 0.5 BTC (0.1% participation, well under 2% cap)
        available_cash=5000.0,
        order_notional=80000.0 * 0.5,  # $40,000 (wait: need sufficient cash)
    )
    # Cash insufficient: order_notional > available_cash
    assert verdict.passed is False
    assert "Cash" in verdict.reason

    # Now with sufficient cash:
    verdict_valid = validate_order_preflight(
        symbol="BTC/USDT",
        side="BUY",
        price=80000.0,
        bid=79990.0,
        ask=80010.0,
        last_quote_time=now - timedelta(seconds=15),
        hourly_volume=500.0,
        proposed_units=0.01,
        available_cash=5000.0,
        order_notional=800.0,
    )
    assert verdict_valid.passed is True
    assert verdict_valid.reason == "PREFLIGHT_PASSED"


def test_preflight_blocks_excessive_spread():
    # Bid-ask spread blown out to 0.45% (illiquid off-hours)
    now = datetime.now(timezone.utc)
    verdict = validate_order_preflight(
        symbol="NVDA",
        side="BUY",
        price=200.0,
        bid=199.10,
        ask=200.90,  # Spread: 1.80 / 200 = 0.90% (exceeds 0.20% max)
        last_quote_time=now,
        hourly_volume=100000.0,
        proposed_units=10.0,
        available_cash=5000.0,
        order_notional=2000.0,
        max_spread_pct=0.0020,
    )
    assert verdict.passed is False
    assert "SPREAD_TOO_WIDE" in verdict.reason


def test_preflight_blocks_stale_market_quote():
    # Quote is 10 minutes old
    old_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    verdict = validate_order_preflight(
        symbol="SOL/USDT",
        side="BUY",
        price=150.0,
        bid=149.95,
        ask=150.05,
        last_quote_time=old_time,
        hourly_volume=50000.0,
        proposed_units=5.0,
        available_cash=5000.0,
        order_notional=750.0,
        max_staleness_seconds=180,  # 3 min limit
    )
    assert verdict.passed is False
    assert "PRICE_STALE" in verdict.reason


def test_preflight_blocks_liquidity_impact_over_participation():
    # Order size is 10% of total hourly volume (would cause massive market impact slippage)
    now = datetime.now(timezone.utc)
    verdict = validate_order_preflight(
        symbol="NEAR/USDT",
        side="BUY",
        price=4.0,
        bid=3.998,
        ask=4.002,
        last_quote_time=now,
        hourly_volume=1000.0,  # Small hourly volume: 1,000 units
        proposed_units=100.0,  # 10% of hourly volume! (limit: 2%)
        available_cash=5000.0,
        order_notional=400.0,
        max_volume_participation_pct=0.02,
    )
    assert verdict.passed is False
    assert "EXCEEDS_LIQUIDITY_DEPTH" in verdict.reason


# =====================================================================
# 4. LOCAL LLM PRE-FLIGHT DEVIL'S ADVOCATE & REFLECTION TESTS
# =====================================================================

def test_local_llm_screener_disabled():
    from core.preflight import LocalLLMScreener
    screener = LocalLLMScreener(enabled=False)
    verdict = screener.screen_entry_proposal("NVDA", "BUY", 200.0, "momentum")
    assert verdict.approved is True
    assert "disabled" in verdict.reason.lower()


def test_local_llm_screener_offline_fallback():
    from core.preflight import LocalLLMScreener
    # Points to unreachable mock port
    screener = LocalLLMScreener(endpoint="http://127.0.0.1:9999/api/generate", timeout=0.5)
    verdict = screener.screen_entry_proposal("BTC/USDT", "BUY", 80000.0, "momentum", headlines=["Sample"])
    assert verdict.approved is True
    assert "fallback" in verdict.reason.lower()


def test_fetch_ticker_headlines_safe_fallback():
    from core.preflight import fetch_ticker_headlines
    headlines = fetch_ticker_headlines("NVDA", limit=2, timeout=2)
    assert isinstance(headlines, list)


def test_generate_llm_reflection_fallback():
    from core.journal import generate_llm_reflection
    # Test fallback reflection with offline endpoint
    reflection = generate_llm_reflection(
        symbol="ETH/USDT",
        action="ENTER_LONG",
        strategy_id="momentum",
        ref_price=2200.0,
        settle_price=2300.0,
        forward_ret_pct=4.54,
        alpha_pct=2.10,
        benchmark_symbol="BTC/USDT",
        endpoint="http://127.0.0.1:9999/api/generate",
        timeout=0.5,
    )
    assert isinstance(reflection, str)
    assert "outperformed" in reflection
    assert "ETH/USDT" not in reflection or "2.10%" in reflection
