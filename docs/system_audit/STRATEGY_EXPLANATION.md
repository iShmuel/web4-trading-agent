# Web4 Quantitative Trading Desk — Deep Strategy Explanation

**Document Identifier:** `DOC-STRAT-DEEP-2026`  
**System Layer:** Quantitative Trading Engines & Alpha Attribution  
**Engines Analyzed:** Momentum, Mean Reversion, StatArb Pairs, Basis Yield  

---

## 1. Momentum / Trend Following Engine

**Implementation:** [`strategies/momentum.py`](../../strategies/momentum.py) (`MomentumStrategy`)

### 1.1 Mathematical & Qualitative Mechanics
The Momentum strategy is designed to capture sustained, multi-day directional trend expansions. Markets exhibit momentum due to institutional order flow accumulation, macro trend continuation, and delayed information diffusion.

1. **Breakout High/Low Channel (Donchian):**
   - The strategy tracks the highest high and lowest low over a rolling $N$-period lookback ($N = 24$ to $48$ hours, defined in symbol asset profiles).
   - Long breakout condition:
     $$\text{Price}_t > \max(\text{High}_{t-N}, \dots, \text{High}_{t-1})$$
2. **Trend Filter (50 & 200 EMA):**
   - Breakouts are only considered valid if aligned with the intermediate trend:
     $$\text{Price}_t > \text{EMA}_{50}(\text{Close})_t$$
   - In crypto and equities, short breakouts additionally require $\text{Price}_t < \text{EMA}_{200}$ (`short_requires_macro_bear`).
3. **Regime Filter:**
   - Evaluated strictly when market-wide $\text{ADX} \ge 28.0$.
4. **Dynamic Trailing Ratchet (Chandelier Exit):**
   - Stop-loss is initially set at $\text{Entry} - \text{ATR} \times M$ ($M = 2.0$ to $2.5$).
   - As price moves in favor of the trade, the trailing stop ratchets higher:
     $$\text{Stop}_t = \max(\text{Stop}_{t-1}, \text{Highest Price} - \text{ATR}_t \times M)$$
   - The stop can never move backward.
5. **Partial Scale-Out Profit Target:**
   - When unrealized profit reaches $2.5\times\text{ATR}$, 50% of the position is automatically closed (`PARTIAL_EXIT`), banking cash into the account while trailing the remaining 50% for open-ended upside.

### 1.2 Strengths & Weaknesses
- **Strengths:** Captures the biggest market winners (e.g., BTC and NVDA rallies), achieves high profit factors (1.61–1.75), and cuts losers quickly via volatility stops.
- **Weaknesses:** Whipsaws in range-bound markets. Without the ADX $\ge 28.0$ gate, false breakouts erode capital through repeated small stop-outs.

---

## 2. Mean Reversion Engine

**Implementation:** [`strategies/mean_reversion.py`](../../strategies/mean_reversion.py) (`MeanReversionStrategy`)

### 2.1 Mathematical & Qualitative Mechanics
The Mean Reversion strategy is engineered to profit from liquidity exhaustions and temporary statistical over-extensions back toward the fair-value rolling mean.

1. **Calibrated Bollinger Bands:**
   - Computes rolling 20-period simple moving average $\mu_{20}$ and standard deviation $\sigma_{20}$.
   - Bands are set at $1.9\sigma$:
     $$\text{Upper Band} = \mu_{20} + 1.9 \times \sigma_{20}$$
     $$\text{Lower Band} = \mu_{20} - 1.9 \times \sigma_{20}$$
2. **Momentum Exhaustion Filter (RSI 14):**
   - Oversold long entry condition:
     $$\text{Close}_t < \text{Lower Band}_t \quad \text{AND} \quad \text{RSI}_{14} \le 30.0$$
   - Overbought short entry condition:
     $$\text{Close}_t > \text{Upper Band}_t \quad \text{AND} \quad \text{RSI}_{14} \ge 70.0$$
3. **Regime Restriction:**
   - Active strictly when $\text{ADX} < 24.0$ (`RANGE_CHOP`).
   - If an asset breaks below the lower band while ADX $\ge 28.0$, this indicates a structural breakdown rather than a temporary oversold dip; the Coordinator strictly suppresses the trade.
4. **Target & Exit:**
   - Primary take-profit target is the 20-period moving average ($\mu_{20}$).
   - Time stop: Closes trade if reversion does not materialize within 12 bars.

