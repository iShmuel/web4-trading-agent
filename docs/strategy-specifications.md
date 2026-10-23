# Strategy Specifications

## 1. Strategy A: Momentum / Trend Following (`strategies/momentum.py`)

### Thesis
Asset prices exhibiting strong directional momentum tend to persist over multi-session horizons. Capturing breakouts with dynamic ATR trailing stops yields positive asymmetry (cutting losers quickly, letting winners ride).

### Entry Rules
- **Long Entry**:
  - Close price $> 200\text{ EMA}$ (or configured macro trend filter).
  - Close price $> \text{Breakout High}$ (highest high of previous $N$ bars).
  - Estimated reward after fees and slippage $> 0$.
- **Short Entry**:
  - Close price $< 200\text{ EMA}$.
  - Close price $< \text{Breakdown Low}$ (lowest low of previous $N$ bars).
  - Short selling permitted in config.

### Exit Rules
- **Trailing Stop Loss**: Dynamic ATR band:
  - Long: $\text{Stop} = \max(\text{Previous Stop}, \text{Current Price} - \text{ATR} \times \text{Multiplier})$.
  - Short: $\text{Stop} = \min(\text{Previous Stop}, \text{Current Price} + \text{ATR} \times \text{Multiplier})$.
- **Tiered Take-Profit**: Optional partial scale-out (e.g. 50% at $2\times \text{ATR}$), moving remaining stop to breakeven.

---

## 2. Strategy B: Mean Reversion (`strategies/mean_reversion.py`)

### Thesis
In range-bound, non-trending regimes (low ADX), extreme deviations from a moving average represent transient statistical imbalances that revert to the mean.

### Entry Rules
- **Regime Filter**: $\text{ADX} < 25$ (market must be consolidating/range-bound).
- **Long Entry (Oversold Bounce)**:
  - Price $\le \text{Lower Bollinger Band}$ ($2\sigma$ below 20 SMA).
  - $\text{RSI} < 30$.
  - Target profit ($\text{SMA} - \text{Price}$) exceeds round-trip fee friction.
- **Short Entry (Overbought Reversal)**:
  - Price $\ge \text{Upper Bollinger Band}$.
  - $\text{RSI} > 70$.

### Exit Rules
- **Mean Target Hit**: Price touches the 20-period SMA reference mean.
- **Stop-Loss Hit**: Price breaches $2\times \text{ATR}$ stop distance.
- **Max Holding Period**: Exceeds maximum bar hold threshold (default: 48 bars).
