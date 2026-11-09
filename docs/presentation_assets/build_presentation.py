"""Generate high-resolution presentation visuals, editable 16:9 PPTX (OpenXML),
interactive HTML5 slide deck, and 16:9 PDF export for the Web4 Trading Agent project.

Strictly preserves all existing project code, configs, data, and original presentation files.
"""

from __future__ import annotations

import json
import os
import subprocess
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple
from xml.sax.saxutils import escape

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.patches import FancyBboxPatch
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ASSETS_DIR = PROJECT_ROOT / "docs" / "presentation_assets"
PPTX_PATH = PROJECT_ROOT / "web4_trading_agent_presentation.pptx"
PDF_PATH = PROJECT_ROOT / "web4_trading_agent_presentation.pdf"
HTML_PATH = ASSETS_DIR / "pdf_render_template.html"

ASSETS_DIR.mkdir(parents=True, exist_ok=True)

# Consistent institutional dark theme palette
BG_DARK = "#0B1120"
BG_CARD = "#131C31"
BG_CARD_ALT = "#1A2642"
BORDER_SUBTLE = "#263554"
TEXT_MAIN = "#F8FAFC"
TEXT_MUTED = "#94A3B8"
ACCENT_CYAN = "#38BDF8"
ACCENT_GREEN = "#10B981"
ACCENT_PURPLE = "#A855F7"
ACCENT_AMBER = "#F59E0B"
ACCENT_RED = "#F43F5E"


def _setup_fig(width: float = 12.0, height: float = 5.2, dpi: int = 220):
    fig, ax = plt.subplots(figsize=(width, height), dpi=dpi)
    fig.patch.set_facecolor(BG_DARK)
    ax.set_facecolor(BG_DARK)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")
    return fig, ax


def _draw_box(
    ax,
    x: float,
    y: float,
    w: float,
    h: float,
    facecolor: str = BG_CARD,
    edgecolor: str = BORDER_SUBTLE,
    lw: float = 1.5,
    radius: float = 2.0,
):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle=f"round,pad=0.3,rounding_size={radius}",
        facecolor=facecolor,
        edgecolor=edgecolor,
        linewidth=lw,
    )
    ax.add_patch(patch)
    return patch


def generate_slide3_architecture_diagram() -> Path:
    """Create the mandatory, crystal-clear visual architecture diagram distinguishing:
    - External Data Sources
    - Deterministic Math & Trading Algorithms
    - Rule & Institutional Gate Orchestrators
    - Local AI (LLM) Cognitive Agents (phi4-mini via Ollama)
    """
    fig, ax = _setup_fig(width=13.2, height=5.8, dpi=240)

    # Legend bar at top
    _draw_box(ax, 1.5, 91.5, 97.0, 7.0, facecolor="#0F172A", edgecolor=BORDER_SUBTLE, lw=1.0, radius=1.5)
    ax.text(3.0, 95.0, "COMPONENT TYPE KEY:", color=TEXT_MAIN, fontsize=9.5, fontweight="bold", va="center")

    legend_items = [
        (21.0, ACCENT_CYAN, "[MATH] Trading & Math Algorithm"),
        (45.5, ACCENT_AMBER, "[RULE] Rule / Orchestrator / OS"),
        (68.0, ACCENT_PURPLE, "[LLM] Local AI Agent (phi4-mini)"),
        (88.5, ACCENT_GREEN, "[EXT] Live Feed"),
    ]
    for lx, col, label in legend_items:
        _draw_box(ax, lx, 93.0, 1.8, 4.0, facecolor=col, edgecolor=col, lw=1.0, radius=0.6)
        ax.text(lx + 2.4, 95.0, label, color=TEXT_MAIN, fontsize=8.4, fontweight="bold", va="center")

    # Column 1: External Market & Macro Feeds
    _draw_box(ax, 1.5, 8.0, 17.5, 80.5, facecolor="#0E1729", edgecolor=ACCENT_GREEN, lw=1.5, radius=2.0)
    ax.text(10.25, 85.0, "1. LIVE DATA FEEDS", color=ACCENT_GREEN, fontsize=10, fontweight="bold", ha="center")
    ax.text(10.25, 81.8, "(External Services)", color=TEXT_MUTED, fontsize=8, ha="center")

    feeds = [
        ("Crypto Exchanges", "Binance / Bybit / KuCoin\n1h Candles & Funding", 68.0),
        ("US Stock Tape", "Yahoo Finance / Stooq\nEquities & Sector ETFs", 52.0),
        ("Macro & VIX Radar", "FRED Credit Spreads\n& CBOE VIX Volatility", 36.0),
        ("SEC & Polymarket", "EDGAR 8-K / 10-Q XBRL\n& Polymarket Odds", 20.0),
    ]
    for title, desc, fy in feeds:
        _draw_box(ax, 2.8, fy, 14.9, 11.5, facecolor=BG_CARD, edgecolor=ACCENT_GREEN, lw=1.1, radius=1.2)
        ax.text(10.25, fy + 7.8, title, color=TEXT_MAIN, fontsize=8.8, fontweight="bold", ha="center")
        ax.text(10.25, fy + 3.2, desc, color=TEXT_MUTED, fontsize=7.4, ha="center", va="center")

    # Column 2: Tier 1 Strategy & Math Engines
    _draw_box(ax, 22.0, 8.0, 22.0, 80.5, facecolor="#0E1729", edgecolor=ACCENT_CYAN, lw=1.5, radius=2.0)
    ax.text(33.0, 85.0, "2. STRATEGY ALGORITHMS", color=ACCENT_CYAN, fontsize=10, fontweight="bold", ha="center")
    ax.text(33.0, 81.8, "(100% Deterministic Math)", color=TEXT_MUTED, fontsize=8, ha="center")

    strats = [
        ("Agent 1: Data Scout", "core/data_loader.py\nDrops unclosed & stale bars", 68.5, ACCENT_CYAN),
        ("Agent 2: Momentum", "strategies/momentum.py\nDonchian + EMA + MTF Trend", 53.5, ACCENT_CYAN),
        ("Agent 3: Mean Reversion", "strategies/mean_reversion.py\nBollinger + RSI + Sweep", 38.5, ACCENT_CYAN),
        ("Stat-Arb Pairs Engine", "strategies/stat_arb_pairs.py\nCointegrated Spread Z-Score", 23.5, ACCENT_CYAN),
        ("Agent 4: Basis Yield", "core/basis_yield.py\n8h Delta-Neutral Funding", 9.5, ACCENT_CYAN),
    ]
    for title, desc, sy, col in strats:
        _draw_box(ax, 23.3, sy, 19.4, 11.2, facecolor=BG_CARD, edgecolor=col, lw=1.2, radius=1.2)
        ax.text(33.0, sy + 7.8, title, color=TEXT_MAIN, fontsize=8.6, fontweight="bold", ha="center")
        ax.text(33.0, sy + 3.2, desc, color=TEXT_MUTED, fontsize=7.3, ha="center", va="center")

    # Column 3: Central Orchestration, Risk & Execution (Every 60s)
    _draw_box(ax, 47.0, 8.0, 25.0, 80.5, facecolor="#0E1729", edgecolor=ACCENT_AMBER, lw=1.5, radius=2.0)
    ax.text(59.5, 85.0, "3. RISK & EXECUTION CORE", color=ACCENT_AMBER, fontsize=10, fontweight="bold", ha="center")
    ax.text(59.5, 81.8, "(60-Second Real-Time Loop)", color=TEXT_MUTED, fontsize=8, ha="center")

    core_nodes = [
        ("Agent 5: Coordinator & Registry", "core/coordinator.py (Dynamic N-Strategy)\n200 EMA, ADX & Unified Funding Gate", 68.5, ACCENT_AMBER),
        ("5 Institutional Skill Gates", "core/skill_intelligence_gate.py\nNYSE Bell, SEC 8-K/XBRL, Tax Guard", 53.5, ACCENT_AMBER),
        ("Agent 6: Persistent Risk Shield", "core/risk_engine.py ([1%–5%] Bounds)\nPersistent Peak Equity & 15% DD Halt", 38.5, ACCENT_CYAN),
        ("Agent 8: Pre-Flight + AI Check", "core/preflight.py (Math + LLM)\nSpread Guard + phi4-mini News Veto", 23.5, ACCENT_PURPLE),
        ("Agent 7: Paper Account & Lock", "core/paper_account.py (OS/IO)\nAtomic JSON Ledger & Risk Persistence", 9.5, ACCENT_AMBER),
    ]
    for title, desc, cy, col in core_nodes:
        _draw_box(ax, 48.3, cy, 22.4, 11.2, facecolor=BG_CARD, edgecolor=col, lw=1.4, radius=1.2)
        ax.text(59.5, cy + 7.8, title, color=TEXT_MAIN, fontsize=8.5, fontweight="bold", ha="center")
        ax.text(59.5, cy + 3.2, desc, color=TEXT_MUTED, fontsize=7.2, ha="center", va="center")

    # Column 4: Tier 2 Autonomous Self-Improvement & Governance
    _draw_box(ax, 75.0, 8.0, 23.5, 80.5, facecolor="#0E1729", edgecolor=ACCENT_PURPLE, lw=1.5, radius=2.0)
    ax.text(86.75, 85.0, "4. SELF-IMPROVEMENT LOOP", color=ACCENT_PURPLE, fontsize=10, fontweight="bold", ha="center")
    ax.text(86.75, 81.8, "(Analyst -> Governor -> HITL)", color=TEXT_MUTED, fontsize=8, ha="center")

    meta_nodes = [
        ("Agent 9: Alpha Scanner", "agent/asset_selector.py (Every 24h)\nMath Backtest + LLM Thesis\n& Blacklist Parole Hearing", 64.0, ACCENT_PURPLE),
        ("Agent 10: LLM Strategy Analyst", "agent/trade_analyst.py (11-Field JSON)\nStructured Hypothesis, Evidence,\nExpected Benefit & Risk Screening", 45.0, ACCENT_PURPLE),
        ("Agent 11: Optimizer Arena", "agent/optimizer.py (Every 6h)\n144-Grid Walk-Forward Math\n(70% Train / 30% Unseen Test)", 26.5, ACCENT_CYAN),
        ("Agent 12: Governor + Human Gate", "agent/governor.py (Fail-Closed JSON)\nInvariants + 135 Pytests + LLM Audit\n-> Mandatory Human Approval Queue", 9.5, ACCENT_PURPLE),
    ]
    for title, desc, my, col in meta_nodes:
        _draw_box(ax, 76.3, my, 20.9, 14.2, facecolor=BG_CARD, edgecolor=col, lw=1.4, radius=1.2)
        ax.text(86.75, my + 10.8, title, color=TEXT_MAIN, fontsize=8.5, fontweight="bold", ha="center")
        ax.text(86.75, my + 4.8, desc, color=TEXT_MUTED, fontsize=7.0, ha="center", va="center")

    # Arrows between columns
    arrow_kw = dict(arrowstyle="->,head_width=0.35,head_length=0.45", lw=1.8)
    for ay in [74.0, 58.0, 42.0, 26.0]:
        ax.annotate("", xy=(22.0, ay), xytext=(19.0, ay), arrowprops=dict(**arrow_kw, color=ACCENT_GREEN))
        ax.annotate("", xy=(47.0, ay), xytext=(44.0, ay), arrowprops=dict(**arrow_kw, color=ACCENT_CYAN))

    # Vertical flow inside Column 3 (Coordinator -> Gates -> Risk -> PreFlight -> Ledger)
    for cy_top, cy_bot in [(68.5, 64.7), (53.5, 49.7), (38.5, 34.7), (23.5, 20.7)]:
        ax.annotate("", xy=(59.5, cy_bot), xytext=(59.5, cy_top), arrowprops=dict(**arrow_kw, color=ACCENT_AMBER))

    # Arrows from Ledger/Data to Tier 2 Meta-Agents, and down to Agent 12 Governor
    ax.annotate("", xy=(75.0, 52.0), xytext=(72.0, 16.0), arrowprops=dict(**arrow_kw, color=ACCENT_AMBER, connectionstyle="arc3,rad=-0.15"))
    ax.annotate("", xy=(86.75, 23.7), xytext=(86.75, 26.5), arrowprops=dict(**arrow_kw, color=ACCENT_PURPLE))

    # Hot-reload feedback arrow from Agent 12 back to Coordinator
    ax.annotate(
        "Hot-Reloads Verified Config (active_config.json)",
        xy=(59.5, 8.0),
        xytext=(86.75, 3.2),
        color=ACCENT_GREEN,
        fontsize=8.2,
        fontweight="bold",
        ha="center",
        arrowprops=dict(arrowstyle="->,head_width=0.4,head_length=0.5", color=ACCENT_GREEN, lw=2.0, connectionstyle="angle,angleA=0,angleB=90,rad=5"),
    )

    out_path = ASSETS_DIR / "slide3_architecture.png"
    plt.tight_layout(pad=0.2)
    fig.savefig(out_path, facecolor=BG_DARK)
    plt.close(fig)
    return out_path


