"""Institutional Pre-Flight Execution Safety Guards.

Validates all proposed orders against strict execution quality rules before reaching
the paper execution engine:
1. Bid-Ask Spread Guard: Blocks orders if market spread exceeds allowable threshold (slippage protection).
2. Tick Staleness Guard: Rejects orders if price quote is delayed or stale.
3. Liquidity Depth / Volume Participation Guard: Rejects orders that exceed max allowable volume participation.
4. Cash Buffer & Minimum Notional Guard: Ensures orders meet minimum exchange limits and preserve account liquidity.
5. Local LLM Devil's Advocate Screener: Out-of-band sanity check querying local Ollama (phi4-mini) with live headlines.
"""

import logging
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import requests

from core.data_loader import is_crypto_symbol

logger = logging.getLogger(__name__)


def fetch_ticker_headlines(symbol: str, limit: int = 3, timeout: int = 4) -> List[str]:
    """Fetch recent market headlines for a ticker via Google News RSS (zero key, public)."""
    clean_sym = symbol.replace("/USDT", "").replace("-USD", "")
    query = f"{clean_sym} crypto" if is_crypto_symbol(symbol) else f"{clean_sym} stock"
    encoded_query = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-US&gl=US&ceid=US:en"

    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            xml_data = resp.read()
        root = ET.fromstring(xml_data)
        items = root.findall(".//item")
        headlines = []
        for it in items[:limit]:
            t = it.find("title")
            if t is not None and t.text:
                headlines.append(t.text.strip())
        return headlines
    except Exception as e:
        logger.debug(f"[PreFlight] News fetch notice for {symbol}: {e}")
        return []


@dataclass
class LLMScreenVerdict:
    approved: bool
    reason: str
    latency_seconds: float = 0.0
    model: str = "phi4-mini:latest"


class LocalLLMScreener:
    """Out-of-band Devil's Advocate Screener using local Ollama (phi4-mini/qwen)."""

    def __init__(
        self,
        endpoint: str = "http://localhost:11434/api/generate",
        model: str = "phi4-mini:latest",
        timeout: float = 6.0,
        enabled: bool = True,
    ):
        self.endpoint = endpoint
        self.model = model
        self.timeout = timeout
        self.enabled = enabled

    def screen_entry_proposal(
        self,
        symbol: str,
        side: str,
        price: float,
        strategy_id: str,
        headlines: Optional[List[str]] = None,
        indicators: Optional[Dict[str, Any]] = None,
    ) -> LLMScreenVerdict:
        """Screen trade proposal for immediate news hazards, earnings blackouts, or red flags."""
        if not self.enabled:
            return LLMScreenVerdict(approved=True, reason="LLM screening disabled by config")

        t0 = time.time()
        headlines_text = (
            "\n".join(f"- {h}" for h in (headlines or []))
            if headlines
            else "- No breaking news reported"
        )
        prompt = (
            f"You are an institutional trading risk officer. An algorithmic trading bot is proposing a {side} entry on {symbol} at ${price:,.2f} via {strategy_id}.\n"
            f"Recent Breaking Headlines:\n{headlines_text}\n\n"
            f"Assess if there are any immediate catastrophic red flags (e.g. accounting fraud, bankruptcy, regulatory ban, immediate earnings call today) that prohibit entering this trade.\n"
            f"Reply strictly in this format:\n"
            f"DECISION: APPROVED or REJECTED\n"
            f"REASON: <concise 1-sentence explanation>"
        )

        try:
            payload = {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.0, "num_predict": 100},
            }
            resp = requests.post(self.endpoint, json=payload, timeout=self.timeout)
            latency = time.time() - t0
            if resp.status_code == 200:
                text = resp.json().get("response", "").strip()
                lines = [line.strip() for line in text.split("\n") if line.strip()]
                decision = "APPROVED"
                reason = "No critical news hazards detected"

                for line in lines:
                    upper = line.upper()
                    if upper.startswith("DECISION:"):
                        if "REJECT" in upper:
                            decision = "REJECTED"
                        elif "APPROV" in upper:
                            decision = "APPROVED"
                    elif upper.startswith("REASON:"):
                        reason = line.split(":", 1)[1].strip()

                return LLMScreenVerdict(
                    approved=(decision == "APPROVED"),
                    reason=reason,
                    latency_seconds=round(latency, 2),
                    model=self.model,
                )
        except Exception as e:
            logger.debug(f"[LocalLLMScreener] Ollama offline or timed out: {e}")

        # Graceful fallback: never stall live trading if local model is offline
        return LLMScreenVerdict(
            approved=True,
            reason="Local LLM offline fallback (bypassed)",
            latency_seconds=round(time.time() - t0, 2),
            model=self.model,
        )


