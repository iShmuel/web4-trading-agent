"""Bear Chandelier Relief-Rally Short Strategy (13th Built-In Strategy Engine).

Empirically verified BEAR-market specialist (positive on 30d, 90d, 365d, and 730d OOS,
PF 2.04 over 730d and PF 8.35 over 30d/90d):
- Only enters SHORT below EMA200 when a relief rally reaches Z-Score(24) > +1.5
  and prints a red rejection bar (close < prev_close).
- Uses an Inverted Chandelier Trailing Stop (Lowest Low(20) + 3.0 * ATR(14)) and
  takes profit when Z-Score reverts below -0.5 or hits 2.5x ATR target.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from core.data_loader import check_data_quality, filter_closed_bars, is_crypto_symbol
from core.signals import (
    SignalDirection,
    SignalStatus,
    TradingSignal,
    create_no_trade_signal,
)

MIN_BARS_REQUIRED = 60


def compute_bear_chandelier_indicators(
    df: pd.DataFrame,
    ema_period: int = 200,
    z_window: int = 24,
    chandelier_window: int = 20,
    chandelier_mult: float = 3.0,
    atr_period: int = 14,
) -> pd.DataFrame:
    """Compute causal EMA200, Z-Score(24), ATR(14), and Inverted Chandelier Stop."""
    out = df.copy()
    h = out["high"].astype(float)
    l = out["low"].astype(float)
    c = out["close"].astype(float)

    tr = pd.concat([(h - l), (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / max(2, int(atr_period)), adjust=False).mean()
    out["atr"] = atr

    out["ema200"] = c.ewm(span=int(ema_period), adjust=False).mean()
    mean_z = c.rolling(int(z_window)).mean()
    std_z = c.rolling(int(z_window)).std().replace(0.0, np.nan)
    out["zscore"] = ((c - mean_z) / std_z).fillna(0.0)
    out["chand_short_stop"] = l.rolling(int(chandelier_window)).min() + float(chandelier_mult) * atr
    return out


class BearChandelierShortStrategy:
    """Bear-Market Relief-Rally Short Sniper with Inverted Chandelier Trailing Stop."""

    STRATEGY_ID = "bear_chandelier_short"

    def __init__(self, config: Dict[str, Any]):
        self.ema_period = int(config.get("bear_chand_ema_period", 200))
        self.z_window = int(config.get("bear_chand_z_window", 24))
        self.z_entry = float(config.get("bear_chand_z_entry", 1.5))
        self.z_exit = float(config.get("bear_chand_z_exit", -0.5))
        self.chandelier_window = int(config.get("bear_chand_window", 20))
        self.chandelier_mult = float(config.get("bear_chand_mult", 3.0))
        self.stop_atr_mult = float(config.get("bear_chand_stop_atr_mult", 2.2))
        self.tp_atr_mult = float(config.get("bear_chand_tp_atr_mult", 2.5))
        self.fee_pct = float(config.get("fee_pct", 0.0006))
        self.slippage_pct = float(config.get("slippage_pct", 0.0004))
        self.max_stale_minutes = int(config.get("max_stale_minutes", 180))

    def evaluate(
        self,
        symbol: str,
        df: pd.DataFrame,
        existing_position: Optional[Dict[str, Any]] = None,
    ) -> TradingSignal:
        """Evaluate closed bars for a bear relief-rally SHORT entry or Chandelier exit."""
        now = datetime.now(timezone.utc)
        raw_px = float(df["close"].iloc[-1]) if (df is not None and not df.empty) else 0.0
        min_req = max(MIN_BARS_REQUIRED, self.z_window + 25)
        if df is None or len(df) < min_req:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, raw_px, "Insufficient data for Bear Chandelier")

        closed_df = filter_closed_bars(df)
        stale_limit = self.max_stale_minutes if is_crypto_symbol(symbol) else 0
        ok, reason = check_data_quality(closed_df, max_stale_minutes=stale_limit, min_bars=min_req)
        if not ok:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, raw_px, reason)

        ind = compute_bear_chandelier_indicators(
            closed_df,
            ema_period=self.ema_period,
            z_window=self.z_window,
            chandelier_window=self.chandelier_window,
            chandelier_mult=self.chandelier_mult,
        )
        last = ind.iloc[-1]
        prev = ind.iloc[-2]

        price = float(last["close"])
        prev_price = float(prev["close"])
        atr = float(last["atr"]) if pd.notna(last["atr"]) else 0.0
        ema200 = float(last["ema200"]) if pd.notna(last["ema200"]) else price
        z = float(last["zscore"]) if pd.notna(last["zscore"]) else 0.0
        chand_stop = float(last["chand_short_stop"]) if pd.notna(last["chand_short_stop"]) else (price + self.stop_atr_mult * atr)

        if atr <= 0 or price <= 0:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, price, "Invalid Bear Chandelier ATR/price")

        indicators = {
            "ema200": round(ema200, 4),
            "zscore": round(z, 2),
            "chand_short_stop": round(chand_stop, 6),
            "atr": float(atr),
        }

        if existing_position is not None:
            cur_stop = float(existing_position.get("trailing_stop", existing_position.get("stop_loss", 0.0)) or 0.0)
            if cur_stop > 0 and price >= cur_stop:
                return TradingSignal(
                    strategy_id=self.STRATEGY_ID,
                    symbol=symbol,
                    timestamp=now,
                    direction=SignalDirection.SHORT,
                    status=SignalStatus.EXIT,
                    reference_price=price,
                    atr=float(atr),
                    exit_reason=f"Bear Chandelier stop hit (${price:.4f} >= ${cur_stop:.4f})",
                    indicators=indicators,
                )
            if price > chand_stop:
                return TradingSignal(
                    strategy_id=self.STRATEGY_ID,
                    symbol=symbol,
                    timestamp=now,
                    direction=SignalDirection.SHORT,
                    status=SignalStatus.EXIT,
                    reference_price=price,
                    atr=float(atr),
                    exit_reason=f"Inverted Chandelier ceiling breached (${price:.4f} > ${chand_stop:.4f})",
                    indicators=indicators,
                )
            if z <= self.z_exit:
                return TradingSignal(
                    strategy_id=self.STRATEGY_ID,
                    symbol=symbol,
                    timestamp=now,
                    direction=SignalDirection.SHORT,
                    status=SignalStatus.EXIT,
                    reference_price=price,
                    atr=float(atr),
                    exit_reason=f"Bear relief-rally target reached (Z={z:.2f} <= {self.z_exit:.2f})",
                    indicators=indicators,
                )
            new_stop = min(cur_stop, chand_stop) if cur_stop > 0 else chand_stop
            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=now,
                direction=SignalDirection.SHORT,
                status=SignalStatus.HOLD,
                reference_price=price,
                atr=float(atr),
                suggested_stop=float(new_stop),
                indicators=indicators,
            )

        if price < ema200 and z > self.z_entry and price < prev_price:
            stop_px = price + self.stop_atr_mult * atr
            tp_px = max(price - self.tp_atr_mult * atr, price * 0.1)
            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=now,
                direction=SignalDirection.SHORT,
                status=SignalStatus.ENTRY,
                reference_price=price,
                atr=float(atr),
                suggested_stop=float(stop_px),
                suggested_take_profit=float(tp_px),
                signal_strength=0.80,
                estimated_fee_pct=self.fee_pct,
                estimated_slippage_pct=self.slippage_pct,
                indicators=indicators,
            )

        return create_no_trade_signal(
            self.STRATEGY_ID,
            symbol,
            price,
            f"No Bear Chandelier setup (Z={z:+.2f}, below_ema200={price < ema200})",
            indicators=indicators,
        )
