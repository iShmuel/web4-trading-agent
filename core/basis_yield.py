"""Delta-Neutral Basis Arbitrage & Perpetual Funding Rate Yield Engine.

Harvests market-independent cash yield by exploiting the positive funding rate
paid by perpetual futures longs to shorts across crypto assets.

By simultaneously holding Long Spot and Short Perp (or earning on idle treasury cash
hedged against market risk), delta exposure is 0.0 while collecting 8% - 18% APY
baseline cash flow.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import time

from core.data_loader import fetch_funding_rate, is_crypto_symbol
from core.paper_account import PaperAccount


@dataclass
class BasisOpportunity:
    symbol: str
    funding_rate_8h: float
    annualized_apr: float
    is_profitable: bool
    hurdle_apr: float = 8.0
    regime: str = "NEUTRAL"
    recommended_allocation_pct: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class BasisYieldEngine:
    """Manages Delta-Neutral Cash-and-Carry / Funding Arbitrage Yield."""

    def __init__(
        self,
        hurdle_apr: float = 8.0,
        max_portfolio_allocation_pct: float = 0.30,
        funding_cache_ttl_seconds: int = 3600,
    ):
        self.hurdle_apr = hurdle_apr
        self.max_portfolio_allocation_pct = max_portfolio_allocation_pct
        self.funding_cache_ttl = funding_cache_ttl_seconds
        # In-memory cache: symbol -> (timestamp, funding_rate)
        self._funding_cache: Dict[str, tuple[float, Optional[float]]] = {}
        # Cumulative yield accrued in current session
        self.total_accrued_yield: float = 0.0

    def get_funding_rate(self, symbol: str) -> Optional[float]:
        """Fetch funding rate with TTL caching to prevent exchange rate-limiting."""
        if not is_crypto_symbol(symbol):
            return None

        now = time.time()
        if symbol in self._funding_cache:
            ts, rate = self._funding_cache[symbol]
            if (now - ts) < self.funding_cache_ttl:
                return rate

        rate = fetch_funding_rate(symbol)
        self._funding_cache[symbol] = (now, rate)
        return rate

    def evaluate_opportunity(
        self,
        symbol: str,
        funding_rate: Optional[float] = None,
    ) -> BasisOpportunity:
        """Evaluate whether a crypto asset presents a profitable basis yield harvest."""
        if funding_rate is None:
            funding_rate = self.get_funding_rate(symbol)

        rate = funding_rate if funding_rate is not None else 0.0
        # 3 funding periods per 24 hours (every 8 hours) * 365 days
        annualized_apr = rate * 3.0 * 365.0 * 100.0

        is_profitable = annualized_apr >= self.hurdle_apr
        if annualized_apr >= 25.0:
            regime = "EUPHORIC_YIELD"
            alloc = min(self.max_portfolio_allocation_pct, 0.25)
        elif annualized_apr >= self.hurdle_apr:
            regime = "ATTRACTIVE_YIELD"
            alloc = min(self.max_portfolio_allocation_pct, 0.15)
        elif annualized_apr > 0:
            regime = "LOW_YIELD"
            alloc = 0.0
        else:
            regime = "INVERTED_NEGATIVE"
            alloc = 0.0

        return BasisOpportunity(
            symbol=symbol,
            funding_rate_8h=rate,
            annualized_apr=round(annualized_apr, 2),
            is_profitable=is_profitable,
            hurdle_apr=self.hurdle_apr,
            regime=regime,
            recommended_allocation_pct=alloc,
        )

    def scan_opportunities(self, symbols: List[str]) -> List[BasisOpportunity]:
        """Scan active watchlist for high-APR delta-neutral yield candidates."""
        results = []
        for sym in symbols:
            if is_crypto_symbol(sym):
                opp = self.evaluate_opportunity(sym)
                results.append(opp)
        # Sort highest APR first
        results.sort(key=lambda x: x.annualized_apr, reverse=True)
        return results

    def accrue_funding_yield(
        self,
        account: PaperAccount,
        symbol: str,
        notional_capital: float,
        funding_rate_8h: Optional[float] = None,
        hours_elapsed: float = 8.0,
    ) -> Dict[str, Any]:
        """Accrue simulated delta-neutral funding payment into paper account balance.

        Returns receipt dict with payout details.
        """
        if funding_rate_8h is None:
            funding_rate_8h = self.get_funding_rate(symbol)

        rate = funding_rate_8h if funding_rate_8h is not None else 0.0
        if rate <= 0 or notional_capital <= 0:
            return {
                "symbol": symbol,
                "accrued_payout": 0.0,
                "funding_rate": rate,
                "status": "SKIPPED",
            }

        fraction = hours_elapsed / 8.0
        payout = notional_capital * rate * fraction

        # Credit paper account cash balance
        account.state["cash"] = float(account.state.get("cash", 0.0)) + payout
        self.total_accrued_yield += payout

        # Record trade in account closed_trades log for full accounting transparency
        if "closed_trades" not in account.state:
            account.state["closed_trades"] = []

        account.state["closed_trades"].append({
            "symbol": symbol,
            "side": "BASIS_YIELD",
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "exit_time": datetime.now(timezone.utc).isoformat(),
            "entry_price": 1.0,
            "exit_price": 1.0 + (rate * fraction),
            "units": notional_capital,
            "dollar_risk": 0.0,
            "pnl": round(payout, 4),
            "return_pct": round(rate * fraction * 100.0, 4),
            "exit_reason": f"Basis Yield Accrual ({rate*100:+.3f}% 8h)",
            "fees": 0.0,
            "strategy_id": "basis_yield",
        })
        account._save()

        return {
            "symbol": symbol,
            "accrued_payout": round(payout, 4),
            "funding_rate": rate,
            "annualized_apr": round(rate * 3 * 365 * 100, 2),
            "status": "ACCRUED",
            "total_accrued_yield": round(self.total_accrued_yield, 4),
        }
