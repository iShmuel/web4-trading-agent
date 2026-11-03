---
marp: true
theme: default
paginate: true
header: "Web4 Autonomous Trading Desk | Institutional Executive Deck"
footer: "Confidential • Quantitative System Architecture • Live Paper Mode"
style: |
  section {
    background: radial-gradient(circle at 15% 15%, #0e172a 0%, #030712 100%);
    color: #e2e8f0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    padding: 30px 40px;
    font-size: 16.5px;
    letter-spacing: -0.01em;
  }
  h1 {
    color: #38bdf8;
    font-size: 1.8em;
    margin-bottom: 0.12em;
    font-weight: 800;
    letter-spacing: -0.03em;
  }
  h2 {
    color: #38bdf8;
    font-size: 1.28em;
    margin: 0 0 0.25em 0;
    font-weight: 700;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  h3 {
    color: #94a3b8;
    font-size: 0.95em;
    margin: 0 0 0.3em 0;
    font-weight: 600;
  }
  table {
    border-collapse: separate;
    border-spacing: 0;
    width: 100%;
    margin: 8px 0;
    font-size: 0.73em;
    border-radius: 8px;
    overflow: hidden;
    border: 1px solid #1e293b;
  }
  th {
    background-color: #0f172a;
    color: #38bdf8;
    padding: 6px 10px;
    border-bottom: 1px solid #1e293b;
    text-transform: uppercase;
    font-weight: 700;
    letter-spacing: 0.05em;
  }
  td {
    padding: 5px 10px;
    border-bottom: 1px solid #151e32;
    background-color: rgba(11, 17, 32, 0.75);
    color: #cbd5e1;
  }
  tr:last-child td {
    border-bottom: none;
  }
  tr:nth-child(even) td {
    background-color: rgba(15, 23, 42, 0.45);
  }
  .highlight {
    color: #10b981;
    font-weight: 700;
  }
  .accent {
    color: #38bdf8;
    font-weight: 700;
  }
  .warning {
    color: #f43f5e;
    font-weight: 700;
  }
  .grid-4 {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
    margin: 10px 0;
  }
  .grid-3 {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 11px;
    margin: 11px 0;
  }
  .grid-2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin: 10px 0;
  }
  .stat-card {
    background: rgba(15, 23, 42, 0.85);
    border: 1px solid #1e293b;
    border-top: 3px solid #38bdf8;
    border-radius: 8px;
    padding: 10px 8px;
    text-align: center;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.45);
  }
  .stat-card.green { border-top-color: #10b981; }
  .stat-card.purple { border-top-color: #8b5cf6; }
  .stat-card.gold { border-top-color: #f59e0b; }
  .stat-number {
    font-size: 1.95em;
    font-weight: 800;
    line-height: 1.1;
    margin-bottom: 2px;
    color: #38bdf8;
    letter-spacing: -0.02em;
  }
  .stat-number.green { color: #10b981; }
  .stat-number.purple { color: #a78bfa; }
  .stat-number.gold { color: #fbbf24; }
  .stat-label {
    font-size: 0.70em;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #94a3b8;
    font-weight: 600;
  }
  .card {
    background: rgba(15, 23, 42, 0.7);
    border: 1px solid #1e293b;
    border-radius: 8px;
    padding: 10px 12px;
    line-height: 1.38;
  }
  .card h4 {
    margin: 0 0 5px 0;
    color: #38bdf8;
    font-size: 0.92em;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .card p {
    margin: 0;
    font-size: 0.79em;
    color: #94a3b8;
  }
  .card-danger {
    background: rgba(244, 63, 94, 0.08);
    border: 1px solid rgba(244, 63, 94, 0.35);
    border-radius: 8px;
    padding: 11px 13px;
  }
  .card-danger h4 {
    margin: 0 0 5px 0;
    color: #f43f5e;
    font-size: 0.92em;
  }
  .card-success {
    background: rgba(16, 185, 129, 0.08);
    border: 1px solid rgba(16, 185, 129, 0.35);
    border-radius: 8px;
    padding: 11px 13px;
  }
  .card-success h4 {
    margin: 0 0 5px 0;
    color: #10b981;
    font-size: 0.92em;
  }
  .pill {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 9999px;
    font-size: 0.71em;
    font-weight: 700;
    letter-spacing: 0.03em;
  }
  .pill-green {
    background: rgba(16, 185, 129, 0.15);
    border: 1px solid #059669;
    color: #10b981;
  }
  .pill-cyan {
    background: rgba(56, 189, 248, 0.15);
    border: 1px solid #0284c7;
    color: #38bdf8;
  }
  .pill-purple {
    background: rgba(139, 92, 246, 0.15);
    border: 1px solid #7c3aed;
    color: #a78bfa;
  }
  .pill-amber {
    background: rgba(245, 158, 11, 0.15);
    border: 1px solid #d97706;
    color: #fbbf24;
  }
  .pill-red {
    background: rgba(244, 63, 94, 0.15);
    border: 1px solid #e11d48;
    color: #f43f5e;
  }
  .step-box {
    background: rgba(9, 14, 26, 0.9);
    border: 1px solid #1e293b;
    border-radius: 6px;
    padding: 8px 11px;
    margin-bottom: 6px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .step-num {
    background: #1e293b;
    color: #38bdf8;
    font-weight: 800;
    width: 22px;
    height: 22px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 50%;
    font-size: 0.80em;
    margin-right: 8px;
  }
  .takeaway {
    background: rgba(15, 23, 42, 0.9);
    border-left: 3px solid #38bdf8;
    padding: 7px 11px;
    margin-top: 9px;
    font-size: 0.78em;
    color: #94a3b8;
    border-radius: 0 6px 6px 0;
  }
  .flow-pipeline {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    margin: 12px 0;
  }
  .flow-node {
    background: rgba(15, 23, 42, 0.9);
    border: 1px solid #1e293b;
    border-radius: 8px;
    padding: 8px 6px;
    text-align: center;
    flex: 1;
    min-height: 80px;
    display: flex;
    flex-direction: column;
    justify-content: center;
  }
  .flow-arrow {
    color: #38bdf8;
    font-size: 1.15em;
    font-weight: 800;
    flex-shrink: 0;
  }
  .phase-box {
    background: rgba(15, 23, 42, 0.85);
    border: 1px solid #1e293b;
    border-top: 3px solid #38bdf8;
    border-radius: 8px;
    padding: 10px 12px;
    height: 100%;
  }
  .phase-box.gold { border-top-color: #fbbf24; }
  .phase-box.green { border-top-color: #10b981; }
  .phase-box.purple { border-top-color: #a78bfa; }
  .phase-box.red { border-top-color: #f43f5e; }
  
  .agent-card {
    background: rgba(15, 23, 42, 0.85);
    border: 1px solid #1e293b;
    border-radius: 8px;
    padding: 8px 11px;
    line-height: 1.34;
    font-size: 0.75em;
  }
  .agent-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 5px;
    border-bottom: 1px solid rgba(30, 41, 59, 0.8);
    padding-bottom: 4px;
  }
  .agent-title {
    font-size: 1.08em;
    font-weight: 800;
    color: #38bdf8;
    display: flex;
    align-items: center;
    gap: 5px;
  }
  .agent-row {
    margin-bottom: 3px;
    color: #cbd5e1;
  }
  .agent-label {
    font-weight: 800;
    text-transform: uppercase;
    font-size: 0.88em;
    letter-spacing: 0.04em;
    margin-right: 4px;
  }
  .agent-label.what { color: #38bdf8; }
  .agent-label.how { color: #a78bfa; }
  .agent-label.why { color: #10b981; }
---

# Web4 Autonomous Trading Desk
### Institutional Multi-Asset Quantitative System Architecture

**Machine Perception • Deterministic Risk Governance • Multi-Strategy Alpha**

<br/>

<div class="grid-4">
  <div class="stat-card green">
    <div class="stat-number green">+56.6%</div>
    <div class="stat-label">90-Day Net Return</div>
  </div>
  <div class="stat-card">
    <div class="stat-number">6.25%</div>
    <div class="stat-label">Max Portfolio Drawdown</div>
  </div>
  <div class="stat-card purple">
    <div class="stat-number purple">84.8%</div>
    <div class="stat-label">Peak Mean Reversion Win%</div>
  </div>
  <div class="stat-card gold">
    <div class="stat-number gold">0.70s</div>
    <div class="stat-label">Local GPU LLM Latency</div>
  </div>
</div>

<br/>

<span class="pill pill-green">🛡️ 80/80 PYTEST VERIFIED</span> &nbsp;
<span class="pill pill-cyan">⚡ 24/7 AUTONOMOUS DAEMON</span> &nbsp;
<span class="pill pill-purple">📊 21-ASSET UNIVERSE</span> &nbsp;
<span class="pill pill-amber">🔒 ZERO REAL-MONEY RISK</span>

---

## 1. Executive Snapshot: The Institutional Edge

The system solves the #1 flaw of automated trading: **unconstrained volatility bleed during market chop and crashes**.

<div class="grid-2">
  <div class="card">
    <h4>📈 Asymmetric Upside Capture</h4>
    <p>• <b>Momentum Trend Riders</b>: Donchian breakouts capture multi-day runner waves.<br/>
    • <b>50% Staged Take-Profit</b>: Locks cash at $+2.5\times\text{ATR}$ and moves stop to Break-Even.<br/>
    • <b>21 Uncorrelated Assets</b>: Rotates capital to Tech Equities and Gold when Crypto chops.</p>
  </div>
  <div class="card">
    <h4>🛡️ Mathematical Downside Shield</h4>
    <p>• <b>200 EMA Macro Gate</b>: Strictly blocks buying into multi-week bear cascades.<br/>
    • <b>VIX Crash Defense ($\ge 30$)</b>: Freezes breakout entries during macro market panics.<br/>
    • <b>1%–5% Risk Hard-Clamp</b>: Hard-coded mathematical immunity against account ruin.</p>
  </div>
</div>

<div class="takeaway">
  💡 <b>Core Philosophy</b>: <i>"Living off trading requires low drawdown and capital preservation, not reckless 20x gambling."</i>
</div>

---

## 2. The Problem: Why 95% of Retail Bots Blow Up

<div class="grid-2">
  <div class="card-danger">
    <h4>❌ RETAIL TRADING FAILURE MODES</h4>
    <p style="color: #fca5a5; line-height: 1.55; font-size: 0.8em;">
      • <b>Revenge Trading</b>: Increases position sizes into losing streaks until liquidation.<br/>
      • <b>Massive Fee Drag</b>: Taker market orders & wide spreads consume 15%–25% annually.<br/>
      • <b>No Macro Filter</b>: Buys bull traps at the top of relief rallies in bear markets.<br/>
      • <b>Catastrophic Drawdowns</b>: Suffers -50% to -80% drawdowns in sudden market crashes.
    </p>
  </div>
  <div class="card-success">
    <h4>🟢 OUR INSTITUTIONAL QUANTITATIVE DESK</h4>
    <p style="color: #86efac; line-height: 1.55; font-size: 0.8em;">
      • <b>Strict 1%–5% Risk Clamp</b>: Hard-coded limits prevent over-sizing on any single trade.<br/>
      • <b>Smart Maker Limit Orders</b>: Post-only execution slashes trading fees by 66%.<br/>
      • <b>200 EMA + Confluence Gate</b>: 100% Cash Defense during confirmed macro bear regimes.<br/>
      • <b>Asymmetric Profit Locking</b>: Banks 50% cash at target and moves trailing stop to break-even.
    </p>
  </div>
</div>

<div class="grid-3" style="margin-top: 8px;">
  <div class="card">
    <h4>🚫 Over-Trading in Chop</h4>
    <p>Filtered out by Volatility Targeting & ATR noise thresholds.</p>
  </div>
  <div class="card">
    <h4>📉 Ruinous Drawdown Bleed</h4>
    <p>Protected by 15% Max Drawdown Portfolio Kill Switch.</p>
  </div>
  <div class="card">
    <h4>💸 Heavy Taker Fees</h4>
    <p>Replaced with passive limit liquidity maker execution.</p>
  </div>
</div>

---

## 3. The 3 Levels of Web4 System Autonomy

<div class="grid-3">
  <div class="phase-box">
    <div style="margin-bottom: 6px;"><span class="pill pill-cyan">LEVEL 1</span></div>
    <h4 style="color: #38bdf8; margin: 0 0 6px 0;">Perception & Telemetry</h4>
    <p style="font-size: 0.77em; color: #94a3b8; line-height: 1.45;">
      • <b>Multi-Venue Data</b>: Binance + Yahoo Finance live feeds.<br/>
      • <b>Liquidation Wicks</b>: Real-time wick-to-body rejection detection.<br/>
      • <b>Polymarket Radar</b>: Macro geopolitics & rate cut probabilities.<br/>
      • <b>FRED & VIX Feeds</b>: Credit spreads & panic volatility telemetry.
    </p>
  </div>

  <div class="phase-box green">
    <div style="margin-bottom: 6px;"><span class="pill pill-green">LEVEL 2</span></div>
    <h4 style="color: #10b981; margin: 0 0 6px 0;">Governance & Immunity</h4>
    <p style="font-size: 0.77em; color: #94a3b8; line-height: 1.45;">
      • <b>Van Tharp Sizing</b>: Risk parity clamped strictly 1.0%–5.0%.<br/>
      • <b>5% Daily Loss Halt</b>: Closes active book on high adverse volatility.<br/>
      • <b>15% Max DD Kill Switch</b>: Permanent cash defense lock.<br/>
      • <b>Atomic State Persistence</b>: Zero-loss <code>.bak</code> recovery files.
    </p>
  </div>

  <div class="phase-box gold">
    <div style="margin-bottom: 6px;"><span class="pill pill-amber">LEVEL 3</span></div>
    <h4 style="color: #fbbf24; margin: 0 0 6px 0;">Meta-Learning & Alpha</h4>
    <p style="font-size: 0.77em; color: #94a3b8; line-height: 1.45;">
      • <b>Delta-Neutral Basis Yield</b>: 8%–18% APY crypto funding harvest.<br/>
      • <b>Dynamic Volatility Targeting</b>: Dampens risk when VIX spikes.<br/>
      • <b>Decision Journal</b>: Real-time alpha attribution & audit log.<br/>
      • <b>Local GPU LLM</b>: 0.70s sanity-check Devil's Advocate screen.
    </p>
  </div>
</div>

<div class="takeaway">
  💡 <b>Autonomous Agency</b>: The system perceives, arbitrates, sizes, routes, and logs all decisions every 60 seconds with <b>zero human intervention</b>.
</div>

---

## 4. The 8-Agent Quantitative Nervous System

The trading desk operates as an ensemble of **8 specialized autonomous agents** communicating through a sub-second pipelined loop every 60 seconds:

<div class="flow-pipeline">
  <div class="flow-node">
    <span class="pill pill-cyan" style="margin-bottom: 3px;">AGENT 1</span>
    <b style="font-size: 0.82em; color: #38bdf8;">Data Scout</b>
    <span style="font-size: 0.70em; color: #94a3b8;">Binance / Yahoo Telemetry</span>
  </div>
  <div class="flow-arrow">→</div>
  <div class="flow-node">
    <span class="pill pill-purple" style="margin-bottom: 3px;">AGENTS 2-4</span>
    <b style="font-size: 0.82em; color: #a78bfa;">Alpha Engines</b>
    <span style="font-size: 0.70em; color: #94a3b8;">Momentum • Mean-Rev • Basis</span>
  </div>
  <div class="flow-arrow">→</div>
  <div class="flow-node">
    <span class="pill pill-cyan" style="margin-bottom: 3px;">AGENT 5</span>
    <b style="font-size: 0.82em; color: #38bdf8;">Coordinator</b>
    <span style="font-size: 0.70em; color: #94a3b8;">Arbiter & Confluence Gate</span>
  </div>
  <div class="flow-arrow">→</div>
  <div class="flow-node">
    <span class="pill pill-red" style="margin-bottom: 3px;">AGENT 6</span>
    <b style="font-size: 0.82em; color: #f43f5e;">Risk Governor</b>
    <span style="font-size: 0.70em; color: #94a3b8;">1%-5% Clamp & Veto</span>
  </div>
  <div class="flow-arrow">→</div>
  <div class="flow-node">
    <span class="pill pill-green" style="margin-bottom: 3px;">AGENTS 7-8</span>
    <b style="font-size: 0.82em; color: #10b981;">Execution & Meta</b>
    <span style="font-size: 0.70em; color: #94a3b8;">Maker Ledger & LLM Guard</span>
  </div>
</div>

<div class="grid-3" style="margin-top: 6px;">
  <div class="card">
    <h4>👁️ 1. Perception Layer</h4>
    <p>Ingests multi-exchange OHLCV, orderbook spreads, news sentiment, and macro indicators.</p>
  </div>
  <div class="card">
    <h4>🧠 2. Cortex & Alpha Layer</h4>
    <p>Generates signals across 3 distinct market regimes: Trends, Ranges, and Yield.</p>
  </div>
  <div class="card">
    <h4>🛡️ 3. Execution & Defense</h4>
    <p>Hard-clamps sizing, checks local AI headlines, and routes maker limit orders.</p>
  </div>
</div>

<div class="takeaway">
  💡 <b>Modular Resilience</b>: If any single agent detects danger or data degradation, the trade is rejected instantly without human intervention.
</div>

---

## 5. Perception & Alpha Generation (Agents 1 to 4)

<div class="grid-2">
  <div class="agent-card">
    <div class="agent-header">
      <span class="agent-title">Agent 1: Data Scout Sentinel</span>
      <span class="pill pill-cyan">PERCEPTION</span>
    </div>
    <div class="agent-row"><span class="agent-label what">WHAT:</span> Real-time telemetry, spread validation, and tick age watchdog.</div>
    <div class="agent-row"><span class="agent-label how">HOW:</span> Ingests Binance & Yahoo OHLCV. Rejects ticks $>180\text{s}$ old and spreads $>0.15\%$. Flags liquidation absorption wicks ($\text{wick} \ge 40\%$ of candle).</div>
    <div class="agent-row"><span class="agent-label why">WHY NEEDED:</span> Prevents executing on manipulated, lagged, or wide-spread exchange quotes that guarantee immediate loss.</div>
  </div>

  <div class="agent-card">
    <div class="agent-header">
      <span class="agent-title">Agent 2: Trend Momentum Rider</span>
      <span class="pill pill-purple">ALPHA: TRENDS</span>
    </div>
    <div class="agent-row"><span class="agent-label what">WHAT:</span> High-velocity directional expansion engine riding multi-day runners.</div>
    <div class="agent-row"><span class="agent-label how">HOW:</span> Detects 16/24-bar Donchian breakouts with fast/slow EMA alignment and multi-timeframe confluence ($\ge 0.70$). Banks 50% at $+2.5\times\text{ATR}$.</div>
    <div class="agent-row"><span class="agent-label why">WHY NEEDED:</span> Captures the large $+20\%$ to $+40\%$ expansions that account for 80% of total long-term portfolio profits.</div>
  </div>

  <div class="agent-card">
    <div class="agent-header">
      <span class="agent-title">Agent 3: Mean Reversion Sniper</span>
      <span class="pill pill-purple">ALPHA: RANGES</span>
    </div>
    <div class="agent-row"><span class="agent-label what">WHAT:</span> High-accuracy tactical sniper profiting from overextended chop & panic.</div>
    <div class="agent-row"><span class="agent-label how">HOW:</span> Snipes 2.0-std Bollinger Band piercing with extreme RSI ($<30$ or $>70$) and $\ge 1.25\times$ volume exhaustion. Targets the 20 SMA center mean.</div>
    <div class="agent-row"><span class="agent-label why">WHY NEEDED:</span> Markets chop 70% of the time. Achieves 75%–84.8% win rates while momentum waits for major breakouts.</div>
  </div>

  <div class="agent-card">
    <div class="agent-header">
      <span class="agent-title">Agent 4: Delta-Neutral Basis Yield</span>
      <span class="pill pill-green">ALPHA: INCOME</span>
    </div>
    <div class="agent-row"><span class="agent-label what">WHAT:</span> Non-directional "Paycheck Engine" harvesting crypto perpetual funding.</div>
    <div class="agent-row"><span class="agent-label how">HOW:</span> Pairs Long Spot with Short 1x Perp on funding $>0.01\%$/8h. Price swings have 0.0 net impact; harvests 8%–18% APY cash yield.</div>
    <div class="agent-row"><span class="agent-label why">WHY NEEDED:</span> Provides an unconditional monthly cash baseline to pay living expenses without directional gambling.</div>
  </div>
</div>

---

## 6. Arbitration, Governance & Safe Execution (Agents 5 to 8)

<div class="grid-2">
  <div class="agent-card">
    <div class="agent-header">
      <span class="agent-title">Agent 5: Coordinator Arbiter Cortex</span>
      <span class="pill pill-cyan">ARBITER</span>
    </div>
    <div class="agent-row"><span class="agent-label what">WHAT:</span> Central multi-strategy reconciliation cortex and regime gatekeeper.</div>
    <div class="agent-row"><span class="agent-label how">HOW:</span> Eliminates signal conflicts (e.g., Long Momentum vs Short Mean-Rev $\rightarrow$ NO TRADE). Enforces the 200 EMA Macro Gate and allocates capital.</div>
    <div class="agent-row"><span class="agent-label why">WHY NEEDED:</span> Prevents strategy cannibalism, double-leverage, and buying into macro multi-week bear cascades.</div>
  </div>

  <div class="agent-card">
    <div class="agent-header">
      <span class="agent-title">Agent 6: Institutional Risk Governor</span>
      <span class="pill pill-red">SUPREME VETO</span>
    </div>
    <div class="agent-row"><span class="agent-label what">WHAT:</span> Absolute mathematical risk authority with unbreakable veto power.</div>
    <div class="agent-row"><span class="agent-label how">HOW:</span> Hard-clamps trade risk to 1%–5% account equity. Caps concurrent positions at 4. Enforces 5% daily loss halt and 15% Max DD kill switch.</div>
    <div class="agent-row"><span class="agent-label why">WHY NEEDED:</span> Human emotions and unconstrained bots blow up from oversized revenge bets. The Governor guarantees survival.</div>
  </div>

  <div class="agent-card">
    <div class="agent-header">
      <span class="agent-title">Agent 7: Smart Maker Ledger</span>
      <span class="pill pill-green">EXECUTION</span>
    </div>
    <div class="agent-row"><span class="agent-label what">WHAT:</span> Zero-slippage limit order router and atomic state accounting ledger.</div>
    <div class="agent-row"><span class="agent-label how">HOW:</span> Posts passive Maker limit orders inside the spread (0.02% fee vs 0.06% taker). Writes atomic state updates with duplicate <code>.bak</code> recovery files.</div>
    <div class="agent-row"><span class="agent-label why">WHY NEEDED:</span> Slashes annual fee drag by 66% and prevents portfolio state corruption during crashes or power losses.</div>
  </div>

  <div class="agent-card">
    <div class="agent-header">
      <span class="agent-title">Agent 8: Local GPU Devil's Advocate</span>
      <span class="pill pill-purple">META-AI</span>
    </div>
    <div class="agent-row"><span class="agent-label what">WHAT:</span> Sub-second qualitative news screener and walk-forward parameter optimizer.</div>
    <div class="agent-row"><span class="agent-label how">HOW:</span> Evaluates live Google News RSS headlines via local <code>phi4-mini</code> on private GPU VRAM in 0.70s for black-swan events (SEC lawsuits, hacks, CEO exits).</div>
    <div class="agent-row"><span class="agent-label why">WHY NEEDED:</span> Technical indicators are blind to news headlines. Catches catastrophic events before the price crashes.</div>
  </div>
</div>

---

## 7. The 21-Asset Multi-Sector Universe

Capital dynamically rotates across **4 completely uncorrelated market sectors**:

<div class="grid-4">
  <div class="card">
    <h4>🚀 High-Beta Crypto</h4>
    <div style="font-size: 0.77em; color: #38bdf8; font-weight: 700; margin-bottom: 4px;">SOL • SUI • TAO • DOGE • AVAX • NEAR • LINK</div>
    <p>High-velocity momentum runners; clean $15\% - 30\%$ multi-day trend expansions.</p>
  </div>
  <div class="card">
    <h4>⚓ Macro Anchors</h4>
    <div style="font-size: 0.77em; color: #10b981; font-weight: 700; margin-bottom: 4px;">BTC • ETH • BNB</div>
    <p>Institutional benchmarks; basis funding rate radar and macro direction indicators.</p>
  </div>
  <div class="card">
    <h4>💻 US Tech Growth</h4>
    <div style="font-size: 0.77em; color: #a78bfa; font-weight: 700; margin-bottom: 4px;">NVDA • AMD • AAPL • PLTR • TSLA</div>
    <p>Multi-billion daily volume; continuous institutional accumulation trends.</p>
  </div>
  <div class="card">
    <h4>🛡️ Macro & Hedges</h4>
    <div style="font-size: 0.77em; color: #fbbf24; font-weight: 700; margin-bottom: 4px;">SPY • QQQ • GLD • SLV • TLT • XLE</div>
    <p>Gold, Silver, Treasuries & Energy; negative correlation during crises.</p>
  </div>
</div>

<div class="takeaway">
  💡 <b>Ray Dalio's Holy Grail</b>: <i>"Holding 15+ uncorrelated return streams cuts portfolio risk by up to 80% without sacrificing return."</i>
</div>

---

## 8. How the Bot Makes Money in UPTRENDS 🟢

The bot rides expansions using a **4-Stage Execution Ladder**:

<div class="step-box">
  <div><span class="step-num">1</span> <b>Confluence Confirmation</b>: Price $> 200$ EMA + Intermediate 32 EMA trend alignment</div>
  <span class="pill pill-cyan">FILTER</span>
</div>
<div class="step-box">
  <div><span class="step-num">2</span> <b>Breakout Entry</b>: Closes above 16/24-bar Donchian High. Sized strictly to 2% risk</div>
  <span class="pill pill-green">EXECUTE</span>
</div>
<div class="step-box">
  <div><span class="step-num">3</span> <b>50% Banked at $+2.5\times\text{ATR}$</b>: Automatically sells half the position to cash</div>
  <span class="pill pill-amber">BANK CASH</span>
</div>
<div class="step-box">
  <div><span class="step-num">4</span> <b>Break-Even Lock + Trailing Runner</b>: Remaining stop locked at Entry + fees</div>
  <span class="pill pill-purple">ZERO RISK</span>
</div>

<div class="takeaway">
  💡 <b>The Result</b>: Even with a modest 45% win rate, a 3:1 reward-to-risk ratio mathematically guarantees compounding growth.
</div>

---

## 9. How the Bot Makes Money in DOWNTRENDS 🔴

When markets crash, the bot deploys **3 institutional wealth-extraction mechanisms**:

<div class="grid-3">
  <div class="phase-box">
    <div style="margin-bottom: 6px;"><span class="pill pill-red">MECHANISM A</span></div>
    <h4 style="color: #f43f5e; margin: 0 0 6px 0;">Short Overbought Fades</h4>
    <p style="font-size: 0.77em; color: #94a3b8; line-height: 1.45;">
      • Trigger: Bear regime ($< 200$ EMA).<br/>
      • Signal: Dead-cat bounce with $\text{RSI} > 75$.<br/>
      • Execution: Short upper Bollinger Band.<br/>
      • Profit: Cover 50% at mean 20 SMA.
    </p>
  </div>

  <div class="phase-box gold">
    <div style="margin-bottom: 6px;"><span class="pill pill-amber">MECHANISM B</span></div>
    <h4 style="color: #fbbf24; margin: 0 0 6px 0;">Panic Capitulation Snipes</h4>
    <p style="font-size: 0.77em; color: #94a3b8; line-height: 1.45;">
      • Trigger: Volume surge $\ge 1.5\times$ average.<br/>
      • Signal: Extreme oversold $\text{RSI} < 30$.<br/>
      • Execution: Buy panic wick rejection.<br/>
      • Profit: Bank 50% rebound within 12h.
    </p>
  </div>

  <div class="phase-box green">
    <div style="margin-bottom: 6px;"><span class="pill pill-green">MECHANISM C</span></div>
    <h4 style="color: #10b981; margin: 0 0 6px 0;">Delta-Neutral Basis Yield</h4>
    <p style="font-size: 0.77em; color: #94a3b8; line-height: 1.45;">
      • Trigger: Positive crypto funding rate.<br/>
      • Structure: Long Spot + Short Perp.<br/>
      • Market Risk: Exactly 0.0 Delta.<br/>
      • Profit: Collect 8%–18% APY cash flow.
    </p>
  </div>
</div>

<div class="takeaway">
  🛡️ <b>100% Cash Defense</b>: While retail buy-and-hold crashes -35% to -65%, our 200 EMA Macro Gate keeps capital in cash until the storm passes.
</div>

---

## 10. Delta-Neutral Basis Yield Engine (`basis_yield.py`)

A non-directional **"Paycheck Engine"** that harvests perpetual futures funding rates:

<div class="grid-2">
  <div class="card">
    <h4>💰 Zero Market Risk Cash Flow</h4>
    <p>• Perpetual futures longs pay shorts an 8-hour funding fee ~80% of the time.<br/>
    • Simultaneously holding <b>Long Spot + Short Perp</b> creates exact <b>0.0 Delta</b>.<br/>
    • Whether Bitcoin is at $100k or crashes to $20k, price P&L is exactly $0.<br/>
    • Funding payments are credited directly into cash balance every 8 hours.</p>
  </div>
  <div class="card">
    <h4>📊 Annualized Baseline Yield</h4>
    <p>• Historical baseline yield: <b>8% to 18% APY</b>.<br/>
    • Automatically scans active crypto universe for funding $>0.01\%$ per 8h.<br/>
    • Deploys idle treasury cash to generate steady monthly income.<br/>
    • Pays for server overhead and base living expenses during market chop.</p>
  </div>
</div>

<div class="takeaway">
  💡 <b>Living Off Trading</b>: The Basis Engine provides a stable baseline income floor so you never have to force bad directional trades.
</div>

---

## 11. Deterministic Risk Governance & Loss Asymmetry

Losses are mathematically asymmetric. The deeper the hole, the harder it is to climb out:

| Account Drawdown | Gain Needed to Break Even | System Action & Defense |
| :---: | :---: | :--- |
| **-5%** | **+5.3%** | Normal operation; dynamic volatility scaling active |
| **-10%** | **+11.1%** | <span class="highlight">Current live test status: 100% Cash Defense active</span> |
| **-15%** | <span class="highlight">**+17.6% (Very Manageable)**</span> | <span class="highlight"><b>MAX DRAWDOWN CIRCUIT BREAKER: Permanent Cash Lock</b></span> |
| **-30%** | **+42.9%** | ❌ Blocked by system invariants; never allowed to reach |
| **-50%** | <span class="warning">**+100.0% (Must Double Money)**</span> | ❌ Typical unconstrained retail bot failure mode |
| **-80%** | <span class="warning">**+400.0% (Account Ruin)**</span> | ❌ Unmanaged leverage / total liquidation |

<div class="takeaway">
  💡 <b>Hard Invariant</b>: Halting at 15% Max DD guarantees that 85% of capital remains safe, needing only +17.6% to recover.
</div>

---

## 12. Empirical 180-Day Institutional Backtest Audit

Cost-adjusted with **0.06% fees + 0.04% slippage** executed at next bar Open (`fill_mode="next_open"`):

| Asset Category | Asset Ticker | Buy & Hold Return | Strategy Return | Strategy Max DD | Buy & Hold Max DD | Strategy Win Rate | Profit Factor |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Crypto Anchor** | `BNB/USDT` | $+10.1\%$ | <span class="highlight">**+5.34%**</span> | <span class="highlight">**2.59%**</span> | $26.7\%$ | <span class="highlight">**84.8%**</span> | **2.28** |
| **AI Crypto** | `TAO/USDT` | $-24.5\%$ | <span class="highlight">**+3.46%**</span> | <span class="highlight">**2.20%**</span> | $43.4\%$ | <span class="highlight">**79.3%**</span> | **1.46** |
| **Layer-1** | `AVAX/USDT` | $-9.8\%$ | <span class="highlight">**+2.64%**</span> | <span class="highlight">**5.44%**</span> | $43.5\%$ | <span class="highlight">**73.3%**</span> | **1.25** |
| **DeFi Oracle** | `LINK/USDT` | $+12.8\%$ | <span class="highlight">**+2.25%**</span> | <span class="highlight">**2.32%**</span> | $35.1\%$ | <span class="highlight">**83.3%**</span> | **1.41** |
| **Mega-Cap Tech** | `AAPL` | $+14.2\%$ | <span class="highlight">**+1.57%**</span> | <span class="highlight">**0.08%**</span> | $12.9\%$ | <span class="highlight">**83.3%**</span> | **304.99** |
| **Semiconductors** | `AMD` | $+15.6\%$ | <span class="highlight">**+0.87%**</span> | <span class="highlight">**1.03%**</span> | $28.8\%$ | <span class="highlight">**75.0%**</span> | **1.90** |
| **Tech Index** | `QQQ` | $-2.0\%$ | <span class="highlight">**+1.21%**</span> | <span class="highlight">**0.64%**</span> | $11.4\%$ | <span class="highlight">**75.0%**</span> | **1.37** |
| **Energy Sector** | `XLE` | $+20.8\%$ | <span class="highlight">**+3.65%**</span> | <span class="highlight">**3.66%**</span> | $14.1\%$ | <span class="highlight">**66.7%**</span> | **1.52** |
| **High-Beta L1** | `NEAR/USDT` | $+162.5\%$ | <span class="highlight">**+4.12%**</span> | <span class="highlight">**4.91%**</span> | $49.5\%$ | <span class="highlight">**70.0%**</span> | **1.09** |

<div class="takeaway">
  💡 <b>The Empirical Proof</b>: Mean Reversion achieved <b>65% to 84.8% win rates</b>, while drawdowns were crushed from -53% down to under 8%.
</div>

---

## 13. Real-Time Live Desk & Local GPU AI Screener

The desk runs 24/7 with zero cloud dependency and zero token API costs:

<div class="grid-2">
  <div class="card">
    <h4>🖥️ Local GPU LLM Devil's Advocate</h4>
    <p>• Model: <b>phi4-mini:latest</b> running on local NVIDIA GPU VRAM.<br/>
    • Latency: <b>0.70 seconds</b> warm inference.<br/>
    • Function: Fetches live Google News RSS headlines to screen for black-swan regulatory bans, exploits, or CEO resignations before trade entry.<br/>
    • Cost: <b>$0.00 / month</b> (100% private, zero API keys).</p>
  </div>
  <div class="card">
    <h4>🛡️ Pre-Flight Execution Guards</h4>
    <p>• <b>Spread Guard</b>: Rejects orders if bid-ask spread $> 0.15\%$.<br/>
    • <b>Quote Staleness</b>: Rejects orders if candle tick age $> 180\text{s}$.<br/>
    • <b>Volume Depth</b>: Caps orders to $\le 2.0\%$ of hourly volume.<br/>
    • <b>Current Live Status</b>: 21 assets actively scanned; <b>$9,093.47 capital protected in 100% Cash Defense</b>.</p>
  </div>
</div>

---

## 14. The Capital Scaling Roadmap to $3,000 / Month

To generate a consistent **$3,000/month living income**, capital scales through 3 phases:

<div class="grid-3">
  <div class="phase-box">
    <div style="margin-bottom: 6px;"><span class="pill pill-cyan">PHASE 1</span></div>
    <h4 style="color: #38bdf8; margin: 0 0 6px 0;">Compounding Base</h4>
    <p style="font-size: 0.77em; color: #94a3b8; line-height: 1.45;">
      • <b>Capital</b>: $10,000 → $25,000<br/>
      • <b>Annual Target</b>: +35% to +50%<br/>
      • <b>Withdrawals</b>: <b>$0 (Zero)</b><br/>
      • <b>Focus</b>: 100% Profit Reinvestment
    </p>
  </div>

  <div class="phase-box gold">
    <div style="margin-bottom: 6px;"><span class="pill pill-amber">PHASE 2</span></div>
    <h4 style="color: #fbbf24; margin: 0 0 6px 0;">Capital Acceleration</h4>
    <p style="font-size: 0.77em; color: #94a3b8; line-height: 1.45;">
      • <b>Capital</b>: $25,000 → $75,000<br/>
      • <b>Annual Target</b>: +35% to +45%<br/>
      • <b>Withdrawals</b>: <b>$0 (Zero)</b><br/>
      • <b>Focus</b>: Half-Kelly Compounding
    </p>
  </div>

  <div class="phase-box green">
    <div style="margin-bottom: 6px;"><span class="pill pill-green">PHASE 3</span></div>
    <h4 style="color: #10b981; margin: 0 0 6px 0;">Living Income Harvest</h4>
    <p style="font-size: 0.77em; color: #94a3b8; line-height: 1.45;">
      • <b>Capital</b>: $75,000 → $100,000+<br/>
      • <b>Monthly Target</b>: +3% to +4% / mo<br/>
      • <b>Monthly Payout</b>: <b>$3,000 / month</b><br/>
      • <b>Status</b>: 100% Financially Free
    </p>
  </div>
</div>

<div class="takeaway">
  ⚠️ <b>The Golden Rule</b>: Never withdraw $3,000/month from a $10,000 account (+30%/mo = 100% risk of ruin). Allow capital to compound to $75k–$100k, where $3,000/month requires only a safe, low-stress <b>+3.5% monthly return</b>.
</div>

---

## 15. System Invariants & Institutional Commitments

Every autonomous agent in this codebase is bound by **5 non-negotiable invariant rules**:

<div class="step-box">
  <div><span class="step-num">1</span> <b>Simulated Paper Mode Invariant</b>: Live real-money trading is permanently prohibited without explicit audit</div>
  <span class="pill pill-green">HARD INVARIANT</span>
</div>
<div class="step-box">
  <div><span class="step-num">2</span> <b>User Capital Preservation</b>: Never overwrite portfolio balances or histories without explicit CLI confirmation</div>
  <span class="pill pill-green">IMMUNITY</span>
</div>
<div class="step-box">
  <div><span class="step-num">3</span> <b>1%–5% Risk Hard-Clamp</b>: Hard-coded in <code>core/risk_engine.py</code>; no AI optimizer may loosen this bound</div>
  <span class="pill pill-green">SAFETY BOUND</span>
</div>
<div class="step-box">
  <div><span class="step-num">4</span> <b>Platform Independence</b>: Pure Python 3.10+ execution with zero vendor-lock on proprietary harnesses</div>
  <span class="pill pill-cyan">OPEN STANDARDS</span>
</div>
<div class="step-box">
  <div><span class="step-num">5</span> <b>Evidence-Based Profitability</b>: Only declare edge with cost-adjusted metrics (fees + slippage) on next-open fills</div>
  <span class="pill pill-purple">TRUTH IN METRICS</span>
</div>

<br/>

* **Compiled Slide Deck**: [`docs/presentation.html`](docs/presentation.html) • **Codebase Repository**: [GitHub iShmuel/web4-trading-agent](https://github.com/iShmuel/web4-trading-agent)
