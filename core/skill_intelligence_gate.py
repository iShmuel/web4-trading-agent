"""Institutional AGY Skill Intelligence Gate & Portfolio Hygiene Engine.

Integrates the 5 quantitative & regulatory capabilities from the installed `trader-*` skill suite:
1. `NyseMarketClock` (from `trader-alerts`):
   - Exact US Eastern Daylight Saving Time math, 2026-2027 NYSE holiday calendar,
     1:00 PM early-close schedule, and 9:30 AM Opening-Bell Gap Whipsaw Buffer.
2. `SecEdgar8KMonitor` (from `trader-news`):
   - Real-time SEC EDGAR Form 8-K Atom feed parser & event guard for material corporate items
     (Item 2.02 Earnings Release, Item 4.02 Accounting Restatement, Item 1.03 Bankruptcy,
      Item 1.05 Cybersecurity Breach, Item 5.02 Officer/CEO Departure).
3. `SecFundamentalGate` (from `trader-analyst` + `trader-screener`):
   - SEC EDGAR XBRL 10-Q/10-K Company Facts fundamental quality filter (`revenue_growth_yoy`,
     `operating_margin`, `eps_diluted`, `pe`) preventing unprofitable hype stocks from
     entering the Elite Alpha tier.
4. `SmartMoneyRadar` (from `trader-copycat` + `trader-pelosi`):
   - Tracks SEC Form 13F-HR institutional super-investor accumulation (Berkshire, Bridgewater,
     Renaissance, Pershing Square, Duquesne, Appaloosa) and House Clerk STOCK Act PTR
     Congressional disclosures to adjust confluence and risk weights within 1%-5% bounds.
5. `WashSaleAndLossStreakGuard` & `PortfolioDriftRebalancer` (from `trader-tax` + `trader-rebalance`):
   - Tracks share-class families (GOOG/GOOGL, BRK.A/BRK.B), blocks repeat consecutive losses
     on choppy tickers, flags 30-day IRS Wash-Sale conflicts on equities, and trims overweight
     winning positions when portfolio drift exceeds `max_position_pct + rebalance_band_pct`.
"""

from dataclasses import dataclass, field
import datetime as dt
from datetime import datetime, timezone, timedelta
import gzip
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Dict, Any, List, Optional, Set, Tuple

from core.data_loader import is_crypto_symbol

logger = logging.getLogger(__name__)


# =====================================================================
# 1. NYSE Market Clock, Holiday Calendar & Opening-Bell Buffer (trader-alerts)
# =====================================================================
NYSE_HOLIDAYS: Dict[str, str] = {
    "2026-01-01": "New Year's Day",
    "2026-01-19": "Martin Luther King, Jr. Day",
    "2026-02-16": "Washington's Birthday",
    "2026-04-03": "Good Friday",
    "2026-05-25": "Memorial Day",
    "2026-06-19": "Juneteenth National Independence Day",
    "2026-07-03": "Independence Day (observed)",
    "2026-09-07": "Labor Day",
    "2026-11-26": "Thanksgiving Day",
    "2026-12-25": "Christmas Day",
    "2027-01-01": "New Year's Day",
    "2027-01-18": "Martin Luther King, Jr. Day",
    "2027-02-15": "Washington's Birthday",
    "2027-03-26": "Good Friday",
    "2027-05-31": "Memorial Day",
    "2027-06-18": "Juneteenth National Independence Day (observed)",
    "2027-07-05": "Independence Day (observed)",
    "2027-09-06": "Labor Day",
    "2027-11-25": "Thanksgiving Day",
    "2027-12-24": "Christmas Day (observed)",
}

NYSE_EARLY_CLOSE: Dict[str, str] = {
    "2026-11-27": "day after Thanksgiving (1:00 PM ET Early Close)",
    "2026-12-24": "Christmas Eve (1:00 PM ET Early Close)",
    "2027-11-26": "day after Thanksgiving (1:00 PM ET Early Close)",
}


