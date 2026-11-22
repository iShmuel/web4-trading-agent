# Web4 Quantitative Trading Desk — Quantitative Formulas & Mathematics

**Document Identifier:** `DOC-MATH-FORMULAS-2026`  
**Scope:** Mathematical Formulations, Numerical Examples, and Code Implementations  

---

## 1. Technical Indicators & Market Microstructure

### 1.1 Average True Range (ATR)
- **Source:** [`core/strategy.py`](../../core/strategy.py)
- **Mathematical Definition:**
  For each bar $t$, True Range (TR) is the maximum of:
  $$\text{TR}_t = \max\Big(\text{High}_t - \text{Low}_t, \ |\text{High}_t - \text{Close}_{t-1}|, \ |\text{Low}_t - \text{Close}_{t-1}|\Big)$$
  ATR is Wilder's smoothed moving average over period $N = 14$:
  $$\text{ATR}_t = \frac{\text{ATR}_{t-1} \times (N - 1) + \text{TR}_t}{N}$$
- **Numerical Example:**
  If $\text{ATR}_{t-1} = 2.00$, $\text{High}_t = 105.0$, $\text{Low}_t = 102.0$, $\text{Close}_{t-1} = 101.0$:
  $\text{TR}_t = \max(105 - 102, |105 - 101|, |102 - 101|) = \max(3, 4, 1) = 4.0$.
  $$\text{ATR}_t = \frac{2.00 \times 13 + 4.0}{14} = \frac{30.0}{14} = \mathbf{2.143}$$

---

### 1.2 Average Directional Index (ADX)
- **Source:** [`core/strategy.py`](../../core/strategy.py)
- **Mathematical Definition:**
  Measures trend strength regardless of trend direction.
  1. Directional Movement:
     $$+\text{DM}_t = \max\Big(\text{High}_t - \text{High}_{t-1}, \ 0\Big) \quad \text{if } \text{High}_t - \text{High}_{t-1} > \text{Low}_{t-1} - \text{Low}_t \text{ else } 0$$
     $$-\text{DM}_t = \max\Big(\text{Low}_{t-1} - \text{Low}_t, \ 0\Big) \quad \text{if } \text{Low}_{t-1} - \text{Low}_t > \text{High}_t - \text{High}_{t-1} \text{ else } 0$$
  2. Directional Indicators:
     $$+\text{DI}_{14} = 100 \times \frac{\text{Smooth}(+\text{DM})}{\text{ATR}_{14}}, \quad -\text{DI}_{14} = 100 \times \frac{\text{Smooth}(-\text{DM})}{\text{ATR}_{14}}$$
  3. Directional Index (DX) and ADX:
     $$\text{DX} = 100 \times \frac{|+\text{DI} - -\text{DI}|}{+\text{DI} + -\text{DI}}, \quad \text{ADX} = \text{WilderSmooth}_{14}(\text{DX})$$
- **Trading Interpretation:**
  - $\text{ADX} < 24.0$: Range-bound market (Chop). Activates Mean Reversion.
  - $24.0 \le \text{ADX} < 28.0$: Transition phase.
  - $\text{ADX} \ge 28.0$: Strong directional trend. Activates Momentum breakouts; suppresses counter-trend Mean Reversion.

---

### 1.3 Bollinger Bands
- **Source:** [`strategies/mean_reversion.py`](../../strategies/mean_reversion.py)
- **Mathematical Definition:**
  $$\mu_t = \frac{1}{20} \sum_{i=0}^{19} \text{Close}_{t-i}, \quad \sigma_t = \sqrt{\frac{1}{20} \sum_{i=0}^{19} (\text{Close}_{t-i} - \mu_t)^2}$$
  $$\text{Upper Band}_t = \mu_t + 1.9 \times \sigma_t, \quad \text{Lower Band}_t = \mu_t - 1.9 \times \sigma_t$$

---

### 1.4 Cointegrated Spread & Z-Score
- **Source:** [`strategies/stat_arb_pairs.py`](../../strategies/stat_arb_pairs.py)
- **Mathematical Definition:**
  Hedge ratio $\beta$ estimated via rolling 48-period OLS regression:
  $$\ln(P_A) = \alpha + \beta \ln(P_B) + \epsilon$$
  $$\text{Spread}_t = \ln(P_{A,t}) - \beta \ln(P_{B,t}) - \alpha$$
  $$Z_t = \frac{\text{Spread}_t - \mu_{\text{spread}, 48}}{\sigma_{\text{spread}, 48}}$$
  - Entry Trigger: $|Z_t| \ge 2.50$.
  - Exit Trigger: $|Z_t| \le 0.50$ (mean-reverted) or $|Z_t| \ge 3.50$ (stop-loss).

---

## 2. Risk Management & Position Sizing

### 2.1 Van Tharp Volatility-Adjusted Sizing
- **Source:** [`core/risk_engine.py`](../../core/risk_engine.py) (`calculate_position`)
- **Formula:**
  $$\text{Stop Distance } D_{\text{stop}} = |\text{Entry Price} - \text{Stop Loss Price}|$$
  $$\text{Target Dollar Risk } R_{\$} = \text{Account Equity} \times \text{Risk \%}$$
  $$\text{Calculated Units } U_{\text{raw}} = \frac{R_{\$}}{D_{\text{stop}}}$$
  Where $\text{Risk \%}$ is clamped:
  $$\text{Risk \%} = \max\Big(0.01, \ \min(0.05, \ \text{Configured Risk})\Big)$$