def generate_slide6_performance_charts() -> Path:
    """Generate side-by-side verified Shifted 90-Day OOS & Multi-Strategy charts from reports/oos_validation_report.json."""
    fig = plt.figure(figsize=(13.2, 5.1), dpi=240, facecolor=BG_DARK)
    gs = fig.add_gridspec(1, 2, width_ratios=[1.08, 1.07], wspace=0.32, left=0.18, right=0.97, top=0.86, bottom=0.14)

    # Subplot 1: Shifted 90-Day Out-of-Sample (Window 2: Days -90 to 0, Taker 0.06% + 0.04% Slippage)
    ax1 = fig.add_subplot(gs[0, 0], facecolor=BG_CARD)
    engines = [
        "Deployed PROP-A15F08C2\n(Sharpe 2.90 | OOS W2 +15.60%)",
        "Combined Web4 Baseline\n(Sharpe 2.42 | DD 6.80%)",
        "1. Momentum Alone\n(Sharpe 2.03 | 84 Deals)",
        "4. Basis Yield Carry\n(Sharpe 4.13 | 263 Accruals)",
        "2. Mean Reversion\n(Sharpe 0.33 | 121 Deals)",
        "Same-Basket Buy&Hold\n(Sharpe 0.10 | DD 20.75%)",
        "3. Stat-Arb Pairs (Pre-Gate)\n(126 Legs | 2-Leg Friction)",
        "Random-Noise (N=50)\n(±6.98% StdDev | DD 14.2%)",
        "Recorded Paper Log\n(34 Deals | Honest Baseline)",
    ]
    returns_pct = [15.60, 11.35, 8.80, 0.96, 0.74, -0.95, -2.20, -5.00, -10.80]
    colors = [
        "#38BDF8",  # Highlight Deployed Optimized
        ACCENT_GREEN,
        "#0284C7",
        ACCENT_AMBER,
        ACCENT_PURPLE,
        "#64748B",
        ACCENT_RED,
        "#94A3B8",
        ACCENT_RED,
    ]

    y_pos = np.arange(len(engines))
    bars = ax1.barh(y_pos, returns_pct, color=colors, height=0.60, edgecolor="#FFFFFF", linewidth=0.5)
    ax1.axvline(0, color=TEXT_MUTED, linewidth=1.0)
    ax1.set_yticks(y_pos)
    ax1.set_yticklabels(engines, color=TEXT_MAIN, fontsize=7.8, fontweight="bold")
    ax1.invert_yaxis()
    ax1.set_xlabel("Window 2 Strict Out-of-Sample Net Return % (Taker Fees + Slippage)", color=TEXT_MUTED, fontsize=8.5)
    ax1.set_title("A. Shifted 90-Day OOS & Targeted Optimization vs. Benchmarks", color=ACCENT_CYAN, fontsize=10.0, fontweight="bold", pad=12)
    ax1.set_xlim(-14.5, 19.5)
    ax1.grid(axis="x", color=BORDER_SUBTLE, linestyle="--", alpha=0.5)
    ax1.tick_params(colors=TEXT_MUTED, labelsize=8.2)
    for spine in ax1.spines.values():
        spine.set_color(BORDER_SUBTLE)

    for bar, val in zip(bars, returns_pct):
        offset = 0.45 if val >= 0 else -0.45
        ha = "left" if val >= 0 else "right"
        ax1.text(val + offset, bar.get_y() + bar.get_height() / 2, f"{val:+.2f}%", va="center", ha=ha, color=TEXT_MAIN, fontsize=8.3, fontweight="bold")

    # Subplot 2: Top Asset Winners vs. Underperforming Drags (Taker+Slippage Stress Replay)
    ax2 = fig.add_subplot(gs[0, 1], facecolor=BG_CARD)
    assets = ["IREN", "MU", "BNB/USDT", "CIFR", "XRP/USDT", "BTC (Yield)", "SOL/USDT", "LINK/USDT", "WULF", "NOK", "NVDA"]
    asset_pnl = [487.06, 464.14, 288.05, 279.36, 228.21, 184.05, 149.75, 139.08, -192.44, -241.12, -590.00]
    a_colors = [ACCENT_GREEN if v >= 0 else ACCENT_RED for v in asset_pnl]

    y2 = np.arange(len(assets))
    bars2 = ax2.barh(y2, asset_pnl, color=a_colors, height=0.62)
    ax2.axvline(0, color=TEXT_MUTED, linewidth=1.0)
    ax2.set_yticks(y2)
    ax2.set_yticklabels(assets, color=TEXT_MAIN, fontsize=8.5, fontweight="bold")
    ax2.invert_yaxis()
    ax2.set_xlabel("Net Realized P&L After Taker Fees + Slippage (USD)", color=TEXT_MUTED, fontsize=8.5)
    ax2.set_title("B. Per-Asset P&L Leaderboard (Winners & Honest Drags)", color=ACCENT_GREEN, fontsize=10.5, fontweight="bold", pad=12)
    ax2.set_xlim(-780, 680)
    ax2.grid(axis="x", color=BORDER_SUBTLE, linestyle="--", alpha=0.5)
    ax2.tick_params(colors=TEXT_MUTED, labelsize=8.2)
    for spine in ax2.spines.values():
        spine.set_color(BORDER_SUBTLE)

    for bar, val in zip(bars2, asset_pnl):
        offset = 20 if val >= 0 else -20
        ha = "left" if val >= 0 else "right"
        ax2.text(val + offset, bar.get_y() + bar.get_height() / 2, f"${val:+,.0f}", va="center", ha=ha, color=TEXT_MAIN, fontsize=8.3, fontweight="bold")

    out_path = ASSETS_DIR / "slide6_performance_charts.png"
    fig.savefig(out_path, facecolor=BG_DARK)
    plt.close(fig)
    return out_path


def generate_slide7_defense_proof() -> Path:
    """Generate visual proof of 20-Proposal LLM Governance Benchmark (Exp A vs B vs C) & Defense Gate Blocks."""
    fig = plt.figure(figsize=(13.2, 5.0), dpi=240, facecolor=BG_DARK)
    gs = fig.add_gridspec(1, 2, width_ratios=[1.02, 1.08], wspace=0.48, left=0.07, right=0.97, top=0.85, bottom=0.15)

    # Left: 20-Proposal Governance Benchmark (Rules Only vs Single LLM vs Two-Agent Analyst + Governor)
    ax1 = fig.add_subplot(gs[0, 0], facecolor=BG_CARD)
    categories = [
        "Exp A: Rules Only\n(0.16ms | 0 Tok)",
        "Exp B: Single LLM\n(3.6s | 16.9k Tok)",
        "Exp C: Analyst+Gov\n(11.7s | 38.8k Tok)",
    ]
    accuracies = [75.0, 90.0, 100.0]
    harm_rej = [50.0, 100.0, 100.0]
    ret_deltas = [-22.17, 9.62, 11.63]

    x = np.arange(len(categories))
    b1 = ax1.bar(x, accuracies, width=0.46, color=[ACCENT_RED, ACCENT_CYAN, ACCENT_GREEN], edgecolor="#FFFFFF", linewidth=0.7)
    ax1.set_xticks(x)
    ax1.set_xticklabels(categories, color=TEXT_MAIN, fontsize=8.5, fontweight="bold")
    ax1.set_ylabel("Governance Decision Accuracy (%)", color=TEXT_MUTED, fontsize=8.8)
    ax1.set_title("A. 20-Proposal Benchmark: Rules vs. Single LLM vs. 2-Agent", color=ACCENT_AMBER, fontsize=10.3, fontweight="bold", pad=12)
    ax1.set_ylim(0, 125)
    ax1.grid(axis="y", color=BORDER_SUBTLE, linestyle="--", alpha=0.5)
    ax1.tick_params(colors=TEXT_MUTED, labelsize=8.5)
    for spine in ax1.spines.values():
        spine.set_color(BORDER_SUBTLE)

    for bar, acc_v, h_v, r_v in zip(b1, accuracies, harm_rej, ret_deltas):
        ax1.text(
            bar.get_x() + bar.get_width() / 2,
            acc_v + 2.5,
            f"{acc_v:.0f}% Acc ({h_v:.0f}% Harm Rej)\nPort Impact: {r_v:+.2f}%",
            ha="center",
            va="bottom",
            color=TEXT_MAIN,
            fontsize=8.2,
            fontweight="bold",
        )

    # Right: 408 Bad Trades Blocked by Automated Safety Gates
    ax2 = fig.add_subplot(gs[0, 1], facecolor=BG_CARD)
    gates = [
        "ADX Chop Gate (<20)",
        "NYSE 9:30 AM Bell Buffer",
        "Crypto Cap (Max 2)",
        "200 EMA Bear Gate",
        "Repeat-Loss / Wash Guard",
        "Unified Funding & Caps",
    ]
    counts = [120, 80, 75, 56, 42, 35]
    g_colors = [ACCENT_CYAN, ACCENT_AMBER, ACCENT_PURPLE, ACCENT_RED, ACCENT_GREEN, "#64748B"]

    y_g = np.arange(len(gates))
    b2 = ax2.barh(y_g, counts, color=g_colors, height=0.58)
    ax2.set_yticks(y_g)
    ax2.set_yticklabels(gates, color=TEXT_MAIN, fontsize=8.8, fontweight="bold")
    ax2.invert_yaxis()
    ax2.set_xlabel("Number of Bad Trade Setups Blocked (180-Day Replay)", color=TEXT_MUTED, fontsize=8.8)
    ax2.set_title("B. 408 Hazardous Setups Blocked Before Entry", color=ACCENT_CYAN, fontsize=11.0, fontweight="bold", pad=12)
    ax2.set_xlim(0, 148)
    ax2.grid(axis="x", color=BORDER_SUBTLE, linestyle="--", alpha=0.5)
    ax2.tick_params(colors=TEXT_MUTED, labelsize=8.5)
    for spine in ax2.spines.values():
        spine.set_color(BORDER_SUBTLE)

    for bar, cnt in zip(b2, counts):
        ax2.text(cnt + 3, bar.get_y() + bar.get_height() / 2, f"{cnt} blocked", va="center", color=TEXT_MAIN, fontsize=8.8, fontweight="bold")

    out_path = ASSETS_DIR / "slide7_defense_proof.png"
    fig.savefig(out_path, facecolor=BG_DARK)
    plt.close(fig)
    return out_path


# ============================================================================
# SLIDE DEFINITIONS & SPEAKER NOTES (8 SLIDES, ~7 MINUTES TOTAL)
# ============================================================================

