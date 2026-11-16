# Multi-Strategy Alpha Expansion Roadmap (12 Candidate Strategies)

> **Status:** Planned / Research Queue (Zero strategies activated without walk-forward OOS + `PF >= 1.15` + CPI benchmark validation).
>
> All 12 strategies below use **existing data feeds** (1h OHLCV, volume, crypto perpetual funding rates, and FRED macro data) with **zero new paid APIs**, and plug into the **Web4 Dynamic Rolling Router** (`core/coordinator.py`) under `RiskEngine` `1%–5%` bounds (`core/risk_engine.py`).

---

## Current 4 Active Strategies (Baseline)
1. **Momentum (`strategies/momentum.py`):** 36-bar Donchian breakouts filtered by `EMA 200`.
2. **Mean Reversion (`strategies/mean_reversion.py`):** Bollinger Band + RSI + ADX ranging pullbacks.
3. **Stat-Arb Pairs (`strategies/stat_arb_pairs.py`):** Cointegrated Z-score spread divergence.
4. **Basis Yield (`core/basis_yield.py`):** Delta-neutral crypto perpetual funding carry (`>= 5.0%` APR hurdle).

---

## 12 Proposed New Strategies (Plain-English Guide)

### 1. Volatility Squeeze Breakout (`strategies/volatility_squeeze.py`) — *Priority #1*
* **Simple Explanation:** When an asset gets abnormally quiet (Bollinger Bands squeeze inside Keltner Channels like a coiled spring) and then breaks out on strong volume, it often starts a fast new trend.
* **Why It Helps:** Catches the exact transition from calm compression to expansion earlier than 36-bar Momentum.

### 2. Trend Pullback ("Buy the Dip in an Uptrend") (`strategies/trend_pullback.py`) — *Priority #2*
* **Simple Explanation:** Waits for a confirmed uptrend (`price > EMA 200`), waits for a healthy short-term dip (`RSI 38–45` near `EMA 20/50`), and buys the bounce at a better price with a tighter stop.
* **Why It Helps:** Bridges the gap between Momentum (chases highs) and Mean Reversion (sideways-only).

### 3. VWAP Fair-Value Reversion (`strategies/vwap_reversion.py`)
* **Simple Explanation:** Anchors fair value to **Rolling VWAP** (Volume-Weighted Average Price) and fades extreme stretches on fading volume back toward institutional fair value.

### 4. Funding-Rate Contrarian Squeeze (`strategies/funding_contrarian.py`)
* **Simple Explanation:** When crypto perpetual funding turns deeply negative (`SHORT_SQUEEZE_PRONE`) and price turns up, crowded shorts are forced to cover, fueling sharp squeeze rallies.
* **Why It Helps:** Turns existing live `funding_rate_pct` telemetry into an active alpha signal.

### 5. Cross-Sectional Relative Strength Rotation (`strategies/relative_strength.py`)
* **Simple Explanation:** Ranks all watchlist assets using `calculate_relative_strength` (`core/strategy.py`) and concentrates limited position slots (`max_concurrent_positions: 5`) into the strongest leaders when the broad market is healthy.

### 6. US Session Open Drive (`strategies/session_open.py`)
* **Simple Explanation:** After the 15-minute NYSE opening-bell buffer, trades breakouts from the early US equity opening range backed by volume.

### 7. Macro Risk-Off Defensive Hedge (`strategies/macro_hedge.py`)
* **Simple Explanation:** When `FredMacroRadar` / broad equities signal `RISK_OFF`, shifts defensive allocation toward macro hedges (`XLE`, `KDP`, etc.) instead of sitting 100% idle in cash.

### 8. BTC-Lead / Altcoin Lag Spillover (`strategies/btc_lead_lag.py`)
* **Simple Explanation:** Trades catch-up moves in correlated altcoins (`SOL/USDT`, `AVAX/USDT`, `DOGE/USDT`, `SUI/USDT`, `ENA/USDT`) 1–3 hours after `BTC/USDT` makes a high-volume breakout.

### 9. RSI Bull/Bear Divergence (`strategies/rsi_divergence.py`)
* **Simple Explanation:** Enters early reversals when price makes a lower low while `RSI` makes a higher low (selling exhaustion), or fades weaker highs at tops.

### 10. Weekend Crypto / Opening Gap Fade (`strategies/weekend_gap_fade.py`)
* **Simple Explanation:** Fades thin weekend crypto spikes/dumps as Monday institutional liquidity returns, and fades exaggerated US equity opening gaps that have no SEC 8-K news catalyst.

### 11. Dual-Timeframe Confluence Trend (`strategies/mtf_confluence_trend.py`)
* **Simple Explanation:** Reuses `calculate_multi_timeframe_confluence` (`core/strategy.py`) so 1h entries only fire when the 4h/Daily trend agrees.

### 12. Volatility-Targeted Trend (`strategies/vol_targeted_trend.py`)
* **Simple Explanation:** Scales trend aggressiveness inversely with realized volatility (staying strictly inside `RiskEngine`'s `1%–5%` bounds) to protect Sharpe during sudden volatility spikes.

---

## Promotion Gate (Required Before Live Activation)
1. Walk-forward 70/30 In-Sample / Out-of-Sample validation with realistic maker/taker fees and slippage.
2. Must clear `PF >= 1.15` in Dynamic Rolling Router and improve or preserve portfolio CPI (`core/cpi_benchmark.py`).
3. Full `pytest` regression pass (`184+` tests) and Governor approval (`agent/governor.py`).