### 2.2 Strengths & Weaknesses
- **Strengths:** Highest win rate of all directional engines (**69.9%** win rate in out-of-sample testing), dampens portfolio volatility, and generates positive cash flow when trend strategies are idle.
- **Weaknesses:** Highly vulnerable to trending crashes ("catching falling knives") if regime filtering fails. Requires strict stop-losses.

---

## 3. Statistical Arbitrage Pairs Engine

**Implementation:** [`strategies/stat_arb_pairs.py`](../../strategies/stat_arb_pairs.py) (`StatArbPairsStrategy`)

### 3.1 Mathematical & Qualitative Mechanics
StatArb Pairs trades the synthetic spread between two economically cointegrated assets:
$$\text{Spread}_t = \ln(\text{Asset A}_t) - \beta \times \ln(\text{Asset B}_t) - \alpha$$
Where $\beta$ is the rolling 48-hour Ordinary Least Squares (OLS) hedge ratio.

1. **Spread Normalization (Z-Score):**
   $$Z_t = \frac{\text{Spread}_t - \mu_{\text{spread}}}{\sigma_{\text{spread}}}$$
2. **Dual-Leg Entry Criteria:**
   - When $Z_t \ge +2.5$: Spread is statistically overextended. Opens **Short A + Long B** (`SHORT_A_LONG_B`).
   - When $Z_t \le -2.5$: Spread is statistically compressed. Opens **Long A + Short B** (`LONG_A_SHORT_B`).
3. **Exit Condition:**
   - Closes when $|Z_t| \le 0.5$ (mean reversion completed) or $|Z_t| \ge 3.5$ (stop-loss for structural cointegration breakdown).
4. **The 2.5x Institutional Edge-to-Cost Gate:**
   - Pairs trading requires crossing the spread on four fills (opening Long, opening Short, closing Long, closing Short).
   - Total dual-leg roundtrip friction:
     $$\text{Cost}_{\text{dual-leg}} = 4 \times (\text{Taker Fee} + \text{Slippage}) = 4 \times 0.0010 = \mathbf{0.0040 \text{ (40 bps)}}$$
   - The gate verifies:
     $$\text{Expected Edge} = (|Z_{\text{entry}}| - 0.5) \times \sigma_{\text{spread}} \ge 2.5 \times \text{Cost}_{\text{dual-leg}}$$
   - Any pair trade that fails this condition is dropped before execution.

### 3.2 Strengths & Weaknesses
- **Strengths:** Market-neutral. Operates independently of whether the broad stock or crypto market is rising or crashing. Adds +$261.21 in portfolio alpha and reduces drawdown from 9.10% to 8.88%.
- **Weaknesses:** High transaction fee drag. Without the 2.5x hurdle gate, dual-leg friction destroyed capital (-$328 loss in un-gated baseline).

---

## 4. Basis Yield Carry Engine

**Implementation:** [`core/basis_yield.py`](../../core/basis_yield.py) (`BasisYieldEngine`)

### 4.1 Mechanics & Yield Source
In cryptocurrency markets, perpetual contracts trade with an 8-hour funding rate mechanism that tethers the perpetual price to spot. When speculators are aggressively bullish, perpetuals trade at a premium, forcing perpetual longs to pay cash to perpetual shorts every 8 hours.

1. **Delta-Neutral Position:**
   - Buy Spot Asset ($+\$1,000 \text{ Spot BTC}$).
   - Sell Short Perpetual Contract ($-\$1,000 \text{ Perp BTC}$).
   - **Net Market Beta:** $\beta = 0.0$. Price fluctuations in BTC produce zero directional PnL.
2. **Accrual Calculation:**
   $$\text{Yield Accrued} = \text{Notional Value} \times \text{Funding Rate}$$
   If 8-hour funding rate is $+0.03\%$, daily return is $+0.09\%$ ($32.8\%$ APR).
3. **Hurdle Rate Gating:**
   - Accruals are only executed if annualized funding yield exceeds the hurdle rate (`basis_hurdle_apr = 8.0%`).
4. **Simulated PnL Accounting:**
   - In `PaperAccount`, basis yield accruals are credited as non-trading cash income with zero trading fee overhead.

### 4.2 Strengths & Weaknesses
- **Strengths:** 100% win rate (263 positive accruals, 0 losses in OOS backtest), monotonic equity lift (+0.96% quarterly), and completely uncorrelated with directional drawdowns.
- **Weaknesses:** Capital utilization cap (maximum 30%–45% of portfolio). In real trading, carries platform counterparty risk (exchange solvency) and liquidation risk during flash crashes if margin is improperly managed.