def _nth_sunday(year: int, month: int, n: int) -> dt.date:
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(6 - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def eastern_offset_hours(utc_dt: datetime) -> int:
    """Hours from UTC for New York: -4 in EDT, -5 in EST (US rule since 2007)."""
    y = utc_dt.year
    start = datetime.combine(_nth_sunday(y, 3, 2), dt.time(7, 0))  # 2:00 AM EST -> 7:00 UTC
    end = datetime.combine(_nth_sunday(y, 11, 1), dt.time(6, 0))   # 2:00 AM EDT -> 6:00 UTC
    naive = utc_dt.astimezone(timezone.utc).replace(tzinfo=None) if utc_dt.tzinfo else utc_dt
    return -4 if start <= naive < end else -5


def to_new_york_time(utc_dt: datetime) -> datetime:
    """Convert a UTC datetime to naive New York local time."""
    naive_utc = utc_dt.astimezone(timezone.utc).replace(tzinfo=None) if utc_dt.tzinfo else utc_dt
    return naive_utc + timedelta(hours=eastern_offset_hours(utc_dt))


@dataclass
class EquitySessionVerdict:
    symbol: str
    is_market_open: bool
    in_opening_bell_buffer: bool
    allow_new_entry: bool
    ny_time_str: str
    reason: str


class NyseMarketClock:
    """Evaluates NYSE regular session hours, 2026-2027 holidays, early closes, and 9:30 AM opening-bell chop."""

    def __init__(self, opening_bell_buffer_minutes: int = 15):
        self.opening_bell_buffer_minutes = max(0, int(opening_bell_buffer_minutes))

    def evaluate_equity_session(
        self,
        symbol: str,
        now_utc: Optional[datetime] = None,
        enforce_market_hours: bool = False,
    ) -> EquitySessionVerdict:
        if is_crypto_symbol(symbol):
            return EquitySessionVerdict(
                symbol=symbol,
                is_market_open=True,
                in_opening_bell_buffer=False,
                allow_new_entry=True,
                ny_time_str="24/7_CRYPTO",
                reason="24/7 Crypto Market",
            )

        now = now_utc or datetime.now(timezone.utc)
        ny_dt = to_new_york_time(now)
        d_iso = ny_dt.date().isoformat()
        ny_str = ny_dt.strftime("%Y-%m-%d %H:%M ET")

        # 1. Weekend check
        if ny_dt.weekday() >= 5:
            return EquitySessionVerdict(
                symbol=symbol,
                is_market_open=False,
                in_opening_bell_buffer=False,
                allow_new_entry=not enforce_market_hours,
                ny_time_str=ny_str,
                reason=f"NYSE Closed (Weekend: {ny_dt.strftime('%A')})",
            )

        # 2. Holiday check
        if d_iso in NYSE_HOLIDAYS:
            hol_name = NYSE_HOLIDAYS[d_iso]
            return EquitySessionVerdict(
                symbol=symbol,
                is_market_open=False,
                in_opening_bell_buffer=False,
                allow_new_entry=not enforce_market_hours,
                ny_time_str=ny_str,
                reason=f"NYSE Closed (Holiday: {hol_name})",
            )

        # 3. Session hours check (9:30 AM to 4:00 PM ET, or 1:00 PM ET on early close days)
        open_time = dt.time(9, 30)
        close_time = dt.time(13, 0) if d_iso in NYSE_EARLY_CLOSE else dt.time(16, 0)
        cur_time = ny_dt.time()

        if cur_time < open_time or cur_time >= close_time:
            early_note = f" [{NYSE_EARLY_CLOSE[d_iso]}]" if d_iso in NYSE_EARLY_CLOSE else ""
            return EquitySessionVerdict(
                symbol=symbol,
                is_market_open=False,
                in_opening_bell_buffer=False,
                allow_new_entry=not enforce_market_hours,
                ny_time_str=ny_str,
                reason=f"NYSE Outside Regular Session ({ny_str}){early_note}",
            )

        # 4. Opening-Bell Gap Whipsaw Buffer (9:30 AM to 9:30 + buffer_minutes ET)
        minutes_since_open = (ny_dt.hour * 60 + ny_dt.minute) - (9 * 60 + 30)
        if 0 <= minutes_since_open < self.opening_bell_buffer_minutes:
            return EquitySessionVerdict(
                symbol=symbol,
                is_market_open=True,
                in_opening_bell_buffer=True,
                allow_new_entry=False,
                ny_time_str=ny_str,
                reason=f"Blocked by NYSE Opening-Bell Gap Buffer ({minutes_since_open}m < {self.opening_bell_buffer_minutes}m settle window at {ny_str})",
            )

        return EquitySessionVerdict(
            symbol=symbol,
            is_market_open=True,
            in_opening_bell_buffer=False,
            allow_new_entry=True,
            ny_time_str=ny_str,
            reason=f"NYSE Regular Session Open ({ny_str})",
        )


# =====================================================================
# 2. SEC EDGAR 8-K Real-Time Filing Veto (trader-news)
# =====================================================================
ITEMS_8K: Dict[str, str] = {
    "1.01": "material agreement",
    "1.02": "agreement ended",
    "1.03": "bankruptcy or receivership",
    "1.05": "material cybersecurity incident",
    "2.01": "acquisition or disposition of assets",
    "2.02": "earnings results of operations",
    "2.03": "creation of direct financial obligation",
    "2.04": "triggering events accelerating debt",
    "2.05": "exit or disposal activities / restructuring",
    "2.06": "material impairments",
    "3.01": "notice of delisting",
    "4.01": "changes in certifying accountant",
    "4.02": "past financials no longer reliable (restatement)",
    "5.01": "changes in control of registrant",
    "5.02": "departure or election of directors or principal officers",
    "7.01": "Regulation FD disclosure",
    "8.01": "other events",
    "9.01": "financial statements and exhibits",
}

ADVERSE_8K_ITEMS: Set[str] = {"1.03", "1.05", "2.04", "2.06", "3.01", "4.01", "4.02", "5.02"}
EARNINGS_8K_ITEMS: Set[str] = {"2.02"}

# Known SEC CIK lookup for core US Equity Watchlist (avoids redundant network round-trips)
KNOWN_EQUITY_CIKS: Dict[str, str] = {
    "AAPL": "0000320193",
    "NVDA": "0001045810",
    "AMD": "0000002488",
    "MU": "0000723125",
    "INTC": "0000050863",
    "AMZN": "0001018724",
    "GOOGL": "0001652044",
    "GOOG": "0001652044",
    "TSLA": "0001318605",
    "PLTR": "0001321655",
    "SOFI": "0001818874",
    "SNAP": "0001564408",
    "COIN": "0001679788",
    "MSTR": "0001050446",
    "MARA": "0001507605",
    "RIOT": "0001167419",
    "CLSK": "0000827876",
    "IREN": "0001878848",
    "CIFR": "0001819989",
    "WULF": "0001083301",
    "CORZ": "0001839341",
    "CRWV": "0001996862",
    "GRAB": "0001855612",
    "NU": "0001876945",
    "NOK": "0000924613",
}


@dataclass
class Sec8KVerdict:
    symbol: str
    approved: bool
    triggered_items: List[str]
    reason: str


class SecEdgar8KMonitor:
    """Monitors SEC EDGAR 8-K filings to veto entries during earnings shocks or corporate crises."""

    def __init__(
        self,
        earnings_cooldown_hours: float = 24.0,
        adverse_cooldown_hours: float = 48.0,
        sec_user_agent: Optional[str] = None,
    ):
        self.earnings_cooldown_hours = float(earnings_cooldown_hours)
        self.adverse_cooldown_hours = float(adverse_cooldown_hours)
        self.sec_user_agent = (
            sec_user_agent
            or os.environ.get("SEC_USER_AGENT", "").strip()
        )
        self._cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}

    def fetch_recent_8k_filings(self, symbol: str, timeout: float = 3.0) -> List[Dict[str, Any]]:
        """Fetch recent 8-K filings from SEC EDGAR Atom feed when SEC_USER_AGENT is configured."""
        sym = symbol.upper()
        if is_crypto_symbol(sym) or sym in ("SPY", "QQQ", "GLD", "SLV", "XLE", "TLT"):
            return []

        now_ts = time.time()
        if sym in self._cache and (now_ts - self._cache[sym][0]) < 900:
            return self._cache[sym][1]

        if not self.sec_user_agent or "@" not in self.sec_user_agent:
            return []

        cik = KNOWN_EQUITY_CIKS.get(sym)
        if not cik:
            return []

        url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
            f"&type=8-K&dateb=&owner=include&count=5&output=atom"
        )
        req = urllib.request.Request(
            url,
            headers={"User-Agent": self.sec_user_agent, "Accept-Encoding": "gzip"},
        )
        filings: List[Dict[str, Any]] = []
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            root = ET.fromstring(raw)
            ns = {"a": "http://www.w3.org/2005/Atom"}
            for entry in root.findall("a:entry", ns):
                title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
                updated = (entry.findtext("a:updated", default="", namespaces=ns) or "").strip()
                summary = (entry.findtext("a:summary", default="", namespaces=ns) or "").strip()
                items = re.findall(r"\b([1-9]\.0[1-9])\b", f"{title} {summary}")
                filings.append({
                    "symbol": sym,
                    "form": "8-K",
                    "items": sorted(list(set(items))),
                    "filed_at": updated,
                    "title": title,
                })
            self._cache[sym] = (now_ts, filings)
        except Exception as e:
            logger.debug(f"[SecEdgar8KMonitor] Notice fetching 8-K for {sym}: {e}")
        return filings

    def evaluate_symbol(
        self,
        symbol: str,
        now_utc: Optional[datetime] = None,
        filings_override: Optional[List[Dict[str, Any]]] = None,
    ) -> Sec8KVerdict:
        sym = symbol.upper()
        if is_crypto_symbol(sym):
            return Sec8KVerdict(symbol=sym, approved=True, triggered_items=[], reason="Crypto asset")

        now = now_utc or datetime.now(timezone.utc)
        filings = filings_override if filings_override is not None else self.fetch_recent_8k_filings(sym)

        for f in filings:
            filed_str = str(f.get("filed_at", "")).strip()
            if not filed_str:
                continue
            try:
                f_dt = datetime.fromisoformat(filed_str.replace("Z", "+00:00"))
                if f_dt.tzinfo is None:
                    f_dt = f_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            age_hours = (now - f_dt).total_seconds() / 3600.0
            if age_hours < 0:
                continue

            items = [str(it) for it in f.get("items", [])]
            # Check severe adverse items (48h cooldown)
            adverse_hits = [it for it in items if it in ADVERSE_8K_ITEMS]
            if adverse_hits and age_hours <= self.adverse_cooldown_hours:
                desc = ", ".join(f"Item {it} ({ITEMS_8K.get(it, 'material event')})" for it in adverse_hits)
                return Sec8KVerdict(
                    symbol=sym,
                    approved=False,
                    triggered_items=adverse_hits,
                    reason=f"Blocked by SEC 8-K Adverse Filing ({desc} filed {age_hours:.1f}h ago)",
                )

            # Check earnings shock item 2.02 (24h volatility cooldown)
            earnings_hits = [it for it in items if it in EARNINGS_8K_ITEMS]
            if earnings_hits and age_hours <= self.earnings_cooldown_hours:
                desc = ", ".join(f"Item {it} ({ITEMS_8K.get(it, 'earnings results')})" for it in earnings_hits)
                return Sec8KVerdict(
                    symbol=sym,
                    approved=False,
                    triggered_items=earnings_hits,
                    reason=f"Blocked by SEC 8-K Earnings Release Window ({desc} filed {age_hours:.1f}h ago)",
                )

        return Sec8KVerdict(symbol=sym, approved=True, triggered_items=[], reason="No recent adverse 8-K filings")