@dataclass
class PreFlightVerdict:
    passed: bool
    reason: str
    symbol: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def validate_order_preflight(
    symbol: str,
    side: str,
    price: float,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    last_quote_time: Optional[datetime] = None,
    hourly_volume: Optional[float] = None,
    proposed_units: float = 0.0,
    available_cash: float = 0.0,
    order_notional: float = 0.0,
    max_spread_pct: float = 0.0015,  # 0.15% maximum allowable bid-ask spread
    max_staleness_seconds: int = 180,  # 3 minutes max tick age
    max_volume_participation_pct: float = 0.02,  # Max 2% of hourly bar volume
    min_notional_usd: float = 10.0,  # Minimum order notional ($10.00)
) -> PreFlightVerdict:
    """Run institutional pre-flight checks on an incoming order proposal."""

    # 1. Minimum Notional Guard
    if order_notional < min_notional_usd:
        return PreFlightVerdict(
            passed=False,
            reason=f"PREFLIGHT_NOTIONAL_TOO_SMALL (${order_notional:.2f} < ${min_notional_usd:.2f})",
            symbol=symbol,
        )

    # 2. Available Cash Guard (Buys must not exceed cash)
    if side.upper() in ("BUY", "LONG") and order_notional > available_cash:
        return PreFlightVerdict(
            passed=False,
            reason=f"PREFLIGHT_INSUFFICIENT_CASH (Order ${order_notional:,.2f} > Cash ${available_cash:,.2f})",
            symbol=symbol,
        )

    # 3. Bid-Ask Spread Guard
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        mid = (bid + ask) / 2.0
        if mid > 0:
            spread_pct = (ask - bid) / mid
            if spread_pct > max_spread_pct:
                return PreFlightVerdict(
                    passed=False,
                    reason=f"PREFLIGHT_SPREAD_TOO_WIDE (Spread {spread_pct:.3%} > limit {max_spread_pct:.3%})",
                    symbol=symbol,
                )

    # 4. Tick Staleness Guard
    if last_quote_time is not None:
        now_utc = datetime.now(timezone.utc)
        if hasattr(last_quote_time, "tzinfo") and last_quote_time.tzinfo is not None:
            age_seconds = (now_utc - last_quote_time).total_seconds()
        else:
            age_seconds = (now_utc.replace(tzinfo=None) - last_quote_time).total_seconds()

        if age_seconds > max_staleness_seconds:
            return PreFlightVerdict(
                passed=False,
                reason=f"PREFLIGHT_PRICE_STALE (Quote age: {age_seconds:.0f}s > {max_staleness_seconds}s limit)",
                symbol=symbol,
            )

    # 5. Liquidity Depth / Volume Participation Guard
    if hourly_volume is not None and hourly_volume > 0 and proposed_units > 0:
        participation = proposed_units / hourly_volume
        if participation > max_volume_participation_pct:
            return PreFlightVerdict(
                passed=False,
                reason=f"PREFLIGHT_EXCEEDS_LIQUIDITY_DEPTH (Participation {participation:.2%} > limit {max_volume_participation_pct:.2%})",
                symbol=symbol,
            )

    return PreFlightVerdict(passed=True, reason="PREFLIGHT_PASSED", symbol=symbol)


class PreFlightChecker:
    """Stateful wrapper for pre-flight execution safety verification."""

    def __init__(
        self,
        max_spread_pct: float = 0.0015,
        max_staleness_seconds: int = 180,
        max_volume_participation_pct: float = 0.02,
        min_notional_usd: float = 10.0,
    ):
        self.max_spread_pct = max_spread_pct
        self.max_staleness_seconds = max_staleness_seconds
        self.max_volume_participation_pct = max_volume_participation_pct
        self.min_notional_usd = min_notional_usd

    def check(
        self,
        symbol: str,
        side: str,
        price: float,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
        last_quote_time: Optional[datetime] = None,
        hourly_volume: Optional[float] = None,
        proposed_units: float = 0.0,
        available_cash: float = 0.0,
        order_notional: float = 0.0,
    ) -> PreFlightVerdict:
        return validate_order_preflight(
            symbol=symbol,
            side=side,
            price=price,
            bid=bid,
            ask=ask,
            last_quote_time=last_quote_time,
            hourly_volume=hourly_volume,
            proposed_units=proposed_units,
            available_cash=available_cash,
            order_notional=order_notional,
            max_spread_pct=self.max_spread_pct,
            max_staleness_seconds=self.max_staleness_seconds,
            max_volume_participation_pct=self.max_volume_participation_pct,
            min_notional_usd=self.min_notional_usd,
        )