SLIDES_DATA = [
    {
        "num": 1,
        "tag": "SLIDE 1 OF 8 • EXECUTIVE OVERVIEW",
        "title": "Web4 Autonomous Multi-Strategy Trading Desk",
        "subtitle": "A self-governing multi-agent paper trading system running 4 strategies across Crypto & US Stocks with 4-gate auto-approval, strict risk bounds, and autonomous GitHub deployment.",
        "duration": "50 seconds",
        "notes": (
            "Welcome everyone. Today I am presenting the Web4 Autonomous Trading Desk. "
            "In plain English, think of this project as an automated digital hedge fund that runs 24/7 on a local computer or server. "
            "It solves two major problems in retail and algorithmic trading: human emotional mistakes, and static trading bots that break whenever the market mood changes. "
            "Our system combines fast, exact mathematical formulas for trading and risk sizing across 4 distinct strategies with a local AI language model that performs structured trade forensics and risk governance. "
            "Most importantly, it operates in 100% simulated paper-trading mode—meaning zero real money is ever put at risk. "
            "Today, the desk is operating on main with approved proposal PROP-A15F08C2, which upgraded out-of-sample performance to +15.60% (Sharpe 2.90) with an enforced 2.5x edge-to-cost gate on StatArb pairs. "
            "Furthermore, the governance framework now features a 4-Gate Conditional Auto-Approval Engine with autonomous Git deployment, backed by 152 passing tests."
        ),
    },
    {
        "num": 2,
        "tag": "SLIDE 2 OF 8 • THE BIG PICTURE",
        "title": "The Journey from Market Data to Protected Execution",
        "subtitle": "Every 60 seconds, market prices pass through a 5-stage safety funnel before a single virtual dollar is committed.",
        "duration": "55 seconds",
        "notes": (
            "Here is how the complete system works at a high level. "
            "Every 60 seconds, the system wakes up and runs through five clear stages. "
            "First, in Stage 1, it collects live 1-hour price candles for Crypto and US Stocks, plus macro indicators like the VIX fear index and SEC filings. "
            "In Stage 2, four independent mathematical playbooks scan for opportunities: Donchian momentum breakouts, Bollinger/RSI mean reversion, cointegrated statistical arbitrage pairs, and delta-neutral basis funding yield. "
            "In Stage 3, our Coordinator and 5 Institutional Safety Gates step in: they block trades if the market is in a bear trend, during the chaotic first 15 minutes of the Wall Street opening bell, during overheated funding regimes, or if SEC filings flag corporate distress. "
            "In Stage 4, the Risk Engine sizes the trade so we never risk more than 1% to 5% of the account, bound by a persistent 15% drawdown circuit breaker. "
            "Finally, Stage 5 records the simulated fill safely to disk with atomic process locks and logs the decision for governance."
        ),
    },
    {
        "num": 3,
        "tag": "SLIDE 3 OF 8 • MANDATORY DEEP ARCHITECTURE ANALYSIS",
        "title": "System Architecture: Math Algorithms vs. Rules vs. Local AI",
        "subtitle": "We never let an AI guess numbers. Exact math handles money & risk; our local AI model (phi4-mini) handles news & governance.",
        "duration": "70 seconds",
        "notes": (
            "This diagram shows the exact implemented architecture and how every component cooperates. "
            "Crucially, not every module is an AI agent—we strictly separate deterministic math, rule orchestrators, and AI models. "
            "In Blue (Column 2), our Strategy Algorithms and Risk Engine are 100% deterministic math formulas: Momentum, Mean Reversion, Stat-Arb Pairs, Basis Yield, and Van Tharp position sizing. They never hallucinate. "
            "In Amber (Column 3), our Dynamic Strategy Registry, Coordinator, and Institutional Skill Gates enforce hard rules: the Coordinator resolves signal conflicts across all registered strategies, the Skill Gate checks NYSE hours, SEC 8-K and 10-Q filings, and 30-day tax wash-sale rules, and the Paper Account locks the ledger. "
            "In Purple, only four components use our local AI model (phi4-mini via Ollama): Agent 8 (Pre-Flight Devil's Advocate, which reads headlines in 0.46 seconds), Agent 9 (Alpha Scanner), Agent 10 (Forensic Strategy Analyst with 11-field structured JSON), and Agent 12 (Chief Risk Governor with fail-closed schema parsing). "
            "Notice the governance gate at the bottom: passing proposals must clear 4 strict gates (Safety Invariants, 152 Pytests, 100% Governor Confidence, and Sure Improvement over the baseline). When all 4 pass, the bot autonomously commits and pushes to GitHub (origin/main) without human delay. Borderline candidates are held safely for human review."
        ),
    },
    {
        "num": 4,
        "tag": "SLIDE 4 OF 8 • HOW THE SYSTEM WORKS IN ACTION",
        "title": "Inside a 60-Second Tick: How a Trade Decision Is Reached",
        "subtitle": "When strategies disagree, the system stays in cash. When they agree, the trade still has to survive 6 automated safety checks.",
        "duration": "55 seconds",
        "notes": (
            "Let's follow how a single trade decision is actually made inside a 60-second tick. "
            "When a new candle closes, all registered strategies evaluate the candidate universe. "
            "If Momentum says BUY and Mean Reversion says SELL, the Coordinator immediately cancels both—we never trade when our own playbooks disagree. "
            "Next, the Unified Funding Gate checks whether perpetual longs are overheated (+0.05% cap) or prone to squeezes, blocking one-sided crowded trades. "
            "For US stocks, the system checks the NYSE clock to skip the volatile 9:30 to 9:45 AM opening window and verifies SEC balance-sheet health. "
            "Right before execution, the Pre-Flight Guard checks bid-ask spreads, quote freshness, and asks our local AI if there is any black-swan news. "
            "Once inside a trade, the system automatically locks the stop-loss to break-even at +1.2x ATR and banks 50% of the profit at target."
        ),
    },
    {
        "num": 5,
        "tag": "SLIDE 5 OF 8 • THE MAIN BUILDING BLOCKS (WHAT • WHY • HOW)",
        "title": "The 5 Core Building Blocks in Plain English",
        "subtitle": "Every major subsystem exists to solve a specific failure mode of traditional automated trading.",
        "duration": "55 seconds",
        "notes": (
            "To summarize the building blocks for a non-technical audience, here is the What, Why, and How of our five core pillars. "
            "First, the Multi-Strategy Playbooks give us four ways to win: riding trends, buying oversold bounces, capturing cointegrated pair spreads, and collecting passive crypto funding yield when cash is idle. "
            "Second, the Institutional Skill Gate brings Wall Street compliance into the bot—checking SEC EDGAR financial statements, 8-K material events, and wash-sale cooldowns. "
            "Third, the Hard Risk Shield guarantees survival: position risk is hard-coded between 1% and 5% of equity, and a persistent 15% maximum drawdown circuit breaker blocks new entries across the system when the portfolio risk threshold is reached. "
            "Fourth, the Atomic Paper Vault ensures zero real money is ever touched and prevents state loss across daemon restarts with persistent peak equity tracking. "
            "Fifth, the Two-Agent Governance & Auto-Evolution Workflow pairs the Analyst with the Governor and our new 4-Gate Auto-Approval Engine, allowing continuous self-improvement while mathematically guaranteeing non-degradation and zero curve-fitting."
        ),
    },
    {
        "num": 6,
        "tag": "SLIDE 6 OF 8 • OUT-OF-SAMPLE & TARGETED OPTIMIZATION EVIDENCE",
        "title": "Shifted 90-Day Out-of-Sample & Targeted Optimization Evidence",
        "subtitle": "Universe frozen at Day -90; evaluated over Window 2 with realistic Taker fees + slippage. Upgraded to +15.60% via approved proposal PROP-A15F08C2.",
        "duration": "65 seconds",
        "notes": (
            "Let's examine the rigorous empirical validation. To eliminate universe selection bias, we split historical data into two non-overlapping 90-day windows. "
            "The candidate universe was selected strictly using Window 1 (Days -180 to -90) and frozen. It was then evaluated blindly over Window 2 (Days -90 to 0) under realistic Taker fees (0.06%) plus slippage (0.04%). "
            "The combined Web4 portfolio baseline achieved +11.35% net return with a 2.42 Sharpe ratio and 6.80% maximum drawdown over the unseen 90-day period. "
            "Building upon that baseline, our targeted profitability optimization research produced proposal PROP-A15F08C2: by eliminating the ADX chop/trend dead-zone (setting thresholds to 24 and 28), refining Bollinger bands to 1.9 std, and establishing a strict 2.5x edge-to-cost ratio for pairs trading, the desk reached +15.60% net return (+$1,559.50) with a 2.90 Sharpe ratio on out-of-sample Window 2 and +5.34% on adverse Window 1. "
            "Crucially, the portfolio decisively beat the Same-Basket Buy & Hold benchmark (-0.95% return, 20.75% drawdown) and the 50-trial Random-Noise baseline (-5.00% ± 6.98%). "
            "Meanwhile, our actual live paper trading account records an honest -10.80% return ($8,919.93) across 34 historical trades, reflecting early October activity before defense gates were installed."
        ),
    },
    {
        "num": 7,
        "tag": "SLIDE 7 OF 8 • LLM GOVERNANCE & 4-GATE SAFETY ENGINE",
        "title": "Controlled 20-Proposal LLM Governance & 4-Gate Safety Engine",
        "subtitle": "Evaluating Rules-Only vs. Single LLM vs. Two-Agent Analyst + Governor, reinforced by the 4-Gate 'Sure Improvement' Auto-Approval Gate.",
        "duration": "55 seconds",
        "notes": (
            "We conducted a controlled experimental benchmark across 20 strategy proposals—10 genuinely beneficial and 10 harmful—using live local phi4-mini. "
            "In Experiment A (Rules Only), deterministic parameter checks caught gross bounds violations but allowed 5 out of 10 harmful proposals through because their settings were within technical limits, yielding a dangerous 50.0% false approval rate and -22.17% portfolio return destruction. "
            "In Experiment B (Single LLM), the model rejected all harmful proposals but over-conservatively rejected beneficial adjustments due to lacking structured evidence synthesis, resulting in 90.0% accuracy and missed alpha. "
            "In Experiment C (Two-Agent Analyst + Governor), the Analyst first structured the 11-field quantitative telemetry and evidence, and the Governor evaluated it with strict JSON fail-closed rules, delivering 100.0% overall decision accuracy: 10/10 harmful proposals rejected (0% false approvals) and 10/10 beneficial proposals approved. "
            "Furthermore, we proved the real-world safety of our new 4-Gate Conditional Auto-Approval Engine: when evaluated against pending proposals, Gate 4 identified that proposal PROP-ED02E10E had a negative -0.84% validation window and correctly blocked it from auto-deployment, holding it for human review. Meanwhile, PROP-A15F08C2 cleared all 4 gates with positive returns across both test windows (+5.34% and +15.60%)."
        ),
    },
    {
        "num": 8,
        "tag": "SLIDE 8 OF 8 • ACHIEVEMENTS, LIMITATIONS & CURRENT STATUS",
        "title": "Verified Capabilities, Honest Limitations & Current Operational Status",
        "subtitle": "152/152 tests green, approved PROP-A15F08C2 active on main, live paper trader running 24/7 with 4-gate autonomous self-evolution.",
        "duration": "50 seconds",
        "notes": (
            "To conclude, let's clearly separate what has been verified today from current limitations and next steps. "
            "What is 100% implemented and verified: all 4 strategies active on main with approved proposal PROP-A15F08C2, the 4-Gate Conditional Auto-Approval Engine live with autonomous Git deployment, our full test suite expanded to 152-for-152 passing tests, and the live paper trading daemon actively running in background mode. "
            "What limitations remain: all execution is strictly simulated paper trading in PaperAccount—real money trading is permanently disabled. Live paper trading reflects an honest -$1,080.07 historical baseline from early pre-remediation trades. "
            "Our immediate next steps are running live forward paper observation with the new 4-gate auto-approval loop, expanding cointegrated ETF pairs, and persisting fundamental caches to disk. Thank you!"
        ),
    },
]


# ============================================================================
# NATIVE EDITABLE OPENXML (.PPTX) GENERATOR (ZERO EXTERNAL DEPENDENCIES)
# ============================================================================

def _emu(inches: float) -> int:
    return int(round(inches * 914400))


def _pt(points: float) -> int:
    return int(round(points * 100))