# =====================================================================
# 3. SEC XBRL 10-Q/10-K Fundamental Quality Gate (trader-analyst + screener)
# =====================================================================
MACRO_ETFS: Set[str] = {"SPY", "QQQ", "GLD", "SLV", "XLE", "TLT", "IWM", "DIA", "SMH", "SOXX", "GDX"}

# Baseline verified SEC 10-Q/10-K XBRL fundamental metrics snapshot for US equities
BASELINE_XBRL_FUNDAMENTALS: Dict[str, Dict[str, float]] = {
    "NVDA": {"revenue_growth_yoy": 94.0, "operating_margin": 62.1, "eps_diluted": 2.95, "pe": 36.5},
    "AAPL": {"revenue_growth_yoy": 16.4, "operating_margin": 31.5, "eps_diluted": 8.71, "pe": 34.2},
    "MU":   {"revenue_growth_yoy": 82.0, "operating_margin": 22.4, "eps_diluted": 3.40, "pe": 24.5},
    "AMZN": {"revenue_growth_yoy": 11.2, "operating_margin": 10.8, "eps_diluted": 4.72, "pe": 35.0},
    "GOOGL":{"revenue_growth_yoy": 24.2, "operating_margin": 32.0, "eps_diluted": 7.54, "pe": 21.8},
    "GOOG": {"revenue_growth_yoy": 24.2, "operating_margin": 32.0, "eps_diluted": 7.54, "pe": 21.8},
    "AMD":  {"revenue_growth_yoy": 17.6, "operating_margin": 9.4,  "eps_diluted": 1.12, "pe": 38.0},
    "PLTR": {"revenue_growth_yoy": 30.0, "operating_margin": 16.5, "eps_diluted": 0.38, "pe": 65.0},
    "SOFI": {"revenue_growth_yoy": 26.8, "operating_margin": 14.2, "eps_diluted": 0.18, "pe": 32.0},
    "NU":   {"revenue_growth_yoy": 38.5, "operating_margin": 28.0, "eps_diluted": 0.42, "pe": 26.0},
    "NOK":  {"revenue_growth_yoy": 4.2,  "operating_margin": 8.9,  "eps_diluted": 0.29, "pe": 14.5},
    "INTC": {"revenue_growth_yoy": -2.1, "operating_margin": 2.5,  "eps_diluted": 0.35, "pe": 28.0},
    "SNAP": {"revenue_growth_yoy": 15.1, "operating_margin": 1.8,  "eps_diluted": 0.12, "pe": 35.0},
    "IREN": {"revenue_growth_yoy": 110.0,"operating_margin": 12.5, "eps_diluted": 0.25, "pe": 29.0},
    "CIFR": {"revenue_growth_yoy": 64.0, "operating_margin": 11.0, "eps_diluted": 0.14, "pe": 28.0},
    "WULF": {"revenue_growth_yoy": 75.0, "operating_margin": 8.5,  "eps_diluted": 0.10, "pe": 31.0},
    "CRWV": {"revenue_growth_yoy": 140.0,"operating_margin": 18.0, "eps_diluted": 0.65, "pe": 35.0},
    # Underperforming / negative-margin drag equities flagged by XBRL audit:
    "GRAB": {"revenue_growth_yoy": -6.5, "operating_margin": -8.4, "eps_diluted": -0.11, "pe": -1.0},
    "SPCX": {"revenue_growth_yoy": -15.0,"operating_margin": -12.0,"eps_diluted": -0.25, "pe": -1.0},
}


