# Web4 Autonomous Trading Desk — Presentation Speaking Guide

Use this 5-minute slide-by-slide script alongside `web4_trading_agent_presentation.pdf` / `.pptx`, followed by a 30-second live demo at `http://127.0.0.1:8080`.

---

## Slide 1 — Executive Overview
> "This is **Web4**, an autonomous multi-strategy **paper-trading** desk across crypto and US stocks. It runs **8 strategies** in parallel, enforces strict `1%–5%` risk limits, and improves its own parameters through a 4-gate safety governor — with zero real money at risk."

## Slide 2 — The Big Picture (5-Stage Funnel)
> "Every 60 seconds, live market prices pass through a 5-stage funnel before a single virtual dollar is committed: market data checks, regime & compliance gates, pre-flight liquidity checks, AI news veto, and hard risk sizing."

## Slide 3 — Architecture (Math vs. Rules vs. Local AI)
> "A key design rule is **we never let an LLM guess prices or position sizes**. Deterministic math handles indicators, sizing, and stops; hard rules enforce risk bounds; and our local AI (`phi4-mini`) only reads headlines and reviews governance proposals."

## Slide 4 — Inside a 60-Second Tick
> "When strategies disagree, the bot stays safely in cash. When a trade enters, it risks at most `2%` of equity, locks in break-even at `+1.2x ATR`, scales out `50%` at target, and trails the rest with a Chandelier stop."

## Slide 5 — The 5 Core Building Blocks
> "Markets only trend about 30% of the time, so we don't rely on one strategy. We combine **8 engines** — trend (`Momentum`, `Trend Pullback`, `Vol Squeeze`, `NR7 Breakout`), reversal (`Mean Reversion`, `VWAP Reversion`), market-neutral (`Stat-Arb Pairs`), and passive carry (`Basis Yield`) — routed dynamically every 6 hours."

## Slide 6 — Out-of-Sample Evidence
> "In our earlier trending 90-day window, the portfolio achieved **`+11.35%` to `+15.60%`** out-of-sample. Even in the much choppier recent 90-day window, our **Dynamic Rolling Router** scored **`95.3/100` CPI (`+2.48%` return, `2.15%` max drawdown, `3.23` profit factor)** while naive single-strategy baselines lost over `-20%`."

## Slide 7 — LLM Governance & 4-Gate Safety
> "Every 6 hours, Agent 11 searches for better parameters, and Agent 12 (the Governor) tests them against 4 strict gates: invariant bounds, all `202` automated tests, out-of-sample Do-No-Harm checks, and AI risk review. Anything that overfits — like `PROP-ED02E10E` — is automatically blocked."

## Slide 8 — Honest Limitations & Live Demo
> "To be transparent: this is strictly paper trading (`PaperAccount`), and our live paper balance (`~$9,141`, `-8.58%`) still reflects early losses from before our recent pair blacklists, Hurst gate, and router upgrades. Let me show you the **live web terminal** running right now."

---

## Live Demo Tip (30 seconds at `http://127.0.0.1:8080`)
1. Point out the **left KPI sidebar** (`RUNNING PID`, `Paper Only`, `VIX: STABLE`, `Basis Yield Carry`).
2. Click a dot on the **Portfolio Equity Curve** to show how it jumps straight to that fill in **All Closed Fills**.
3. Open **Strategies & Scanners ▾ → Basis Yield Carry** (or **Mean Reversion**) to show live parameters, 8h funding APRs, and the Dynamic Rolling Router in action.
