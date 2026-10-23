# Risk Management & Portfolio Controls

## 1. Dynamic Volatility Sizing (Van Tharp R-Risk)
Every proposed trade is sized dynamically based on market volatility:
$$\text{Dollar Risk} = \text{Total Equity} \times \text{Risk \%}$$
$$\text{Stop Distance} = \text{ATR} \times \text{Multiplier}$$
$$\text{Position Units} = \frac{\text{Dollar Risk}}{\text{Stop Distance}}$$

- **Strict Risk Cap**: Risk percentage per trade is hard-bounded between **1.0% and 5.0%**. The self-improvement optimizer cannot breach these bounds.

## 2. Portfolio-Level Exposure Caps
- **Total Portfolio Exposure**: Capped at **80% of equity**.
- **Single Asset Exposure**: Capped at **35% of equity**. If volatility sizing requests more capital, the order size is automatically scaled down to fit the cap.
- **Per-Strategy Allocation**: Capped at **60% of equity** per strategy engine.
- **Max Open Positions**: Default 3 concurrent positions across the entire portfolio.

## 3. Circuit Breakers & Kill Switches
1. **Daily Loss Breaker**:
   - Monitored continuously against the day's opening equity baseline.
   - If cumulative daily loss breaches **5.0%**, all new entries are halted immediately.
2. **Maximum Drawdown Breaker**:
   - Monitored against the portfolio's historical peak equity (high-water mark).
   - If peak-to-trough drawdown breaches **15.0%**, the emergency kill switch triggers.
3. **Data Quality & Staleness Breaker**:
   - If candle data is older than 120 minutes or contains price anomalies, evaluations for that asset are rejected.