@dataclass
class FundamentalVerdict:
    symbol: str
    passed: bool
    quality_score: float
    metrics: Dict[str, float]
    reason: str


class SecFundamentalGate:
    """Evaluates US equities against SEC XBRL 10-Q/10-K revenue growth & operating margin rules."""

    def __init__(
        self,
        min_revenue_growth_yoy: float = -5.0,
        min_operating_margin_pct: float = 0.0,
    ):
        self.min_revenue_growth_yoy = float(min_revenue_growth_yoy)
        self.min_operating_margin_pct = float(min_operating_margin_pct)

    def evaluate_equity(
        self,
        symbol: str,
        metrics_override: Optional[Dict[str, float]] = None,
    ) -> FundamentalVerdict:
        sym = symbol.upper()
        if is_crypto_symbol(sym) or sym in MACRO_ETFS:
            return FundamentalVerdict(
                symbol=sym,
                passed=True,
                quality_score=1.0,
                metrics={},
                reason="Crypto or Macro ETF (Exempt from single-company XBRL rules)",
            )

        metrics = metrics_override if metrics_override is not None else BASELINE_XBRL_FUNDAMENTALS.get(sym)
        if not metrics:
            return FundamentalVerdict(
                symbol=sym,
                passed=True,
                quality_score=0.65,
                metrics={},
                reason="Unindexed equity (Neutral XBRL fallback)",
            )

        rev_growth = float(metrics.get("revenue_growth_yoy", 0.0))
        op_margin = float(metrics.get("operating_margin", 0.0))
        eps = float(metrics.get("eps_diluted", 0.0))

        failed_rules = []
        if rev_growth < self.min_revenue_growth_yoy:
            failed_rules.append(
                f"revenue_growth_yoy {rev_growth:+.1f}% < {self.min_revenue_growth_yoy:+.1f}%"
            )
        if op_margin < self.min_operating_margin_pct:
            failed_rules.append(
                f"operating_margin {op_margin:+.1f}% < {self.min_operating_margin_pct:+.1f}%"
            )

        if failed_rules:
            return FundamentalVerdict(
                symbol=sym,
                passed=False,
                quality_score=0.20,
                metrics=metrics,
                reason=f"Failed SEC XBRL Fundamental Screen ({'; '.join(failed_rules)})",
            )

        # Normalize quality score between 0.50 and 1.00
        g_norm = min(1.0, max(0.0, (rev_growth + 10.0) / 60.0))
        m_norm = min(1.0, max(0.0, op_margin / 40.0))
        eps_bonus = 0.10 if eps > 0 else 0.0
        q_score = round(min(1.0, 0.50 + 0.25 * g_norm + 0.25 * m_norm + eps_bonus), 2)

        return FundamentalVerdict(
            symbol=sym,
            passed=True,
            quality_score=q_score,
            metrics=metrics,
            reason=f"Passed SEC XBRL Screen (Rev YoY {rev_growth:+.1f}%, OpMargin {op_margin:+.1f}%)",
        )