- **Numerical Example:**
  - Account Equity: **$10,000.00**
  - Configured Risk: **2.0%** ($R_{\$} = \$200.00$)
  - Entry Price: **$150.00**
  - ATR ($14$): **$3.00**, Stop Multiplier: **2.0** ($D_{\text{stop}} = 2.0 \times \$3.00 = \$6.00$)
  - $\text{Stop Loss Price} = \$150.00 - \$6.00 = \$144.00$
  $$\text{Units} = \frac{\$200.00}{\$6.00} = \mathbf{33.333 \text{ units}}$$
  $$\text{Notional Exposure} = 33.333 \times \$150.00 = \mathbf{\$5,000.00}$$

---

### 2.2 Exposure Cap Gating
- **Source:** [`core/risk_engine.py`](../../core/risk_engine.py) (`validate_proposed_entry`)
- **Single-Asset Cap:**
  $$\text{Max Notional Allowed} = \text{Account Equity} \times 0.35$$
  In the example above, $\$5,000.00$ exceeds $35\%$ of $\$10,000$ ($\$3,500.00$).
  $$\text{Capped Units} = \frac{\$3,500.00}{\$150.00} = \mathbf{23.333 \text{ units}}$$
  $$\text{Final Realized Dollar Risk} = 23.333 \times \$6.00 = \mathbf{\$140.00 \text{ (1.4\% of equity)}}$$

---

### 2.3 Circuit Breakers
- **Daily Loss Circuit Breaker (5%):**
  $$\text{Daily PnL \%} = \frac{\text{Current Equity} - \text{Start of UTC Day Equity}}{\text{Start of UTC Day Equity}}$$
  If $\text{Daily PnL \%} \le -0.05$, kill-switch triggers: rejects all new trade entries.
- **Persistent Drawdown Circuit Breaker (15%):**
  $$\text{Drawdown \%} = \frac{\text{Current Equity} - \text{Peak Equity}_{\text{historical}}}{\text{Peak Equity}_{\text{historical}}}$$
  If $\text{Drawdown \%} \le -0.15$, system permanently halts until explicit human intervention.

---

## 3. Transaction Costs & PnL Accounting

### 3.1 Cost-Adjusted Fill Simulation
- **Source:** [`core/paper_account.py`](../../core/paper_account.py)
- **Taker Fee Rate:** $f = 0.0006$ ($6$ bps).
- **Slippage Rate:** $s = 0.0004$ ($4$ bps).
- **Long Execution:**
  $$\text{Fill Price}_{\text{entry}} = P_{\text{bar}} \times (1 + s)$$
  $$\text{Fee}_{\text{entry}} = U \times \text{Fill Price}_{\text{entry}} \times f$$
- **Net Realized PnL:**
  $$\text{Gross PnL} = (\text{Fill Price}_{\text{exit}} - \text{Fill Price}_{\text{entry}}) \times U$$
  $$\text{Net PnL} = \text{Gross PnL} - (\text{Fee}_{\text{entry}} + \text{Fee}_{\text{exit}})$$

---

## 4. Institutional Statistical Performance Metrics

### 4.1 Sharpe Ratio
Annualized Sharpe ratio on hourly equity returns:
$$\text{Hourly Mean Return } \mu_h = \frac{1}{T} \sum r_h, \quad \text{Hourly Volatility } \sigma_h = \sqrt{\frac{1}{T-1} \sum (r_h - \mu_h)^2}$$
$$\text{Annualized Sharpe } S = \frac{\mu_h}{\sigma_h} \times \sqrt{252 \times 6.5 \text{ (or } 365 \times 24 \text{ for crypto)}}$$

---

### 4.2 Sortino Ratio
Measures return relative only to *downside* volatility:
$$\text{Downside Deviation } \sigma_d = \sqrt{\frac{1}{T} \sum \min(r_h, 0)^2}$$
$$\text{Sortino} = \frac{\mu_h}{\sigma_d} \times \sqrt{N_{\text{annual}}}$$

---

### 4.3 Calmar Ratio
$$\text{Calmar Ratio} = \frac{\text{Annualized Net Return \%}}{\text{Maximum Drawdown \%}}$$

---

### 4.4 Probabilistic Sharpe Ratio (PSR) & Deflated Sharpe Ratio (DSR)
- **Source:** [`core/institutional_stress_tester.py`](../../core/institutional_stress_tester.py)
- **Formula (Bailey & López de Prado, 2014):**
  $$\text{PSR}(S_0) = Z\left(\frac{(S - S_0) \sqrt{T - 1}}{\sqrt{1 - \gamma_3 S + \frac{\gamma_4 - 1}{4} S^2}}\right)$$
  Where:
  - $S$ is estimated sample Sharpe ratio.
  - $S_0$ is benchmark Sharpe ratio ($0.0$).
  - $\gamma_3$ is return skewness.
  - $\gamma_4$ is return kurtosis.
  - $Z(\cdot)$ is standard normal cumulative distribution function.
  - **DSR** adjusts $S_0$ upward to account for data mining across $K$ trials:
    $$S_0 = \sqrt{2 \ln K} \times \left(1 - \frac{\gamma}{\sqrt{2 \ln K}}\right)$$
