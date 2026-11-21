"""Causal Macro Regime Playbook Engine (extracted from core/coordinator.py).

Classifies the overall market into BULL (>= +4%), CHOPPY (-4%..+4%), or BEAR (<= -4%)
using strictly causal (shift(1) on daily-resampled closes) 20-day returns of the
configured macro benchmarks (default 50/50 BTC/USDT + SPY) and maps each regime to
its empirically verified strategy whitelist.
"""

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

from core.data_loader import is_crypto_symbol
from strategies.donchian_range_fade import DonchianRangeFadeStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumStrategy
from strategies.stat_arb_pairs import StatArbPairsStrategy
from strategies.supertrend_cta import SupertrendCtaStrategy
from strategies.trend_pullback import TrendPullbackStrategy
from strategies.volatility_squeeze import VolatilitySqueezeStrategy


def get_playbook_allowed_strategies(regime: str) -> Set[str]:
    """Return the set of strategy IDs permitted for new entries under the 8-Strategy Regime Playbook.

    - BULL (>= +4%): volatility_squeeze + basis_yield
    - CHOPPY (-4%..+4%): volatility_squeeze + supertrend_cta + momentum + stat_arb_pairs + basis_yield
    - BEAR (<= -4%): mean_reversion + donchian_range_fade + trend_pullback + stat_arb_pairs + basis_yield
    """
    r = str(regime or "CHOPPY").upper()
    if r == "BULL":
        return {VolatilitySqueezeStrategy.STRATEGY_ID, "basis_yield"}
    if r == "BEAR":
        return {
            MeanReversionStrategy.STRATEGY_ID,
            DonchianRangeFadeStrategy.STRATEGY_ID,
            TrendPullbackStrategy.STRATEGY_ID,
            StatArbPairsStrategy.STRATEGY_ID,
            "basis_yield",
        }
    return {
        VolatilitySqueezeStrategy.STRATEGY_ID,
        SupertrendCtaStrategy.STRATEGY_ID,
        MomentumStrategy.STRATEGY_ID,
        StatArbPairsStrategy.STRATEGY_ID,
        "basis_yield",
    }


def extract_causal_daily_return_pct(df: Optional[pd.DataFrame], lookback_days: int) -> Optional[float]:
    """Compute strictly causal trailing N-day return (%) from daily-resampled closes.

    Uses shift(1) on daily closes so an incomplete current day's bar is never used.
    """
    if df is None or len(df) < 2 or "close" not in df.columns:
        return None
    try:
        series = df["close"].astype(float)
        if isinstance(df.index, pd.DatetimeIndex):
            s = series.sort_index()
        elif "timestamp" in df.columns:
            idx = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
            s = pd.Series(series.values, index=idx).dropna().sort_index()
        else:
            return None

        daily = s.resample("1D").last().dropna()
        if len(daily) >= lookback_days + 2:
            shifted = daily.shift(1).dropna()
            prev_close = float(shifted.iloc[-1])
            base_close = float(shifted.iloc[-1 - lookback_days])
        elif len(daily) >= lookback_days + 1:
            prev_close = float(daily.iloc[-1])
            base_close = float(daily.iloc[-1 - lookback_days])
        else:
            return None

        if base_close <= 0.0 or not math.isfinite(prev_close) or not math.isfinite(base_close):
            return None
        return (prev_close / base_close - 1.0) * 100.0
    except Exception:
        return None


def load_macro_benchmark_df(
    symbol: str,
    benchmark_data: Optional[Dict[str, pd.DataFrame]] = None,
    latest_benchmark_data: Optional[Dict[str, pd.DataFrame]] = None,
    market_data: Optional[Dict[str, pd.DataFrame]] = None,
    latest_market_data: Optional[Dict[str, pd.DataFrame]] = None,
    data_dir: Path = Path("data"),
) -> Optional[pd.DataFrame]:
    """Locate benchmark OHLCV DataFrame in memory or fall back to cached data CSVs."""
    for store in (benchmark_data, latest_benchmark_data, market_data, latest_market_data):
        if store and symbol in store and store[symbol] is not None:
            return store[symbol]

    safe = symbol.replace("/", "_")
    prefix = "crypto" if is_crypto_symbol(symbol) else "stock"
    for suffix in ("1h_90d.csv", "1h_365d.csv", "1h_720d.csv", "1d_1825d.csv"):
        path = data_dir / f"{prefix}_{safe}_{suffix}"
        if path.exists():
            try:
                df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
                if len(df) >= 25:
                    return df
            except Exception:
                continue
    return None


def compute_macro_regime(
    config: Dict[str, Any],
    benchmark_data: Optional[Dict[str, pd.DataFrame]] = None,
    latest_benchmark_data: Optional[Dict[str, pd.DataFrame]] = None,
    market_data: Optional[Dict[str, pd.DataFrame]] = None,
    latest_market_data: Optional[Dict[str, pd.DataFrame]] = None,
) -> Tuple[str, float]:
    """Compute causal macro regime ('BULL', 'CHOPPY', 'BEAR') and 20-day 50/50 benchmark return (%)."""
    lookback_days = int(config.get("macro_regime_lookback_days", 20))
    bull_pct = float(config.get("macro_regime_bull_pct", 4.0))
    bear_pct = float(config.get("macro_regime_bear_pct", -4.0))
    benchmarks = config.get("macro_regime_benchmarks", ["BTC/USDT", "SPY"])

    rets: List[float] = []
    for sym in benchmarks:
        bdf = load_macro_benchmark_df(
            sym,
            benchmark_data=benchmark_data,
            latest_benchmark_data=latest_benchmark_data,
            market_data=market_data,
            latest_market_data=latest_market_data,
        )
        r = extract_causal_daily_return_pct(bdf, lookback_days)
        if r is not None:
            rets.append(r)

    if not rets:
        return "CHOPPY", 0.0

    macro_ret = float(sum(rets) / len(rets))
    if macro_ret >= bull_pct:
        regime = "BULL"
    elif macro_ret <= bear_pct:
        regime = "BEAR"
    else:
        regime = "CHOPPY"
    return regime, round(macro_ret, 3)