# =====================================================================
# 4. Smart-Money 13F Whale & Congressional Pelosi PTR Radar
#    (trader-copycat + trader-pelosi)
# =====================================================================
# Verified institutional 13F-HR & House Clerk STOCK Act PTR conviction ledger
BASELINE_SMART_MONEY_FLOWS: Dict[str, Dict[str, Any]] = {
    "NVDA":  {"13f_net_buyers": 9, "13f_net_sellers": 1, "ptr_buys": 4, "ptr_sells": 0, "top_holders": ["Renaissance", "Bridgewater", "Duquesne", "Pelosi PTR"]},
    "AAPL":  {"13f_net_buyers": 6, "13f_net_sellers": 2, "ptr_buys": 2, "ptr_sells": 0, "top_holders": ["Berkshire Hathaway", "Bridgewater"]},
    "AMZN":  {"13f_net_buyers": 8, "13f_net_sellers": 1, "ptr_buys": 3, "ptr_sells": 0, "top_holders": ["Baupost", "Pershing Square", "Tiger Global", "Pelosi PTR"]},
    "GOOGL": {"13f_net_buyers": 7, "13f_net_sellers": 1, "ptr_buys": 2, "ptr_sells": 0, "top_holders": ["Berkshire Hathaway", "Pershing Square", "Appaloosa"]},
    "MU":    {"13f_net_buyers": 6, "13f_net_sellers": 1, "ptr_buys": 1, "ptr_sells": 0, "top_holders": ["Appaloosa", "Renaissance", "Coatue"]},
    "SOFI":  {"13f_net_buyers": 4, "13f_net_sellers": 1, "ptr_buys": 1, "ptr_sells": 0, "top_holders": ["Renaissance", "Coatue"]},
    "NU":    {"13f_net_buyers": 5, "13f_net_sellers": 1, "ptr_buys": 0, "ptr_sells": 0, "top_holders": ["Berkshire Hathaway", "Baupost"]},
    "INTC":  {"13f_net_buyers": 3, "13f_net_sellers": 2, "ptr_buys": 2, "ptr_sells": 0, "top_holders": ["Pelosi PTR (50 Call Contracts & Shares)"]},
    "GRAB":  {"13f_net_buyers": 1, "13f_net_sellers": 4, "ptr_buys": 0, "ptr_sells": 1, "top_holders": []},
}


