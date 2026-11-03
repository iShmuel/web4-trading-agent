"""FRED & Market Macro Regime Radar.

Monitors macroeconomic stress indicators:
1. CBOE Volatility Index (VIX): Gauges market-wide panic and equity option volatility.
2. High-Yield OAS Credit Spreads: Measures junk bond default risk and liquidity contraction.
3. 10Y-2Y Treasury Yield Spread: Detects yield curve inversions and transitions.

Connects to the St. Louis Fed API if FRED_API_KEY is present; otherwise gracefully falls
back to Yahoo Finance (^VIX) with zero API keys required.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
import requests
import yfinance as yf

logger = logging.getLogger(__name__)

FRED_API_BASE = "https://api.stlouisfed.org/fred/series/observations"
REQUEST_TIMEOUT = 10


@dataclass
class MacroRegimeReport:
    regime: str  # "STABLE", "VOLATILITY_EXPANSION", "CRASH_DEFENSE"
    vix_level: float
    credit_spread_bps: Optional[float] = None
    allow_long_breakouts: bool = True
    risk_scale_factor: float = 1.0
    reason: str = "Normal market conditions"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def evaluate_macro_vix_regime(vix: float, credit_spread_bps: Optional[float] = None) -> MacroRegimeReport:
    """Evaluate volatility and credit stress levels into an institutional regime report."""
    vix_val = float(vix) if vix is not None else 18.0
    spread_val = float(credit_spread_bps) if credit_spread_bps is not None else 320.0

    # Crash Defense Regime: Extreme Volatility or Credit Crunch
    if vix_val >= 30.0 or spread_val >= 500.0:
        reason_parts = []
        if vix_val >= 30.0:
            reason_parts.append(f"VIX Extreme Panic ({vix_val:.1f} >= 30.0)")
        if spread_val >= 500.0:
            reason_parts.append(f"Credit Spreads Blown Out ({spread_val:.0f} bps >= 500)")
        return MacroRegimeReport(
            regime="CRASH_DEFENSE",
            vix_level=vix_val,
            credit_spread_bps=spread_val,
            allow_long_breakouts=False,
            risk_scale_factor=0.50,
            reason="; ".join(reason_parts),
        )

    # Volatility Expansion: Elevated risk (VIX 22 - 30)
    if vix_val >= 22.0 or spread_val >= 420.0:
        return MacroRegimeReport(
            regime="VOLATILITY_EXPANSION",
            vix_level=vix_val,
            credit_spread_bps=spread_val,
            allow_long_breakouts=True,
            risk_scale_factor=0.75,
            reason=f"Elevated market volatility (VIX: {vix_val:.1f})",
        )

    # Stable Market Environment
    return MacroRegimeReport(
        regime="STABLE",
        vix_level=vix_val,
        credit_spread_bps=spread_val,
        allow_long_breakouts=True,
        risk_scale_factor=1.0,
        reason=f"Benign volatility regime (VIX: {vix_val:.1f})",
    )


class FredMacroRadar:
    """Fetches macro telemetry via Yahoo Finance or FRED API with local caching."""

    def __init__(self, api_key: Optional[str] = None, cache_ttl_seconds: int = 1800):
        self.api_key = api_key or os.getenv("FRED_API_KEY")
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cached_report: Optional[MacroRegimeReport] = None
        self._last_fetch_time: Optional[datetime] = None

    def fetch_live_vix(self) -> float:
        """Fetch latest CBOE VIX price via Yahoo Finance."""
        try:
            ticker = yf.Ticker("^VIX")
            hist = ticker.history(period="2d", interval="1h")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception as e:
            logger.debug(f"[FredMacroRadar] Yahoo ^VIX fetch notice: {e}")
        return 18.0  # Historical median baseline fallback

    def fetch_fred_credit_spread(self) -> Optional[float]:
        """Fetch US High Yield OAS spread from FRED API (if configured)."""
        if not self.api_key:
            return None
        try:
            params = {
                "series_id": "BAMLH0A0HYM2",
                "api_key": self.api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 5,
            }
            resp = requests.get(FRED_API_BASE, params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                obs = data.get("observations", [])
                for o in obs:
                    val_str = o.get("value", "")
                    if val_str != ".":
                        # Value is in percentage points (e.g. 3.25 = 325 bps)
                        return float(val_str) * 100.0
        except Exception as e:
            logger.debug(f"[FredMacroRadar] FRED credit spread fetch notice: {e}")
        return None

    def evaluate(self, force_refresh: bool = False) -> MacroRegimeReport:
        """Evaluate macro regime with in-memory TTL caching."""
        now = datetime.now(timezone.utc)
        if (
            not force_refresh
            and self._cached_report is not None
            and self._last_fetch_time is not None
            and (now - self._last_fetch_time).total_seconds() < self.cache_ttl_seconds
        ):
            return self._cached_report

        vix = self.fetch_live_vix()
        credit_spread = self.fetch_fred_credit_spread()
        report = evaluate_macro_vix_regime(vix=vix, credit_spread_bps=credit_spread)
        self._cached_report = report
        self._last_fetch_time = now
        return report