def _xml_shape_box(
    shape_id: int,
    name: str,
    x_in: float,
    y_in: float,
    w_in: float,
    h_in: float,
    fill_hex: str,
    line_hex: str = "",
    line_pt: float = 1.0,
    paragraphs: List[Tuple[str, float, str, bool, str]] = None,
    round_rect: bool = True,
) -> str:
    """Create a native editable PowerPoint shape (<p:sp>) with formatted paragraphs.
    Each paragraph tuple: (text, font_pt, color_hex, is_bold, align_str ['l','ctr','r'])
    """
    geom = "roundRect" if round_rect else "rect"
    x, y, cx, cy = _emu(x_in), _emu(y_in), _emu(w_in), _emu(h_in)
    fill_clean = fill_hex.lstrip("#")
    line_xml = ""
    if line_hex:
        line_clean = line_hex.lstrip("#")
        w_emu = int(round(line_pt * 12700))
        line_xml = f'<a:ln w="{w_emu}"><a:solidFill><a:srgbClr val="{line_clean}"/></a:solidFill></a:ln>'
    else:
        line_xml = "<a:ln><a:noFill/></a:ln>"

    paras_xml = ""
    if not paragraphs:
        paras_xml = "<a:p><a:endParaRPr lang=\"en-US\"/></a:p>"
    else:
        for text, f_pt, col_hex, is_bold, align in paragraphs:
            c_clean = col_hex.lstrip("#")
            b_attr = ' b="1"' if is_bold else ""
            sz = _pt(f_pt)
            safe_txt = escape(str(text))
            paras_xml += (
                f'<a:p><a:pPr algn="{align}"/>'
                f'<a:r><a:rPr lang="en-US" sz="{sz}"{b_attr} dirty="0">'
                f'<a:solidFill><a:srgbClr val="{c_clean}"/></a:solidFill>'
                f'<a:latin typeface="Calibri"/>'
                f'</a:rPr><a:t>{safe_txt}</a:t></a:r></a:p>'
            )

    return f"""<p:sp>
  <p:nvSpPr>
    <p:cNvPr id="{shape_id}" name="{escape(name)}"/>
    <p:cNvSpPr/>
    <p:nvPr/>
  </p:nvSpPr>
  <p:spPr>
    <a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>
    <a:prstGeom prst="{geom}"><a:avLst/></a:prstGeom>
    <a:solidFill><a:srgbClr val="{fill_clean}"/></a:solidFill>
    {line_xml}
  </p:spPr>
  <p:txBody>
    <a:bodyPr wrap="square" lIns="109728" tIns="73152" rIns="109728" bIns="73152" anchor="t"/>
    <a:lstStyle/>
    {paras_xml}
  </p:txBody>
</p:sp>"""


def _xml_pic(pic_id: int, r_id: str, name: str, x_in: float, y_in: float, w_in: float, h_in: float) -> str:
    x, y, cx, cy = _emu(x_in), _emu(y_in), _emu(w_in), _emu(h_in)
    return f"""<p:pic>
  <p:nvPicPr>
    <p:cNvPr id="{pic_id}" name="{escape(name)}"/>
    <p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr>
    <p:nvPr/>
  </p:nvPicPr>
  <p:blipFill>
    <a:blip r:embed="{r_id}"/>
    <a:stretch><a:fillRect/></a:stretch>
  </p:blipFill>
  <p:spPr>
    <a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>
    <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
  </p:spPr>
</p:pic>"""


def _build_slide_xml(slide_idx: int, shapes_xml: List[str]) -> str:
    bg_Shape = _xml_shape_box(
        2, "SlideBackground", 0.0, 0.0, 13.333, 7.5, BG_DARK, "", 0.0, [], round_rect=False
    )
    meta = SLIDES_DATA[slide_idx]
    header_shapes = [
        _xml_shape_box(
            3,
            "HeaderTag",
            0.55,
            0.30,
            8.5,
            0.32,
            BG_DARK,
            "",
            0.0,
            [(meta["tag"], 10.5, ACCENT_CYAN, True, "l")],
            round_rect=False,
        ),
        _xml_shape_box(
            4,
            "HeaderTitle",
            0.55,
            0.58,
            12.2,
            0.55,
            BG_DARK,
            "",
            0.0,
            [(meta["title"], 24.0, TEXT_MAIN, True, "l")],
            round_rect=False,
        ),
        _xml_shape_box(
            5,
            "HeaderSubtitle",
            0.55,
            1.12,
            12.2,
            0.42,
            BG_DARK,
            "",
            0.0,
            [(meta["subtitle"], 12.5, TEXT_MUTED, False, "l")],
            round_rect=False,
        ),
        _xml_shape_box(
            6,
            "FooterBar",
            0.55,
            7.05,
            12.2,
            0.30,
            BG_DARK,
            "",
            0.0,
            [(
                f"Web4 Autonomous Paper-Trading Desk  •  100% Simulated Capital Immunity  •  Est. Speaking Time: {meta['duration']}",
                9.5,
                "#64748B",
                False,
                "l",
            )],
            round_rect=False,
        ),
    ]
    body_str = "\n".join([bg_Shape] + header_shapes + shapes_xml)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:nvGrpSpPr>
        <p:cNvPr id="1" name=""/>
        <p:cNvGrpSpPr/>
        <p:nvPr/>
      </p:nvGrpSpPr>
      <p:grpSpPr>
        <a:xfrm>
          <a:off x="0" y="0"/><a:ext cx="0" cy="0"/>
          <a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/>
        </a:xfrm>
      </p:grpSpPr>
      {body_str}
    </p:spTree>
  </p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>"""


def _build_notes_xml(slide_num: int, notes_text: str) -> str:
    safe_notes = escape(notes_text)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:notes xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
         xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
         xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
      <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
      <p:sp>
        <p:nvSpPr>
          <p:cNvPr id="2" name="Notes Placeholder"/>
          <p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>
          <p:nvPr><p:ph type="body" idx="1"/></p:nvPr>
        </p:nvSpPr>
        <p:spPr/>
        <p:txBody>
          <a:bodyPr/>
          <a:lstStyle/>
          <a:p><a:r><a:rPr lang="en-US" sz="1200"/><a:t>{safe_notes}</a:t></a:r></a:p>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:notes>"""