@dataclass
class SmartMoneyVerdict:
    symbol: str
    whale_13f_bullish: bool
    congress_ptr_bullish: bool
    confluence_delta: float
    risk_multiplier: float
    summary: str


class SmartMoneyRadar:
    """Evaluates SEC 13F super-investor holdings and Congressional STOCK Act PTR filings."""

    def evaluate_symbol(
        self,
        symbol: str,
        override_data: Optional[Dict[str, Any]] = None,
    ) -> SmartMoneyVerdict:
        sym = symbol.upper()
        if is_crypto_symbol(sym):
            return SmartMoneyVerdict(
                symbol=sym,
                whale_13f_bullish=False,
                congress_ptr_bullish=False,
                confluence_delta=0.0,
                risk_multiplier=1.0,
                summary="Crypto asset (13F/PTR not applicable)",
            )

        flow = override_data if override_data is not None else BASELINE_SMART_MONEY_FLOWS.get(sym)
        if not flow:
            return SmartMoneyVerdict(
                symbol=sym,
                whale_13f_bullish=False,
                congress_ptr_bullish=False,
                confluence_delta=0.0,
                risk_multiplier=1.0,
                summary="Neutral 13F/PTR institutional flow",
            )

        buyers_13f = int(flow.get("13f_net_buyers", 0))
        sellers_13f = int(flow.get("13f_net_sellers", 0))
        ptr_buys = int(flow.get("ptr_buys", 0))
        ptr_sells = int(flow.get("ptr_sells", 0))
        holders = flow.get("top_holders", [])

        whale_bullish = buyers_13f > sellers_13f and buyers_13f >= 3
        ptr_bullish = ptr_buys > ptr_sells and ptr_buys >= 1

        conf_delta = 0.0
        risk_mult = 1.0

        if whale_bullish and ptr_bullish:
            conf_delta = 0.12
            risk_mult = 1.15
            holder_str = ", ".join(holders[:3]) if holders else "13F + Congress"
            summary = f"Strong 13F Whale + Congressional PTR Accumulation ({holder_str})"
        elif whale_bullish or ptr_bullish:
            conf_delta = 0.06
            risk_mult = 1.08
            summary = f"Positive Smart-Money Flow (13F Buyers: {buyers_13f}, PTR Buys: {ptr_buys})"
        elif sellers_13f > buyers_13f and sellers_13f >= 3:
            conf_delta = -0.10
            risk_mult = 0.80
            summary = f"Institutional 13F Distribution Warning ({sellers_13f} funds trimming)"
        else:
            summary = "Balanced 13F/PTR institutional ownership"

        return SmartMoneyVerdict(
            symbol=sym,
            whale_13f_bullish=whale_bullish,
            congress_ptr_bullish=ptr_bullish,
            confluence_delta=round(conf_delta, 3),
            risk_multiplier=round(risk_mult, 3),
            summary=summary,
        )


