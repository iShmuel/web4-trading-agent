"""Polymarket Leading Macro Event Radar.

Fetches crowd-priced implied probabilities for forward-looking macroeconomic and binary
events (Fed interest rate cuts, rate hikes, US recession, crypto regulatory actions)
via Polymarket's public Gamma API. Operates with zero API keys and zero cost.

Provides the central PortfolioCoordinator with leading event intelligence to throttle risk
or pause breakout entries ahead of binary volatility shocks.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger(__name__)

GAMMA_BASE_URL = "https://gamma-api.polymarket.com/events"
REQUEST_TIMEOUT = 8


@dataclass
class MacroEventRisk:
    risk_level: str  # "LOW", "ELEVATED", "HIGH"
    recession_probability: float = 0.0
    rate_hike_probability: float = 0.0
    rate_cut_probability: float = 0.0
    recommended_risk_multiplier: float = 1.0
    active_event_warning: Optional[str] = None
    is_fallback: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _safe_parse_json(val: Any) -> list:
    """Safely decode JSON list from Gamma API string attributes."""
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return []
    return []


def parse_polymarket_events(events: List[Dict[str, Any]]) -> MacroEventRisk:
    """Parse raw Polymarket event objects into a structured MacroEventRisk report."""
    if not events:
        return MacroEventRisk(
            risk_level="LOW",
            recommended_risk_multiplier=1.0,
            is_fallback=True,
        )

    recession_prob = 0.0
    rate_hike_prob = 0.0
    rate_cut_prob = 0.0
    warnings = []

    for event in events:
        title = str(event.get("title", "")).lower()
        markets = event.get("markets", [])

        for m in markets:
            q = str(m.get("question", "")).lower()
            outcomes = _safe_parse_json(m.get("outcomes", []))
            prices = _safe_parse_json(m.get("outcomePrices", []))

            if not outcomes or not prices or len(outcomes) != len(prices):
                continue

            # Look for "Yes" probability
            yes_price = 0.0
            for out_name, out_p in zip(outcomes, prices):
                if str(out_name).lower() == "yes":
                    try:
                        yes_price = float(out_p)
                    except (ValueError, TypeError):
                        yes_price = 0.0
                    break

            # Recession tracking
            if "recession" in title or "recession" in q:
                if yes_price > recession_prob:
                    recession_prob = yes_price

            # Fed rate hike tracking
            if ("hike" in q or "increase" in q) and ("rate" in q or "fed" in title):
                if yes_price > rate_hike_prob:
                    rate_hike_prob = yes_price

            # Fed rate cut tracking
            if ("cut" in q or "decrease" in q) and ("rate" in q or "fed" in title):
                if yes_price > rate_cut_prob:
                    rate_cut_prob = yes_price

    # Risk evaluation rules
    risk_level = "LOW"
    risk_mult = 1.0

    if recession_prob >= 0.60:
        risk_level = "HIGH"
        risk_mult = 0.50
        warnings.append(f"Elevated US Recession Risk priced at {recession_prob:.0%}")
    elif recession_prob >= 0.40:
        risk_level = "ELEVATED"
        risk_mult = 0.75
        warnings.append(f"Moderate US Recession probability at {recession_prob:.0%}")

    if rate_hike_prob >= 0.30:
        risk_level = "HIGH"
        risk_mult = min(risk_mult, 0.50)
        warnings.append(f"Surprise Fed Rate Hike risk priced at {rate_hike_prob:.0%}")

    active_warning = "; ".join(warnings) if warnings else None

    return MacroEventRisk(
        risk_level=risk_level,
        recession_probability=round(recession_prob, 3),
        rate_hike_probability=round(rate_hike_prob, 3),
        rate_cut_probability=round(rate_cut_prob, 3),
        recommended_risk_multiplier=risk_mult,
        active_event_warning=active_warning,
        is_fallback=False,
    )


class PolymarketRadar:
    """Manages periodic polling, caching, and evaluation of prediction market event odds."""

    def __init__(self, mock_events: Optional[List[Dict[str, Any]]] = None, cache_ttl_seconds: int = 900):
        self._mock_events = mock_events
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cached_report: Optional[MacroEventRisk] = None
        self._last_fetch_time: Optional[datetime] = None

    def fetch_live_events(self) -> List[Dict[str, Any]]:
        """Fetch active macro event list from Polymarket Gamma API."""
        if self._mock_events is not None:
            return self._mock_events

        try:
            params = {
                "closed": "false",
                "limit": 15,
                "order": "volume24hr",
                "ascending": "false",
            }
            resp = requests.get(GAMMA_BASE_URL, params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.debug(f"[PolymarketRadar] Gamma API request notice: {e}")

        return []

    def evaluate(self, force_refresh: bool = False) -> MacroEventRisk:
        """Evaluate macro risk with in-memory TTL caching."""
        now = datetime.now(timezone.utc)
        if (
            not force_refresh
            and self._cached_report is not None
            and self._last_fetch_time is not None
            and (now - self._last_fetch_time).total_seconds() < self._cache_ttl_seconds
        ):
            return self._cached_report

        events = self.fetch_live_events()
        report = parse_polymarket_events(events)
        self._cached_report = report
        self._last_fetch_time = now
        return report