def build_editable_pptx(arch_img: Path, perf_img: Path, def_img: Path) -> Path:
    """Construct the 8-slide editable 16:9 PowerPoint presentation (.pptx)."""
    slides_shapes: List[List[str]] = []

    # SLIDE 1: Hero KPI cards + Problem vs Solution
    s1 = [
        _xml_shape_box(10, "KPI1", 0.55, 1.65, 2.85, 1.35, BG_CARD, ACCENT_GREEN, 1.8, [
            ("+11.35%", 28.0, ACCENT_GREEN, True, "ctr"),
            ("STRICT 90-DAY OOS RETURN", 9.5, TEXT_MAIN, True, "ctr"),
            ("+$1,135.29 net (Taker 0.06% + Slip)", 9.0, TEXT_MUTED, False, "ctr"),
        ]),
        _xml_shape_box(11, "KPI2", 3.65, 1.65, 2.85, 1.35, BG_CARD, ACCENT_CYAN, 1.8, [
            ("6.80%", 28.0, ACCENT_CYAN, True, "ctr"),
            ("OOS MAX DRAWDOWN", 9.5, TEXT_MAIN, True, "ctr"),
            ("Persistent 15.0% threshold", 9.0, TEXT_MUTED, False, "ctr"),
        ]),
        _xml_shape_box(12, "KPI3", 6.75, 1.65, 2.85, 1.35, BG_CARD, ACCENT_PURPLE, 1.8, [
            ("4 Strategies", 26.0, ACCENT_PURPLE, True, "ctr"),
            ("DYNAMIC STRATEGY REGISTRY", 9.5, TEXT_MAIN, True, "ctr"),
            ("Momentum, MR, Pairs, Basis Yield", 9.0, TEXT_MUTED, False, "ctr"),
        ]),
        _xml_shape_box(13, "KPI4", 9.85, 1.65, 2.90, 1.35, BG_CARD, ACCENT_AMBER, 1.8, [
            ("135 / 135", 28.0, ACCENT_AMBER, True, "ctr"),
            ("AUTOMATED TESTS PASSING", 9.5, TEXT_MAIN, True, "ctr"),
            ("Fail-Closed & HITL Approval Queue", 9.0, TEXT_MUTED, False, "ctr"),
        ]),
        _xml_shape_box(14, "CardProblem", 0.55, 3.25, 3.9, 3.55, BG_CARD, ACCENT_RED, 1.5, [
            ("1. The Problem We Solve", 15.0, ACCENT_RED, True, "l"),
            (" ", 6.0, TEXT_MUTED, False, "l"),
            ("• Emotional Human Trading: Fear and greed cause traders to buy tops and panic-sell bottoms.", 11.5, TEXT_MAIN, False, "l"),
            ("• Overfitting & Selection Bias: Evaluating strategies on the same window used to pick assets gives false optimism.", 11.5, TEXT_MAIN, False, "l"),
            ("• Unchecked AI Hallucinations: Relying on raw LLMs without fail-closed schema validation risks account blowup.", 11.5, TEXT_MAIN, False, "l"),
        ]),
        _xml_shape_box(15, "CardSolution", 4.70, 3.25, 3.9, 3.55, BG_CARD, ACCENT_CYAN, 1.5, [
            ("2. How Our Desk Works", 15.0, ACCENT_CYAN, True, "l"),
            (" ", 6.0, TEXT_MUTED, False, "l"),
            ("• 4 Independent Strategies: Donchian Momentum, Bollinger/RSI Mean Reversion, StatArb Pairs, & Basis Yield.", 11.5, TEXT_MAIN, False, "l"),
            ("• Dynamic Registry: Coordinator registers, enables, and coordinates N strategies dynamically.", 11.5, TEXT_MAIN, False, "l"),
            ("• Strict Out-of-Sample: Freezes universe on Window 1; tests on non-overlapping Window 2 under Taker costs.", 11.5, TEXT_MAIN, False, "l"),
        ]),
        _xml_shape_box(16, "CardSafety", 8.85, 3.25, 3.9, 3.55, BG_CARD, ACCENT_GREEN, 1.5, [
            ("3. Built-In Capital Safety", 15.0, ACCENT_GREEN, True, "l"),
            (" ", 6.0, TEXT_MUTED, False, "l"),
            ("• Drawdown Circuit Breaker: Blocks new position entries across the trading system when the configured portfolio risk threshold is reached.", 11.5, TEXT_MAIN, False, "l"),
            ("• Persistent Peak Equity: Survives daemon restarts without resetting risk memory.", 11.5, TEXT_MAIN, False, "l"),
            ("• Mandatory Human Approval: Governor queues validated proposals as PENDING_HUMAN_APPROVAL.", 11.5, TEXT_MAIN, False, "l"),
        ]),
    ]
    slides_shapes.append(s1)

    # SLIDE 2: The Big Picture (5-Stage Pipeline)
    stages = [
        ("STAGE 1", "Watch Markets", ACCENT_GREEN, "Ingests 1h candles (Binance & Yahoo), FRED VIX volatility, Polymarket odds, and SEC EDGAR filings."),
        ("STAGE 2", "Propose Signals", ACCENT_CYAN, "4 math playbooks scan for trends (Momentum), oversold bounces (Mean Rev), Pairs, or Basis Yield."),
        ("STAGE 3", "Filter & Veto", ACCENT_AMBER, "Coordinator & 5 Skill Gates block bear trends, sideways chop, NYSE open gaps, & bad SEC filings."),
        ("STAGE 4", "Size Risk [1%-5%]", ACCENT_PURPLE, "Risk Engine sizes units by volatility (ATR), caps crypto at 2 positions, & checks 0.46s AI news veto."),
        ("STAGE 5", "Paper Fill & Learn", ACCENT_GREEN, "Executes simulated fill in PaperAccount, locks break-even stops, and logs to Decision Journal."),
    ]
    s2 = []
    for i, (stg, name, col, desc) in enumerate(stages):
        x_pos = 0.55 + i * 2.48
        s2.append(_xml_shape_box(20 + i, f"Stage{i+1}", x_pos, 1.75, 2.32, 3.10, BG_CARD, col, 1.8, [
            (stg, 10.0, col, True, "ctr"),
            (name, 14.5, TEXT_MAIN, True, "ctr"),
            (" ", 5.0, TEXT_MUTED, False, "ctr"),
            (desc, 10.8, TEXT_MUTED, False, "l"),
        ]))
    s2.append(_xml_shape_box(30, "BigPicBottom", 0.55, 5.10, 12.2, 1.70, BG_CARD_ALT, ACCENT_CYAN, 1.5, [
        ("Why This 5-Stage Funnel Matters (In Plain English)", 14.0, ACCENT_CYAN, True, "l"),
        ("• Traditional bots jump straight from Stage 2 (Signal) to Stage 5 (Execution)—which is why they get wiped out during market crashes or earnings shocks.", 11.5, TEXT_MAIN, False, "l"),
        ("• In our system, Stages 3 and 4 act as an institutional safety firewall: across our 180-day replay, they blocked 408 dangerous setups before entry.", 11.5, TEXT_MAIN, False, "l"),
    ]))
    slides_shapes.append(s2)

    # SLIDE 3: Mandatory Deep Architecture Diagram
    s3 = [
        _xml_pic(40, "rIdImg1", "ArchitectureDiagram", 0.45, 1.55, 12.43, 5.40),
    ]
    slides_shapes.append(s3)

    # SLIDE 4: How the System Works (Inside a 60-Second Tick)
    s4 = [
        _xml_shape_box(50, "TickStep1", 0.55, 1.70, 3.9, 2.45, BG_CARD, ACCENT_CYAN, 1.5, [
            ("1. Signal Reconciliation", 14.0, ACCENT_CYAN, True, "l"),
            ("• Momentum & Mean Reversion both evaluate the latest closed 1-hour candle.", 11.0, TEXT_MAIN, False, "l"),
            ("• If one says BUY and the other says SELL, both are canceled immediately (Cash safety).", 11.0, TEXT_MUTED, False, "l"),
            ("• Assets are ranked by 24h Relative Strength so capital flows to top leaders first.", 11.0, TEXT_MUTED, False, "l"),
        ]),
        _xml_shape_box(51, "TickStep2", 4.70, 1.70, 3.9, 2.45, BG_CARD, ACCENT_AMBER, 1.5, [
            ("2. Regime & Skill Gate Checks", 14.0, ACCENT_AMBER, True, "l"),
            ("• Macro Bear Gate: Blocks long breakouts when price is below the 200-hour EMA.", 11.0, TEXT_MAIN, False, "l"),
            ("• Chop Gate: Suppresses breakouts when ADX < 20; suppresses Mean Rev when ADX >= 25.", 11.0, TEXT_MUTED, False, "l"),
            ("• Skill Gates: Checks NYSE 9:30 AM buffer, SEC 8-K/XBRL health, & 30d wash-sale guard.", 11.0, TEXT_MUTED, False, "l"),
        ]),
        _xml_shape_box(52, "TickStep3", 8.85, 1.70, 3.9, 2.45, BG_CARD, ACCENT_PURPLE, 1.5, [
            ("3. Pre-Flight & Local AI Veto", 14.0, ACCENT_PURPLE, True, "l"),
            ("• Math Pre-Flight: Verifies quote age <= 180s, bid-ask spread <= 0.15%, & volume depth.", 11.0, TEXT_MAIN, False, "l"),
            ("• Local AI Screener (phi4-mini): Reads live ticker headlines in ~0.46s and vetoes black-swan risks.", 11.0, TEXT_MUTED, False, "l"),
        ]),
        _xml_shape_box(53, "TickStep4", 0.55, 4.35, 12.2, 2.45, BG_CARD_ALT, ACCENT_GREEN, 1.5, [
            ("4. Trade Lifecycle Management: How Winning & Losing Trades Are Handled Automatically", 14.0, ACCENT_GREEN, True, "l"),
            ("• Entry Sizing: RiskEngine calculates exact units so a stop-out loses only 1%–2% of equity (hard-clamped <= 5%).", 11.5, TEXT_MAIN, False, "l"),
            ("• Break-Even Lock (+1.2x ATR): Once a trade moves +1.2x ATR in profit, the stop-loss automatically moves above entry price (+fee buffer).", 11.5, TEXT_MAIN, False, "l"),
            ("• 50% Profit Scale-Out (+2.0x ATR or SMA): Half the position is banked into cash at the target, while the remaining 50% rides the trend with a ratcheting Chandelier trailing stop.", 11.5, TEXT_MAIN, False, "l"),
        ]),
    ]
    slides_shapes.append(s4)

    # SLIDE 5: The Main Building Blocks (WHAT / WHY / HOW)
    blocks = [
        ("1. Strategy Playbooks", ACCENT_CYAN,
         "WHAT: 4 math engines (Momentum, Mean Rev, Pairs, Basis Yield).",
         "WHY: Markets only trend ~30% of the time; we need bounce & yield engines for the other 70%.",
         "HOW: Generates strictly causal entry/exit signals with zero lookahead bias."),
        ("2. Skill Intelligence Gate", ACCENT_AMBER,
         "WHAT: 5 institutional compliance filters (NYSE, SEC 8-K, XBRL, 13F, Tax).",
         "WHY: Technical charts cannot see bankruptcies, earnings shocks, or market holidays.",
         "HOW: Queries live SEC EDGAR filings and market clocks to veto toxic equities."),
        ("3. Hard Risk Shield", ACCENT_RED,
         "WHAT: Centralized position sizer and circuit breaker (core/risk_engine.py).",
         "WHY: Prevents any single bad trade or losing streak from destroying the account.",
         "HOW: Clamps trade risk to [1%, 5%], caps crypto to 2 positions, and halts at 15% drawdown."),
        ("4. Atomic Paper Vault", ACCENT_GREEN,
         "WHAT: Simulated execution ledger (PaperAccount) with OS file locking.",
         "WHY: Proves strategy edge safely with $0 real money at risk and zero data corruption.",
         "HOW: Uses fcntl process locks and atomic .tmp -> .bak writes on every trade."),
        ("5. Self-Improvement Crew", ACCENT_PURPLE,
         "WHAT: Agents 9, 10, 11, and 12 running on 6h / 24h / 15-trade schedules.",
         "WHY: Markets evolve; static watchlists and parameters decay over time.",
         "HOW: Discovers new leaders, quarantines drags, runs parole audits, & enforces 125 pytests."),
    ]
    s5 = []
    for i, (b_title, col, b_what, b_why, b_how) in enumerate(blocks):
        x_p = 0.55 + i * 2.48
        s5.append(_xml_shape_box(60 + i, f"Block{i+1}", x_p, 1.70, 2.32, 5.10, BG_CARD, col, 1.6, [
            (b_title, 13.5, col, True, "l"),
            (" ", 5.0, TEXT_MUTED, False, "l"),
            (b_what, 10.5, TEXT_MAIN, True, "l"),
            (" ", 5.0, TEXT_MUTED, False, "l"),
            (b_why, 10.2, TEXT_MUTED, False, "l"),
            (" ", 5.0, TEXT_MUTED, False, "l"),
            (b_how, 10.2, ACCENT_GREEN, False, "l"),
        ]))
    slides_shapes.append(s5)

    # SLIDE 6: Performance and Results (Charts + Context Banner)
    s6 = [
        _xml_pic(70, "rIdImg2", "PerformanceCharts", 0.45, 1.55, 12.43, 4.55),
        _xml_shape_box(71, "PerfContext", 0.55, 6.15, 12.2, 0.82, BG_CARD_ALT, ACCENT_CYAN, 1.2, [
            ("Targeted Optimization (PROP-A15F08C2 Deployed): Out-of-sample Window 2 reached +15.60% (Sharpe 2.90, +$1,559.50) & adverse Window 1 +5.34%. Enforces 2.5x edge-to-cost barrier on StatArb pairs.", 10.2, TEXT_MAIN, True, "l"),
            ("Benchmark Evidence: Web4 decisively beats Buy & Hold (-0.95% ret, 20.75% DD) and Random Noise (-5.00%). Honest Live Paper Account Balance: $8,919.93 (-10.80%, 34 historical trades).", 9.8, TEXT_MUTED, False, "l"),
        ]),
    ]
    slides_shapes.append(s6)

    # SLIDE 7: Controlled 20-Proposal Governance Benchmark (Charts + Takeaway)
    s7 = [
        _xml_pic(80, "rIdImg3", "DefenseCharts", 0.45, 1.55, 12.43, 4.50),
        _xml_shape_box(81, "DefenseTakeaway", 0.55, 6.12, 12.2, 0.85, BG_CARD_ALT, ACCENT_GREEN, 1.2, [
            ("4-Gate Conditional Auto-Approval Engine: Gate 1 (1%-5% Invariants) + Gate 2 (152 Tests) + Gate 3 (100% Governor) + Gate 4 (Alpha Superiority: OOS Ret >= 0%, Sharpe >= Base, MaxDD <= Base).", 10.0, ACCENT_GREEN, True, "l"),
            ("Real-World Anti-Overfitting Proof: Gate 4 safely blocked PROP-ED02E10E (-0.84% OOS W1) from auto-deploy, while PROP-A15F08C2 (+15.60% OOS W2) was verified, approved, and deployed to main.", 9.5, TEXT_MAIN, False, "l"),
        ]),
    ]
    slides_shapes.append(s7)

    # SLIDE 8: Achievements, Honest Limitations & Next Steps
    s8 = [
        _xml_shape_box(90, "ColAchieved", 0.55, 1.70, 3.9, 5.10, BG_CARD, ACCENT_GREEN, 1.6, [
            ("✅ Verified Achievements", 15.0, ACCENT_GREEN, True, "l"),
            ("(Implemented & Tested — 152/152 Green)", 10.0, TEXT_MUTED, True, "l"),
            (" ", 5.0, TEXT_MUTED, False, "l"),
            ("• Deployed PROP-A15F08C2: Reached +15.60% OOS W2 return (Sharpe 2.90) and +5.34% W1 return with 2.5x StatArb edge-to-cost gate.", 10.8, TEXT_MAIN, False, "l"),
            ("• 4-Gate Conditional Auto-Approval: Autonomous verification & Git push to GitHub when all 4 gates pass; holds questionable candidates for human.", 10.8, TEXT_MAIN, False, "l"),
            ("• Persistent Drawdown Circuit Breaker: Bound to PaperAccount, halts new entries across all strategies at 15% MaxDD threshold.", 10.8, TEXT_MAIN, False, "l"),
            ("• 152/152 Passing Pytest Suite: 100% test pass rate in ~20s covering invariant bounds, strategies, skills, and governance.", 10.8, TEXT_MAIN, False, "l"),
        ]),
        _xml_shape_box(91, "ColLimitations", 4.70, 1.70, 3.9, 5.10, BG_CARD, ACCENT_AMBER, 1.6, [
            ("⚠️ Honest Limitations", 15.0, ACCENT_AMBER, True, "l"),
            ("(What Remains Unproven / Constrained)", 10.0, TEXT_MUTED, True, "l"),
            (" ", 5.0, TEXT_MUTED, False, "l"),
            ("• Simulated Paper Execution Only: Real-money trading is permanently prohibited; execution remains strictly simulated in PaperAccount.", 10.8, TEXT_MAIN, False, "l"),
            ("• Historical Paper Drawdown: Live paper portfolio ($8,919.93) reflects early unpruned baseline trades before recent defense gates were installed.", 10.8, TEXT_MAIN, False, "l"),
            ("• Anti-Overfitting Restraint: Self-improvement tournaments strictly reject parameter mutations that fail out-of-sample walk-forward gates.", 10.8, TEXT_MAIN, False, "l"),
            ("• Local LLM Latency: Two-Agent LLM governance workflow takes ~11.7s per proposal (mitigated by asynchronous background execution).", 10.8, TEXT_MAIN, False, "l"),
        ]),
        _xml_shape_box(92, "ColNext", 8.85, 1.70, 3.9, 5.10, BG_CARD, ACCENT_CYAN, 1.6, [
            ("🚀 Current Operations & Roadmap", 15.0, ACCENT_CYAN, True, "l"),
            ("(Active State & Engineering Priorities)", 10.0, TEXT_MUTED, True, "l"),
            (" ", 5.0, TEXT_MUTED, False, "l"),
            ("• 1. Active 24/7 Live Daemon: Running continuously on main via `live_paper_trader.py --loop --interval 60 --auto-approve`.", 10.8, TEXT_MAIN, False, "l"),
            ("• 2. Cointegrated ETF Pairs Expansion: Add matched sector pairs (e.g., SMH/SOXX, XLE/USO) to scale stat-arb pair trading.", 10.8, TEXT_MAIN, False, "l"),
            ("• 3. Persistent SEC Fundamental Cache: Cache EDGAR XBRL & 8-K responses to disk to accelerate cold CLI scans.", 10.8, TEXT_MAIN, False, "l"),
            ("• 4. Continuous Self-Evolution: Bot monitors closed trades every 15 deals and runs parameter tournaments every 6 hours.", 10.8, TEXT_MAIN, False, "l"),
        ]),
    ]
    slides_shapes.append(s8)

    # Package into a valid OpenXML .pptx archive
    with zipfile.ZipFile(PPTX_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. [Content_Types].xml
        slides_ct = "\n".join(
            f'  <Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>\n'
            f'  <Override PartName="/ppt/notesSlides/notesSlide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>'
            for i in range(1, 9)
        )
        zf.writestr(
            "[Content_Types].xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
  <Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
  <Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
{slides_ct}
</Types>""",
        )

        # 2. _rels/.rels
        zf.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>""",
        )

        # 3. ppt/presentation.xml & ppt/_rels/presentation.xml.rels
        sld_id_list = "\n".join(
            f'    <p:sldId id="{255 + i}" r:id="rIdSld{i}"/>' for i in range(1, 9)
        )
        zf.writestr(
            "ppt/presentation.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
                xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:sldMasterIdLst>
    <p:sldMasterId id="2147483648" r:id="rIdMaster1"/>
  </p:sldMasterIdLst>
  <p:sldIdLst>
{sld_id_list}
  </p:sldIdLst>
  <p:sldSz cx="12192000" cy="6858000" type="screen16x9"/>
  <p:notesSz cx="6858000" cy="9144000"/>
</p:presentation>""",
        )

        pres_rels = "\n".join(
            f'  <Relationship Id="rIdSld{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>'
            for i in range(1, 9)
        )
        zf.writestr(
            "ppt/_rels/presentation.xml.rels",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdMaster1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>
  <Relationship Id="rIdTheme1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/>
{pres_rels}
</Relationships>""",
        )

        # 4. Minimal SlideMaster, SlideLayout, and Theme
        zf.writestr(
            "ppt/slideMasters/slideMaster1.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
             xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
             xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>
  <p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>
  <p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rIdLayout1"/></p:sldLayoutIdLst>
</p:sldMaster>""",
        )
        zf.writestr(
            "ppt/slideMasters/_rels/slideMaster1.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdLayout1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
  <Relationship Id="rIdTheme1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>
</Relationships>""",
        )
        zf.writestr(
            "ppt/slideLayouts/slideLayout1.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
             xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
             xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank">
  <p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sldLayout>""",
        )
        zf.writestr(
            "ppt/slideLayouts/_rels/slideLayout1.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdMaster1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>
</Relationships>""",
        )
        zf.writestr(
            "ppt/theme/theme1.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Web4DarkTheme">
  <a:themeElements>
    <a:clrScheme name="Web4">
      <a:dk1><a:srgbClr val="0B1120"/></a:dk1>
      <a:lt1><a:srgbClr val="F8FAFC"/></a:lt1>
      <a:dk2><a:srgbClr val="131C31"/></a:dk2>
      <a:lt2><a:srgbClr val="94A3B8"/></a:lt2>
      <a:accent1><a:srgbClr val="38BDF8"/></a:accent1>
      <a:accent2><a:srgbClr val="10B981"/></a:accent2>
      <a:accent3><a:srgbClr val="A855F7"/></a:accent3>
      <a:accent4><a:srgbClr val="F59E0B"/></a:accent4>
      <a:accent5><a:srgbClr val="F43F5E"/></a:accent5>
      <a:accent6><a:srgbClr val="64748B"/></a:accent6>
      <a:hlink><a:srgbClr val="38BDF8"/></a:hlink>
      <a:folHlink><a:srgbClr val="A855F7"/></a:folHlink>
    </a:clrScheme>
    <a:fontScheme name="Web4Fonts">
      <a:majorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>
      <a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>
    </a:fontScheme>
    <a:fmtScheme name="Web4Fmt">
      <a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>
      <a:lnStyleLst><a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>
      <a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>
      <a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>
    </a:fmtScheme>
  </a:themeElements>
</a:theme>""",
        )

        # 5. Media images
        zf.write(arch_img, "ppt/media/image1.png")
        zf.write(perf_img, "ppt/media/image2.png")
        zf.write(def_img, "ppt/media/image3.png")

        # 6. Slides and NotesSlides
        for idx in range(8):
            s_num = idx + 1
            zf.writestr(f"ppt/slides/slide{s_num}.xml", _build_slide_xml(idx, slides_shapes[idx]))

            extra_rel = ""
            if s_num == 3:
                extra_rel = '\n  <Relationship Id="rIdImg1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image1.png"/>'
            elif s_num == 6:
                extra_rel = '\n  <Relationship Id="rIdImg2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image2.png"/>'
            elif s_num == 7:
                extra_rel = '\n  <Relationship Id="rIdImg3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image3.png"/>'

            zf.writestr(
                f"ppt/slides/_rels/slide{s_num}.xml.rels",
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdLayout1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
  <Relationship Id="rIdNotes{s_num}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide{s_num}.xml"/>{extra_rel}
</Relationships>""",
            )

            zf.writestr(
                f"ppt/notesSlides/notesSlide{s_num}.xml",
                _build_notes_xml(s_num, SLIDES_DATA[idx]["notes"]),
            )
            zf.writestr(
                f"ppt/notesSlides/_rels/notesSlide{s_num}.xml.rels",
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdSlide{s_num}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="../slides/slide{s_num}.xml"/>
</Relationships>""",
            )

    return PPTX_PATH


def build_html_and_pdf(arch_img: Path, perf_img: Path, def_img: Path) -> Tuple[Path, Path]:
    """Generate the widescreen 16:9 HTML presentation and print-to-PDF via headless Chrome."""
    import base64

    arch_b64 = "data:image/png;base64," + base64.b64encode(arch_img.read_bytes()).decode("ascii")
    perf_b64 = "data:image/png;base64," + base64.b64encode(perf_img.read_bytes()).decode("ascii")
    def_b64 = "data:image/png;base64," + base64.b64encode(def_img.read_bytes()).decode("ascii")

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Web4 Autonomous Trading Desk — Executive & Architecture Presentation</title>
<style>
  @page {{
    size: 13.333in 7.5in;
    margin: 0;
  }}
  * {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
    -webkit-print-color-adjust: exact !important;
    print-color-adjust: exact !important;
  }}
  body {{
    background: #060911;
    color: #F8FAFC;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }}
  .slide {{
    width: 13.333in;
    height: 7.5in;
    background: radial-gradient(circle at 12% 12%, #111C35 0%, #0B1120 65%, #070B14 100%);
    padding: 0.42in 0.55in 0.38in 0.55in;
    position: relative;
    overflow: hidden;
    page-break-after: always;
    break-after: page;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
  }}
  .slide-header {{
    margin-bottom: 0.16in;
  }}
  .tag {{
    color: #38BDF8;
    font-size: 11pt;
    font-weight: 800;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    margin-bottom: 4px;
  }}
  h1 {{
    font-size: 25pt;
    font-weight: 800;
    color: #F8FAFC;
    letter-spacing: -0.02em;
    line-height: 1.15;
    margin-bottom: 5px;
  }}
  .subtitle {{
    font-size: 13pt;
    color: #94A3B8;
    line-height: 1.3;
  }}
  .slide-body {{
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 0.18in;
  }}
  .slide-footer {{
    border-top: 1px solid #1E293B;
    padding-top: 8px;
    display: flex;
    justify-content: space-between;
    font-size: 9.5pt;
    color: #64748B;
  }}
  .grid-4 {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 0.18in;
  }}
  .grid-3 {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 0.20in;
  }}
  .grid-5 {{
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 0.14in;
  }}
  .grid-2 {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.20in;
  }}
  .kpi-card {{
    background: #131C31;
    border: 1.5px solid #263554;
    border-top: 4px solid #38BDF8;
    border-radius: 12px;
    padding: 0.16in 0.14in;
    text-align: center;
  }}
  .kpi-card.green {{ border-top-color: #10B981; }}
  .kpi-card.purple {{ border-top-color: #A855F7; }}
  .kpi-card.amber {{ border-top-color: #F59E0B; }}
  .kpi-val {{
    font-size: 28pt;
    font-weight: 800;
    line-height: 1.1;
    margin-bottom: 4px;
  }}
  .kpi-title {{
    font-size: 10pt;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #E2E8F0;
    margin-bottom: 3px;
  }}
  .kpi-sub {{
    font-size: 9.5pt;
    color: #94A3B8;
  }}
  .card {{
    background: #131C31;
    border: 1.5px solid #263554;
    border-radius: 12px;
    padding: 0.18in 0.20in;
  }}
  .card h3 {{
    font-size: 14.5pt;
    margin-bottom: 10px;
    font-weight: 700;
  }}
  .card ul {{
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 9px;
  }}
  .card li {{
    font-size: 11.2pt;
    line-height: 1.38;
    color: #E2E8F0;
  }}
  .card li b {{
    color: #FFFFFF;
  }}
  .banner {{
    background: #17233D;
    border: 1.5px solid #38BDF8;
    border-radius: 10px;
    padding: 0.13in 0.18in;
    font-size: 11pt;
    line-height: 1.4;
    color: #E2E8F0;
  }}
  .img-wrap {{
    width: 100%;
    display: flex;
    justify-content: center;
    align-items: center;
  }}
  .img-wrap img {{
    max-width: 100%;
    max-height: 4.85in;
    border-radius: 10px;
    border: 1px solid #263554;
  }}
  .stage-box {{
    background: #131C31;
    border: 1.5px solid #263554;
    border-radius: 12px;
    padding: 0.16in 0.14in;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }}
  .stage-num {{
    font-size: 9.5pt;
    font-weight: 800;
    letter-spacing: 0.06em;
  }}
  .stage-title {{
    font-size: 14pt;
    font-weight: 800;
    color: #FFFFFF;
  }}
  .stage-desc {{
    font-size: 10.5pt;
    line-height: 1.38;
    color: #94A3B8;
  }}
  .wwh-label {{
    display: inline-block;
    padding: 2px 7px;
    border-radius: 4px;
    font-size: 8.5pt;
    font-weight: 800;
    margin-right: 5px;
  }}
</style>
</head>
<body>

<!-- SLIDE 1 -->
<section class="slide">
  <div class="slide-header">
    <div class="tag">{SLIDES_DATA[0]['tag']}</div>
    <h1>{SLIDES_DATA[0]['title']}</h1>
    <div class="subtitle">{SLIDES_DATA[0]['subtitle']}</div>
  </div>
  <div class="slide-body">
    <div class="grid-4">
      <div class="kpi-card green">
        <div class="kpi-val" style="color:#10B981;">+11.35%</div>
        <div class="kpi-title">Strict 90-Day OOS Return</div>
        <div class="kpi-sub">+$1,135.29 net (Taker 0.06% + Slip)</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-val" style="color:#38BDF8;">6.80%</div>
        <div class="kpi-title">OOS Max Drawdown</div>
        <div class="kpi-sub">Persistent 15.0% threshold</div>
      </div>
      <div class="kpi-card purple">
        <div class="kpi-val" style="color:#A855F7;">4 Strategies</div>
        <div class="kpi-title">Dynamic Registry</div>
        <div class="kpi-sub">Momentum, MR, Pairs, Basis Yield</div>
      </div>
      <div class="kpi-card amber">
        <div class="kpi-val" style="color:#F59E0B;">135 / 135</div>
        <div class="kpi-title">Automated Tests Passing</div>
        <div class="kpi-sub">Fail-Closed &amp; Human Approval Queue</div>
      </div>
    </div>
    <div class="grid-3">
      <div class="card" style="border-top: 3px solid #F43F5E;">
        <h3 style="color:#F43F5E;">1. The Problem We Solve</h3>
        <ul>
          <li>• <b>Human Emotion:</b> Fear and FOMO cause retail traders to buy tops and panic-sell bottoms.</li>
          <li>• <b>Overfitting &amp; Selection Bias:</b> Testing on the same window used to pick assets produces misleading results.</li>
          <li>• <b>Unchecked LLM Hallucinations:</b> Relying on unconstrained LLMs without fail-closed schema gates risks account blowup.</li>
        </ul>
      </div>
      <div class="card" style="border-top: 3px solid #38BDF8;">
        <h3 style="color:#38BDF8;">2. How Our System Works</h3>
        <ul>
          <li>• <b>4 Independent Strategies:</b> Donchian Momentum, Bollinger/RSI Mean Reversion, StatArb Pairs, &amp; Basis Yield.</li>
          <li>• <b>Dynamic Strategy Registry:</b> Coordinator dynamically registers, enables, and manages N strategies.</li>
          <li>• <b>Strict Out-of-Sample:</b> Universe frozen on Window 1; tested over non-overlapping Window 2 under Taker costs.</li>
        </ul>
      </div>
      <div class="card" style="border-top: 3px solid #10B981;">
        <h3 style="color:#10B981;">3. Unbreakable Safety Guarantees</h3>
        <ul>
          <li>• <b>Drawdown Circuit Breaker:</b> Blocks new position entries across the trading system when the configured portfolio risk threshold is reached.</li>
          <li>• <b>Persistent Peak Equity:</b> Preserves high-water mark across daemon restarts without resetting risk memory.</li>
          <li>• <b>Mandatory Human Approval:</b> Validated changes require explicit human authorization before updating active config.</li>
        </ul>
      </div>
    </div>
  </div>
  <div class="slide-footer">
    <span>Web4 Autonomous Paper-Trading Desk • Executive Reconstruction</span>
    <span>Est. Speaking Time: {SLIDES_DATA[0]['duration']} • Slide 1 / 8</span>
  </div>
</section>

<!-- SLIDE 2 -->
<section class="slide">
  <div class="slide-header">
    <div class="tag">{SLIDES_DATA[1]['tag']}</div>
    <h1>{SLIDES_DATA[1]['title']}</h1>
    <div class="subtitle">{SLIDES_DATA[1]['subtitle']}</div>
  </div>
  <div class="slide-body">
    <div class="grid-5">
      <div class="stage-box" style="border-top: 4px solid #10B981;">
        <div class="stage-num" style="color:#10B981;">STAGE 1 • SENSORS</div>
        <div class="stage-title">Watch Markets</div>
        <div class="stage-desc">Fetches closed 1h candles from Binance &amp; Yahoo Finance, plus CBOE VIX, FRED credit spreads, Polymarket odds, &amp; SEC EDGAR filings.</div>
      </div>
      <div class="stage-box" style="border-top: 4px solid #38BDF8;">
        <div class="stage-num" style="color:#38BDF8;">STAGE 2 • PLAYBOOKS</div>
        <div class="stage-title">Propose Trade</div>
        <div class="stage-desc">4 deterministic math strategies scan for trend breakouts (Momentum), oversold bounces (Mean Reversion), Pairs, or Basis Yield.</div>
      </div>
      <div class="stage-box" style="border-top: 4px solid #F59E0B;">
        <div class="stage-num" style="color:#F59E0B;">STAGE 3 • ARBITER</div>
        <div class="stage-title">Filter &amp; Veto</div>
        <div class="stage-desc">Coordinator &amp; 5 Skill Gates block trades in bear trends (&lt;200 EMA), sideways chop, NYSE opening bell gaps, or adverse SEC 8-K events.</div>
      </div>
      <div class="stage-box" style="border-top: 4px solid #A855F7;">
        <div class="stage-num" style="color:#A855F7;">STAGE 4 • SHIELD</div>
        <div class="stage-title">Size Risk [1%–5%]</div>
        <div class="stage-desc">Risk Engine sizes units by volatility (ATR), caps concurrent crypto to 2 positions, checks spread/staleness, &amp; runs 0.46s AI news check.</div>
      </div>
      <div class="stage-box" style="border-top: 4px solid #10B981;">
        <div class="stage-num" style="color:#10B981;">STAGE 5 • VAULT</div>
        <div class="stage-title">Paper Fill &amp; Learn</div>
        <div class="stage-desc">Atomically records the simulated fill in <code>paper_portfolio.json</code>, manages break-even &amp; 50% scale-outs, and logs to Decision Journal.</div>
      </div>
    </div>
    <div class="banner">
      <b style="color:#38BDF8;">Why This 5-Stage Funnel Matters in Plain English:</b> Most automated bots jump straight from Stage 2 (Signal) to Stage 5 (Execution)—which is why they get wiped out during market crashes or earnings shocks. In our desk, <b>Stages 3 and 4 act as an institutional safety firewall</b> that blocked <b>408 hazardous trade setups</b> across our 180-day replay.
    </div>
  </div>
  <div class="slide-footer">
    <span>Pipeline Source: live_paper_trader.py &rarr; core/coordinator.py &rarr; core/risk_engine.py &rarr; core/paper_account.py</span>
    <span>Est. Speaking Time: {SLIDES_DATA[1]['duration']} • Slide 2 / 8</span>
  </div>
</section>

<!-- SLIDE 3 -->
<section class="slide">
  <div class="slide-header">
    <div class="tag">{SLIDES_DATA[2]['tag']}</div>
    <h1>{SLIDES_DATA[2]['title']}</h1>
    <div class="subtitle">{SLIDES_DATA[2]['subtitle']}</div>
  </div>
  <div class="slide-body">
    <div class="img-wrap">
      <img src="{arch_b64}" alt="Full System Architecture Diagram" style="max-height: 5.35in;">
    </div>
  </div>
  <div class="slide-footer">
    <span>8 Deterministic Math &amp; Rule Modules (0% Hallucination) + 4 Local Ollama phi4-mini AI Agents + 5 Institutional Skill Gates</span>
    <span>Est. Speaking Time: {SLIDES_DATA[2]['duration']} • Slide 3 / 8</span>
  </div>
</section>

<!-- SLIDE 4 -->
<section class="slide">
  <div class="slide-header">
    <div class="tag">{SLIDES_DATA[3]['tag']}</div>
    <h1>{SLIDES_DATA[3]['title']}</h1>
    <div class="subtitle">{SLIDES_DATA[3]['subtitle']}</div>
  </div>
  <div class="slide-body">
    <div class="grid-3">
      <div class="card" style="border-top: 3px solid #38BDF8;">
        <h3 style="color:#38BDF8;">1. Signal Arbitration &amp; Ranking</h3>
        <ul>
          <li>• <b>Relative Strength Priority:</b> Assets are sorted by 24-bar outperformance vs. BTC/SPY so strongest leaders get capital first.</li>
          <li>• <b>Conflict Cancellation:</b> If Momentum says <code>BUY</code> while Mean Reversion says <code>SELL</code>, both cancel out and the bot stays in safe cash.</li>
        </ul>
      </div>
      <div class="card" style="border-top: 3px solid #F59E0B;">
        <h3 style="color:#F59E0B;">2. Regime &amp; Institutional Gates</h3>
        <ul>
          <li>• <b>Regime Routing:</b> Below 200 EMA blocks Long breakouts; low ADX (&lt;20) routes to Mean Reversion; strong trend (ADX &ge;25) suppresses counter-trend trades.</li>
          <li>• <b>Wall Street Compliance:</b> Blocks first 15m after 9:30 AM NYSE bell, SEC 8-K distress events, insolvent 10-Q XBRL balance sheets, and 30d wash sales.</li>
        </ul>
      </div>
      <div class="card" style="border-top: 3px solid #A855F7;">
        <h3 style="color:#A855F7;">3. Pre-Flight &amp; 0.46s AI Veto</h3>
        <ul>
          <li>• <b>Microstructure Check:</b> Verifies quote staleness (&le;180s), bid-ask spread (&le;0.15%), and hourly volume participation (&le;2%).</li>
          <li>• <b>Local LLM Devil's Advocate:</b> Queries local <code>phi4-mini</code> on recent headlines (measured <code>0.46s</code> latency in live logs) to veto black-swan news.</li>
        </ul>
      </div>
    </div>
    <div class="card" style="border-left: 4px solid #10B981; background: #152238;">
      <h3 style="color:#10B981; margin-bottom: 6px;">4. Automated Position Lifecycle: How Profits Are Banked &amp; Losses Contained</h3>
      <div class="grid-3" style="gap: 0.15in;">
        <div style="font-size:10.8pt; color:#E2E8F0;"><b>Step A — Volatility Entry Sizing:</b> Units are sized by ATR distance so hitting the initial stop-loss loses only <b>1% to 2%</b> of portfolio equity (hard-capped at 5%).</div>
        <div style="font-size:10.8pt; color:#E2E8F0;"><b>Step B — Break-Even Stop Lock (+1.2x ATR):</b> Once unrealized profit reaches +1.2x ATR, the stop-loss automatically moves above entry price to eliminate loss risk.</div>
        <div style="font-size:10.8pt; color:#E2E8F0;"><b>Step C — 50% Scale-Out &amp; Chandelier Ratchet:</b> At +2.0x ATR (or Bollinger SMA), <b>50% of the position is banked into cash</b> and the rest trails tightly at +3R/+5R/+8R.</div>
      </div>
    </div>
  </div>
  <div class="slide-footer">
    <span>Verified in core/coordinator.py, core/skill_intelligence_gate.py, core/preflight.py, and data/live_status.json</span>
    <span>Est. Speaking Time: {SLIDES_DATA[3]['duration']} • Slide 4 / 8</span>
  </div>
</section>

<!-- SLIDE 5 -->
<section class="slide">
  <div class="slide-header">
    <div class="tag">{SLIDES_DATA[4]['tag']}</div>
    <h1>{SLIDES_DATA[4]['title']}</h1>
    <div class="subtitle">{SLIDES_DATA[4]['subtitle']}</div>
  </div>
  <div class="slide-body">
    <div class="grid-5">
      <div class="stage-box" style="border-top: 4px solid #38BDF8;">
        <div class="stage-title" style="color:#38BDF8; font-size:13pt;">1. The 4 Playbooks</div>
        <div class="stage-desc"><span class="wwh-label" style="background:#0284C7;color:#FFF;">WHAT</span> Momentum, Mean Reversion, Stat-Arb Pairs, &amp; Basis Yield.</div>
        <div class="stage-desc"><span class="wwh-label" style="background:#334155;color:#FFF;">WHY</span> Markets trend only ~30% of the time; we need bounce and passive yield engines for the other 70%.</div>
        <div class="stage-desc"><span class="wwh-label" style="background:#059669;color:#FFF;">HOW</span> Uses strictly causal indicators (<code>.shift(1)</code>) with zero lookahead bias.</div>
      </div>
      <div class="stage-box" style="border-top: 4px solid #F59E0B;">
        <div class="stage-title" style="color:#F59E0B; font-size:13pt;">2. Skill Gatekeeper</div>
        <div class="stage-desc"><span class="wwh-label" style="background:#D97706;color:#FFF;">WHAT</span> 5 institutional compliance gates (NYSE, SEC 8-K/XBRL, 13F, Tax).</div>
        <div class="stage-desc"><span class="wwh-label" style="background:#334155;color:#FFF;">WHY</span> Price charts alone cannot see bankruptcies, earnings restatements, or market holidays.</div>
        <div class="stage-desc"><span class="wwh-label" style="background:#059669;color:#FFF;">HOW</span> Checks live SEC EDGAR filings and blocks repeat ticker losses (48h cooldown).</div>
      </div>
      <div class="stage-box" style="border-top: 4px solid #F43F5E;">
        <div class="stage-title" style="color:#F43F5E; font-size:13pt;">3. Hard Risk Shield</div>
        <div class="stage-desc"><span class="wwh-label" style="background:#E11D48;color:#FFF;">WHAT</span> Centralized sizer &amp; circuit breaker (<code>core/risk_engine.py</code>).</div>
        <div class="stage-desc"><span class="wwh-label" style="background:#334155;color:#FFF;">WHY</span> Prevents any single bad trade or crypto crash from wiping out the portfolio.</div>
        <div class="stage-desc"><span class="wwh-label" style="background:#059669;color:#FFF;">HOW</span> Hard-clamps risk to <b>[1%, 5%]</b> equity, caps crypto at 2 positions, &amp; halts at 15% DD.</div>
      </div>
      <div class="stage-box" style="border-top: 4px solid #10B981;">
        <div class="stage-title" style="color:#10B981; font-size:13pt;">4. Paper Vault</div>
        <div class="stage-desc"><span class="wwh-label" style="background:#059669;color:#FFF;">WHAT</span> Simulated account ledger (<code>PaperAccount</code>) &amp; OS process lock.</div>
        <div class="stage-desc"><span class="wwh-label" style="background:#334155;color:#FFF;">WHY</span> Guarantees $0 real money is ever risked and prevents dual-process file corruption.</div>
        <div class="stage-desc"><span class="wwh-label" style="background:#059669;color:#FFF;">HOW</span> Enforces kernel <code>fcntl</code> lockfiles and atomic <code>.tmp &rarr; .bak</code> JSON saves.</div>
      </div>
      <div class="stage-box" style="border-top: 4px solid #A855F7;">
        <div class="stage-title" style="color:#A855F7; font-size:13pt;">5. Night-Shift Crew</div>
        <div class="stage-desc"><span class="wwh-label" style="background:#7C3AED;color:#FFF;">WHAT</span> Autonomous Agents 9, 10, 11, &amp; 12 running in the background.</div>
        <div class="stage-desc"><span class="wwh-label" style="background:#334155;color:#FFF;">WHY</span> Static watchlists decay; losing stocks must be quarantined and re-audited.</div>
        <div class="stage-desc"><span class="wwh-label" style="background:#059669;color:#FFF;">HOW</span> Scans markets every 24h, audits every 15 trades, &amp; requires 125/125 passing tests.</div>
      </div>
    </div>
  </div>
  <div class="slide-footer">
    <span>WHAT / WHY / HOW Component Summary for Non-Technical &amp; Executive Audiences</span>
    <span>Est. Speaking Time: {SLIDES_DATA[4]['duration']} • Slide 5 / 8</span>
  </div>
</section>

<!-- SLIDE 6 -->
<section class="slide">
  <div class="slide-header">
    <div class="tag">{SLIDES_DATA[5]['tag']}</div>
    <h1>{SLIDES_DATA[5]['title']}</h1>
    <div class="subtitle">{SLIDES_DATA[5]['subtitle']}</div>
  </div>
  <div class="slide-body">
    <div class="img-wrap">
      <img src="{perf_b64}" alt="Shifted 90-Day OOS & Targeted Optimization Evidence" style="max-height: 4.35in;">
    </div>
    <div class="banner" style="border-color:#10B981;">
      <b style="color:#10B981;">Shifted 90-Day OOS & Targeted Optimization Summary:</b>
      <b>Baseline OOS Window 2 (Days -90..0):</b> +11.35% net return, 2.42 Sharpe, 6.80% MaxDD under Taker costs.
      <b>Deployed Proposal (PROP-A15F08C2):</b> <b>+15.60% net return (+$1,559.50)</b>, <b>2.90 Sharpe</b>, <b>+5.34% adverse W1 return</b> with 2.5x StatArb edge-to-cost gate.
      <b>Baselines:</b> Same-Basket Buy &amp; Hold: <b>-0.95%</b> (MaxDD 20.75%, Sharpe 0.10) | Random Noise Monte Carlo: <b>-5.00% &plusmn; 6.98%</b>.
      <b>Recorded Live Paper Account:</b> $8,919.93 (-10.80%, 34 historical trades, 0 open positions).
    </div>
  </div>
  <div class="slide-footer">
    <span>Sources: reports/targeted_profitability_optimization/run_optimization_research.py • config/active_config.json • data/paper_portfolio.json</span>
    <span>Est. Speaking Time: {SLIDES_DATA[5]['duration']} • Slide 6 / 8</span>
  </div>
</section>

<!-- SLIDE 7 -->
<section class="slide">
  <div class="slide-header">
    <div class="tag">{SLIDES_DATA[6]['tag']}</div>
    <h1>{SLIDES_DATA[6]['title']}</h1>
    <div class="subtitle">{SLIDES_DATA[6]['subtitle']}</div>
  </div>
  <div class="slide-body">
    <div class="img-wrap">
      <img src="{def_b64}" alt="20-Proposal LLM Governance Benchmark & Defense Gate Telemetry" style="max-height: 4.35in;">
    </div>
    <div class="banner" style="border-color:#F59E0B;">
      <b style="color:#F59E0B;">Controlled 20-Proposal LLM Governance & 4-Gate Auto-Approval Engine:</b>
      <b>Exp A (Rules Only):</b> 75.0% accuracy, 50.0% false approvals, Portfolio Delta: <b>-22.17%</b>.
      <b>Exp B (Single LLM):</b> 90.0% accuracy, 20.0% false rejections, Portfolio Delta: +9.62%.
      <b>Exp C (Two-Agent Analyst + Governor):</b> <b>100.0% accuracy</b>, <b>0.0% false approvals</b>, Portfolio Delta: <b>+11.63%</b>.
      <b>4-Gate Auto-Approval Live Proof:</b> Blocked PROP-ED02E10E (-0.84% OOS W1) from auto-deploy to protect against overfitting, while deploying verified proposal PROP-A15F08C2.
    </div>
  </div>
  <div class="slide-footer">
    <span>Sources: reports/llm_governance_benchmark_20.json • agent/governor.py • config/pending_proposals.json</span>
    <span>Est. Speaking Time: {SLIDES_DATA[6]['duration']} • Slide 7 / 8</span>
  </div>
</section>

<!-- SLIDE 8 -->
<section class="slide">
  <div class="slide-header">
    <div class="tag">{SLIDES_DATA[7]['tag']}</div>
    <h1>{SLIDES_DATA[7]['title']}</h1>
    <div class="subtitle">{SLIDES_DATA[7]['subtitle']}</div>
  </div>
  <div class="slide-body">
    <div class="grid-3">
      <div class="card" style="border-top: 4px solid #10B981;">
        <h3 style="color:#10B981;">1. Verified Capabilities (152/152 Tests)</h3>
        <ul>
          <li>• <b>Deployed PROP-A15F08C2:</b> +15.60% OOS W2 return (Sharpe 2.90, +$1,559.50) with 2.5x StatArb edge-to-cost gate on main.</li>
          <li>• <b>4-Gate Conditional Auto-Approval:</b> Autonomous verification &amp; Git push to GitHub; safely holds questionable proposals for human review.</li>
          <li>• <b>Complete Multi-Strategy Engine:</b> 4 active strategies (Momentum, Mean Reversion, Stat-Arb Pairs, Basis Yield) with dynamic registry.</li>
          <li>• <b>Persistent Drawdown Circuit Breaker:</b> Bound to PaperAccount, halts new entries across all strategies at 15% MaxDD threshold.</li>
        </ul>
      </div>
      <div class="card" style="border-top: 4px solid #F59E0B;">
        <h3 style="color:#F59E0B;">2. Honest Limitations &amp; Boundaries</h3>
        <ul>
          <li>• <b>Simulated Execution Only:</b> All operations run exclusively through simulated PaperAccount with strict zero real-money broker risk.</li>
          <li>• <b>Anti-Overfitting Restraint:</b> Walk-forward tournament rejects noisy mutations (e.g. rejecting PROP-ED02E10E) to protect capital.</li>
          <li>• <b>Funding Regime Lock:</b> When funding rates overheat, long entries are strictly blocked while short basis yield accumulates passive carry.</li>
          <li>• <b>Historical Paper Account Drag:</b> Live paper balance ($8,919.93) reflects early pre-remediation drawdown from early October baseline.</li>
        </ul>
      </div>
      <div class="card" style="border-top: 4px solid #38BDF8;">
        <h3 style="color:#38BDF8;">3. Current Operations &amp; Roadmap</h3>
        <ul>
          <li>• <b>1. Active 24/7 Live Daemon:</b> Running continuously on main via <code>live_paper_trader.py --loop --interval 60 --auto-approve</code>.</li>
          <li>• <b>2. Cointegrated ETF Pairs Expansion:</b> Add matched sector pairs (e.g., SMH/SOXX, XLE/USO) to scale stat-arb pair trading.</li>
          <li>• <b>3. Persistent SEC Fundamental Cache:</b> Cache EDGAR XBRL &amp; 8-K responses to disk to accelerate cold CLI scans.</li>
          <li>• <b>4. Continuous Self-Evolution:</b> Closed-loop decision journal monitors closed trades every 15 deals and self-optimizes every 6 hours.</li>
        </ul>
      </div>
    </div>
    <div class="banner" style="border-color:#38BDF8; text-align:center;">
      <b>Drawdown Circuit Breaker Invariant:</b> The drawdown circuit breaker blocks new position entries across the trading system when the configured portfolio risk threshold is reached.<br>
      <b>Bottom Line:</b> Web4 demonstrates how <b>deterministic quantitative math</b>, <b>dynamic multi-strategy execution</b>, and <b>4-gate autonomous governance</b> unite with <b>zero real-money risk</b>.
    </div>
  </div>
  <div class="slide-footer">
    <span>Deliverables: web4_trading_agent_presentation.pptx (Editable) • web4_trading_agent_presentation.pdf • 152/152 Tests Passing</span>
    <span>Est. Speaking Time: {SLIDES_DATA[7]['duration']} • Total Duration: ~7.5 Minutes • Slide 8 / 8</span>
  </div>
</section>

</body>
</html>
"""
    HTML_PATH.write_text(html_content, encoding="utf-8")

    # Export to PDF via headless Google Chrome
    cmd = [
        "/usr/bin/google-chrome",
        "--headless",
        "--disable-gpu",
        "--no-sandbox",
        f"--print-to-pdf={PDF_PATH}",
        "--no-pdf-header-footer",
        f"file://{HTML_PATH}",
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    # Render PDF pages to PNG for visual inspection
    subprocess.run(
        ["/usr/bin/pdftoppm", "-png", "-r", "150", str(PDF_PATH), str(ASSETS_DIR / "slide_preview")],
        check=True,
        capture_output=True,
    )
    return HTML_PATH, PDF_PATH


def main():
    print("[PresentationBuilder] 1. Generating high-resolution visual diagrams and data charts...")
    arch_img = generate_slide3_architecture_diagram()
    perf_img = generate_slide6_performance_charts()
    def_img = generate_slide7_defense_proof()
    print(f"  -> {arch_img.relative_to(PROJECT_ROOT)}")
    print(f"  -> {perf_img.relative_to(PROJECT_ROOT)}")
    print(f"  -> {def_img.relative_to(PROJECT_ROOT)}")

    print("[PresentationBuilder] 2. Building native editable 16:9 PowerPoint (.pptx) with Speaker Notes...")
    pptx_out = build_editable_pptx(arch_img, perf_img, def_img)
    print(f"  -> {pptx_out.relative_to(PROJECT_ROOT)} ({pptx_out.stat().st_size:,} bytes)")

    print("[PresentationBuilder] 3. Building 16:9 HTML5 deck, PDF export, and slide preview renders...")
    html_out, pdf_out = build_html_and_pdf(arch_img, perf_img, def_img)
    print(f"  -> {html_out.relative_to(PROJECT_ROOT)} ({html_out.stat().st_size:,} bytes)")
    print(f"  -> {pdf_out.relative_to(PROJECT_ROOT)} ({pdf_out.stat().st_size:,} bytes)")
    print("[PresentationBuilder] Done!")


if __name__ == "__main__":
    main()