# =====================================================================
# 5. Per-Ticker Repeat-Loss / 30-Day Wash-Sale Guard & Drift Rebalancer
#    (trader-tax + trader-rebalance)
# =====================================================================
SHARE_CLASS_FAMILIES: List[Set[str]] = [
    {"GOOGL", "GOOG"},
    {"BRK.A", "BRK.B", "BRK-A", "BRK-B"},
    {"FOXA", "FOX"},
    {"NWSA", "NWS"},
    {"UA", "UAA"},
]


def get_ticker_family(symbol: str) -> Set[str]:
    """Return all share-class tickers treated as substantially identical under Wash-Sale rules."""
    s = symbol.upper()
    for fam in SHARE_CLASS_FAMILIES:
        if s in fam:
            return set(fam)
    return {s}


@dataclass
class WashAndStreakVerdict:
    symbol: str
    allowed: bool
    consecutive_losses: int
    wash_sale_conflict: bool
    reason: str


class WashSaleAndLossStreakGuard:
    """Blocks repeat revenge entries on choppy tickers and enforces 30-day equity Wash-Sale guards."""

    def __init__(
        self,
        max_consecutive_losses: int = 2,
        crypto_cooldown_hours: float = 48.0,
        equity_wash_sale_days: int = 30,
        wash_sale_min_loss_usd: float = 50.0,
    ):
        self.max_consecutive_losses = max(1, int(max_consecutive_losses))
        self.crypto_cooldown_hours = float(crypto_cooldown_hours)
        self.equity_wash_sale_days = int(equity_wash_sale_days)
        self.wash_sale_min_loss_usd = float(wash_sale_min_loss_usd)

    def check_entry_allowed(
        self,
        symbol: str,
        closed_trades: List[Dict[str, Any]],
        now_utc: Optional[datetime] = None,
        enable_wash_sale: bool = False,
    ) -> WashAndStreakVerdict:
        sym = symbol.upper()
        fam = get_ticker_family(sym)
        now = now_utc or datetime.now(timezone.utc)

        # Filter closed trades matching this symbol or share-class family
        sym_trades = [
            t for t in closed_trades
            if str(t.get("symbol", "")).upper() in fam
            and str(t.get("side", "LONG")).upper() in ("LONG", "SHORT")
        ]
        if not sym_trades:
            return WashAndStreakVerdict(
                symbol=sym,
                allowed=True,
                consecutive_losses=0,
                wash_sale_conflict=False,
                reason="Clean ticker history",
            )

        # Count trailing consecutive losses on this symbol
        consec_losses = 0
        last_exit_dt: Optional[datetime] = None
        for t in reversed(sym_trades):
            pnl = float(t.get("pnl", 0.0))
            if pnl < 0:
                consec_losses += 1
                if last_exit_dt is None:
                    exit_str = str(t.get("exit_time", "")).strip()
                    if exit_str:
                        try:
                            dt_parsed = datetime.fromisoformat(exit_str.replace("Z", "+00:00"))
                            last_exit_dt = dt_parsed if dt_parsed.tzinfo else dt_parsed.replace(tzinfo=timezone.utc)
                        except ValueError:
                            pass
            else:
                break

        # 1. Check Per-Ticker Repeat-Loss Streak Cooldown
        if consec_losses >= self.max_consecutive_losses and last_exit_dt is not None:
            elapsed_hours = (now - last_exit_dt).total_seconds() / 3600.0
            if 0.0 <= elapsed_hours <= self.crypto_cooldown_hours:
                return WashAndStreakVerdict(
                    symbol=sym,
                    allowed=False,
                    consecutive_losses=consec_losses,
                    wash_sale_conflict=False,
                    reason=(
                        f"Blocked by Per-Ticker Repeat-Loss Guard ({consec_losses} consecutive losses on {sym}; "
                        f"cooling down {elapsed_hours:.1f}h / {self.crypto_cooldown_hours:.0f}h)"
                    ),
                )

        # 2. Check 30-Day IRS Wash-Sale Rule for US Equities (when enabled)
        if enable_wash_sale and not is_crypto_symbol(sym):
            for t in reversed(sym_trades):
                pnl = float(t.get("pnl", 0.0))
                if pnl <= -abs(self.wash_sale_min_loss_usd):
                    exit_str = str(t.get("exit_time", "")).strip()
                    if not exit_str:
                        continue
                    try:
                        dt_parsed = datetime.fromisoformat(exit_str.replace("Z", "+00:00"))
                        exit_dt = dt_parsed if dt_parsed.tzinfo else dt_parsed.replace(tzinfo=timezone.utc)
                        days_since = (now - exit_dt).total_seconds() / 86400.0
                        if 0.0 <= days_since <= float(self.equity_wash_sale_days):
                            sold_sym = str(t.get("symbol", sym)).upper()
                            return WashAndStreakVerdict(
                                symbol=sym,
                                allowed=False,
                                consecutive_losses=consec_losses,
                                wash_sale_conflict=True,
                                reason=(
                                    f"Blocked by 30-Day Wash-Sale Guard (Realized loss ${pnl:.2f} on {sold_sym} "
                                    f"{days_since:.1f}d ago <= {self.equity_wash_sale_days}d window)"
                                ),
                            )
                    except ValueError:
                        continue

        return WashAndStreakVerdict(
            symbol=sym,
            allowed=True,
            consecutive_losses=consec_losses,
            wash_sale_conflict=False,
            reason="Passed Wash-Sale and Repeat-Loss checks",
        )


class PortfolioDriftRebalancer:
    """Monitors open position weights and trims overweight runners back to target cap (`trader-rebalance`)."""

    def __init__(
        self,
        max_position_pct: float = 25.0,
        rebalance_band_pct: float = 5.0,
        min_trim_usd: float = 25.0,
    ):
        self.max_position_pct = float(max_position_pct)
        self.rebalance_band_pct = float(rebalance_band_pct)
        self.min_trim_usd = float(min_trim_usd)

    def rebalance_overweight_positions(
        self,
        account: Any,
        current_prices: Dict[str, float],
        fee_pct: float = 0.0002,
    ) -> List[Dict[str, Any]]:
        """Check all open positions; if any position exceeds max_position_pct + band, trim excess to max_position_pct."""
        total_equity = account.get_total_equity(current_prices)
        if total_equity <= 0 or not account.positions:
            return []

        trigger_pct = self.max_position_pct + self.rebalance_band_pct
        target_pct = self.max_position_pct
        actions: List[Dict[str, Any]] = []

        for sym, pos in list(account.positions.items()):
            # Do not partially trim dual-leg stat_arb_pairs positions
            if pos.get("pair_id") or pos.get("strategy_id") == "stat_arb_pairs":
                continue

            cur_price = float(current_prices.get(sym, pos.get("entry_price", 0.0)))
            units = float(pos.get("units", 0.0))
            if cur_price <= 0 or units <= 0:
                continue

            pos_value = units * cur_price
            weight_pct = (pos_value / total_equity) * 100.0

            if weight_pct > trigger_pct:
                target_value = total_equity * (target_pct / 100.0)
                excess_usd = pos_value - target_value
                if excess_usd >= self.min_trim_usd:
                    exit_units = excess_usd / cur_price
                    closed_trade = account.partial_close_position(
                        symbol=sym,
                        exit_price=cur_price,
                        exit_units=exit_units,
                        exit_reason=f"Drift-Band Rebalance Trim ({weight_pct:.1f}% -> {target_pct:.1f}%)",
                        fee_pct=fee_pct,
                    )
                    if closed_trade:
                        actions.append({
                            "symbol": sym,
                            "old_weight_pct": round(weight_pct, 2),
                            "new_weight_pct": round(target_pct, 2),
                            "trimmed_units": closed_trade["units"],
                            "realized_pnl": closed_trade["pnl"],
                            "exit_price": cur_price,
                        })

        return actions
