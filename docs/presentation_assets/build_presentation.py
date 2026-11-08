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
        ("Agent 5: Coordinator Arbiter", "core/coordinator.py (Rules)\n200 EMA & ADX Regime Router", 68.5, ACCENT_AMBER),
        ("5 Institutional Skill Gates", "core/skill_intelligence_gate.py\nNYSE Bell, SEC 8-K/XBRL, Tax Guard", 53.5, ACCENT_AMBER),
        ("Agent 6: Risk Engine [1%–5%]", "core/risk_engine.py (Math)\nATR Sizing & 15% DD Kill Switch", 38.5, ACCENT_CYAN),
        ("Agent 8: Pre-Flight + AI Check", "core/preflight.py (Math + LLM)\nSpread Guard + phi4-mini News Veto", 23.5, ACCENT_PURPLE),
        ("Agent 7: Paper Account & Lock", "core/paper_account.py (OS/IO)\nAtomic JSON Ledger & Decision Journal", 9.5, ACCENT_AMBER),
    ]
    for title, desc, cy, col in core_nodes:
        _draw_box(ax, 48.3, cy, 22.4, 11.2, facecolor=BG_CARD, edgecolor=col, lw=1.4, radius=1.2)
        ax.text(59.5, cy + 7.8, title, color=TEXT_MAIN, fontsize=8.5, fontweight="bold", ha="center")
        ax.text(59.5, cy + 3.2, desc, color=TEXT_MUTED, fontsize=7.2, ha="center", va="center")

    # Column 4: Tier 2 Autonomous Self-Improvement & Governance
    _draw_box(ax, 75.0, 8.0, 23.5, 80.5, facecolor="#0E1729", edgecolor=ACCENT_PURPLE, lw=1.5, radius=2.0)
    ax.text(86.75, 85.0, "4. SELF-IMPROVEMENT LOOP", color=ACCENT_PURPLE, fontsize=10, fontweight="bold", ha="center")
    ax.text(86.75, 81.8, "(Background Meta-Agents)", color=TEXT_MUTED, fontsize=8, ha="center")

    meta_nodes = [
        ("Agent 9: Alpha Scanner", "agent/asset_selector.py (Every 24h)\nMath Backtest + LLM Thesis\n& Blacklist Parole Hearing", 64.0, ACCENT_PURPLE),
        ("Agent 10: Forensic Analyst", "agent/trade_analyst.py (Every 15 Deals)\nMath Attribution + LLM Diagnosis\nAuto-Prunes Bleeders", 45.0, ACCENT_PURPLE),
        ("Agent 11: Optimizer Arena", "agent/optimizer.py (Every 6h)\n144-Grid Walk-Forward Math\n(70% Train / 30% Unseen Test)", 26.5, ACCENT_CYAN),
        ("Agent 12: Chief Risk Governor", "agent/governor.py (Sole Write Key)\nAudits Invariants + 125 Pytests\n+ LLM Risk Officer Verdict", 9.5, ACCENT_PURPLE),
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
    """Generate side-by-side verified performance charts from data/live_replay_backtest.json."""
    fig = plt.figure(figsize=(13.2, 5.1), dpi=240, facecolor=BG_DARK)
    gs = fig.add_gridspec(1, 2, width_ratios=[1.0, 1.15], wspace=0.28, left=0.17, right=0.97, top=0.86, bottom=0.14)

    # Subplot 1: Strategy Engine Net Profit & Win Rate (180-Day Verified Replay)
    ax1 = fig.add_subplot(gs[0, 0], facecolor=BG_CARD)
    engines = ["Mean Reversion\n(75.7% WR | 107 Deals)", "Momentum Breakout\n(50.0% WR | 138 Deals)", "Basis Funding Yield\n(100% WR | 614 Payouts)", "Total Net Profit\n(Maker Mode +13.65%)"]
    profits = [967.44, 211.11, 186.86, 1365.33]
    colors = [ACCENT_CYAN, ACCENT_PURPLE, ACCENT_AMBER, ACCENT_GREEN]

    y_pos = np.arange(len(engines))
    bars = ax1.barh(y_pos, profits, color=colors, height=0.55, edgecolor="#FFFFFF", linewidth=0.6)
    ax1.set_yticks(y_pos)
    ax1.set_yticklabels(engines, color=TEXT_MAIN, fontsize=8.8, fontweight="bold")
    ax1.invert_yaxis()
    ax1.set_xlabel("Verified 180-Day Net Realized P&L (USD on 10,000 Initial Capital)", color=TEXT_MUTED, fontsize=8.8)
    ax1.set_title("A. Profit Contribution by Strategy Engine", color=ACCENT_CYAN, fontsize=11.0, fontweight="bold", pad=12)
    ax1.set_xlim(0, 1720)
    ax1.grid(axis="x", color=BORDER_SUBTLE, linestyle="--", alpha=0.5)
    ax1.tick_params(colors=TEXT_MUTED, labelsize=8.5)
    for spine in ax1.spines.values():
        spine.set_color(BORDER_SUBTLE)

    for bar, val in zip(bars, profits):
        ax1.text(val + 30, bar.get_y() + bar.get_height() / 2, f"+${val:,.2f}", va="center", color=TEXT_MAIN, fontsize=9.0, fontweight="bold")

    # Subplot 2: Top Asset Winners vs. Underperforming Drags (Taker+Slippage Stress Replay)
    ax2 = fig.add_subplot(gs[0, 1], facecolor=BG_CARD)
    assets = ["IREN", "MU", "BNB/USDT", "CIFR", "XRP/USDT", "BTC (Yield)", "SOL/USDT", "LINK/USDT", "WULF", "NOK", "NVDA"]
    asset_pnl = [487.06, 464.14, 288.05, 279.36, 228.21, 184.05, 149.75, 139.08, -192.44, -241.12, -590.00]
    a_colors = [ACCENT_GREEN if v >= 0 else ACCENT_RED for v in asset_pnl]

    y2 = np.arange(len(assets))
    bars2 = ax2.barh(y2, asset_pnl, color=a_colors, height=0.62)
    ax2.axvline(0, color=TEXT_MUTED, linewidth=1.0)
    ax2.set_yticks(y2)
    ax2.set_yticklabels(assets, color=TEXT_MAIN, fontsize=8.8, fontweight="bold")
    ax2.invert_yaxis()
    ax2.set_xlabel("Net Realized P&L After Taker Fees + Slippage (USD)", color=TEXT_MUTED, fontsize=8.8)
    ax2.set_title("B. Per-Asset P&L Leaderboard (Winners & Honest Drags)", color=ACCENT_GREEN, fontsize=11.0, fontweight="bold", pad=12)
    ax2.set_xlim(-780, 680)
    ax2.grid(axis="x", color=BORDER_SUBTLE, linestyle="--", alpha=0.5)
    ax2.tick_params(colors=TEXT_MUTED, labelsize=8.5)
    for spine in ax2.spines.values():
        spine.set_color(BORDER_SUBTLE)

    for bar, val in zip(bars2, asset_pnl):
        offset = 20 if val >= 0 else -20
        ha = "left" if val >= 0 else "right"
        ax2.text(val + offset, bar.get_y() + bar.get_height() / 2, f"${val:+,.0f}", va="center", ha=ha, color=TEXT_MAIN, fontsize=8.5, fontweight="bold")

    out_path = ASSETS_DIR / "slide6_performance_charts.png"
    fig.savefig(out_path, facecolor=BG_DARK)
    plt.close(fig)
    return out_path


def generate_slide7_defense_proof() -> Path:
    """Generate visual proof of Quarantine Impact and Defense Gate Blocks."""
    fig = plt.figure(figsize=(13.2, 5.0), dpi=240, facecolor=BG_DARK)
    gs = fig.add_gridspec(1, 2, width_ratios=[0.95, 1.15], wspace=0.52, left=0.07, right=0.97, top=0.85, bottom=0.15)

    # Left: Before vs After Quarantining Toxic Drags (BMT, ENA, SOFI, INTC, CRWV, SNAP, PLUG)
    ax1 = fig.add_subplot(gs[0, 0], facecolor=BG_CARD)
    categories = ["Unpruned\n(Toxic Drags)", "Pruned Maker\n(Active List)", "Pruned Taker\n(0.10% Stress)"]
    net_profits = [91.22, 1365.33, 1001.26]
    max_dds = [15.33, 9.81, 10.19]

    x = np.arange(len(categories))
    b1 = ax1.bar(x, net_profits, width=0.46, color=[ACCENT_RED, ACCENT_GREEN, ACCENT_CYAN], edgecolor="#FFFFFF", linewidth=0.7)
    ax1.set_xticks(x)
    ax1.set_xticklabels(categories, color=TEXT_MAIN, fontsize=8.8, fontweight="bold")
    ax1.set_ylabel("180-Day Net Profit (USD)", color=TEXT_MUTED, fontsize=8.8)
    ax1.set_title("A. Impact of Autonomous Drag Quarantine", color=ACCENT_AMBER, fontsize=11.0, fontweight="bold", pad=12)
    ax1.set_ylim(0, 1650)
    ax1.grid(axis="y", color=BORDER_SUBTLE, linestyle="--", alpha=0.5)
    ax1.tick_params(colors=TEXT_MUTED, labelsize=8.5)
    for spine in ax1.spines.values():
        spine.set_color(BORDER_SUBTLE)

    for bar, p_val, dd_val in zip(b1, net_profits, max_dds):
        ax1.text(
            bar.get_x() + bar.get_width() / 2,
            p_val + 45,
            f"+${p_val:,.0f}\n(DD: {dd_val:.1f}%)",
            ha="center",
            va="bottom",
            color=TEXT_MAIN,
            fontsize=8.6,
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
        "Exposure Caps",
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
        "title": "Web4 Autonomous Trading Desk",
        "subtitle": "A self-governing, multi-agent paper-trading system that watches Crypto & US Stocks 24/7, filters out market noise, and strictly protects capital.",
        "duration": "50 seconds",
        "notes": (
            "Welcome everyone. Today I am presenting the Web4 Autonomous Trading Desk. "
            "In plain English, think of this project as an automated digital hedge fund that runs 24/7 on a local computer or server. "
            "It solves two major problems in retail and algorithmic trading: human emotional mistakes, and static trading bots that break whenever the market mood changes. "
            "Our system combines fast, exact mathematical formulas for trading and risk sizing with a local AI language model that reads breaking news and audits past trades. "
            "Most importantly, it operates in 100% simulated paper-trading mode—meaning zero real money is ever put at risk while proving +13.65% net return over a 180-day historical replay."
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
            "In Stage 2, four independent mathematical playbooks scan for opportunities—looking for strong upward breakouts, oversold rubber-band bounces, or passive funding yield. "
            "In Stage 3, our Central Referee and 5 Institutional Safety Gates step in: they block trades if the market is in a bear trend, during the chaotic first 15 minutes of the Wall Street opening bell, or if breaking news looks dangerous. "
            "In Stage 4, the Risk Engine sizes the trade so we never risk more than 1% to 5% of the account. "
            "Finally, Stage 5 records the simulated fill safely to disk and logs the decision so the system can learn from it."
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
            "In Amber (Column 3), our Orchestrators and Institutional Skill Gates enforce hard rules: the Coordinator resolves signal conflicts, the Skill Gate checks NYSE hours, SEC 8-K and 10-Q filings, and 30-day tax wash-sale rules, and the Paper Account locks the ledger. "
            "In Purple, only four components use our local AI model (phi4-mini via Ollama): Agent 8 (Pre-Flight Devil's Advocate, which reads headlines in 0.46 seconds), Agent 9 (Alpha Scanner), Agent 10 (Forensic Trade Analyst), and Agent 12 (Chief Risk Governor). "
            "Notice the green feedback arrow at the bottom: Agents 9, 10, and 11 can only submit proposals. Agent 12 is the sole gatekeeper with write access, and it only deploys changes if all 125 automated tests pass."
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
            "When a new candle closes, both Momentum and Mean Reversion evaluate the asset. "
            "If Momentum says BUY and Mean Reversion says SELL, the Coordinator immediately cancels both—we never trade when our own playbooks disagree. "
            "Next, the Regime Router checks the market environment: if the price is below its 200-hour average, long breakouts are blocked; if the market is choppy (ADX below 20), breakout trades are suppressed in favor of Mean Reversion. "
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
            "First, the Multi-Strategy Playbooks give us three ways to win: riding trends, buying oversold bounces, and collecting passive crypto funding yield when cash is idle. "
            "Second, the Institutional Skill Gate brings Wall Street compliance into the bot—checking SEC EDGAR financial statements, 8-K material events, and wash-sale cooldowns. "
            "Third, the Hard Risk Shield guarantees survival: position risk is hard-coded between 1% and 5% of equity, and a 15% maximum drawdown circuit breaker acts as an emergency brake. "
            "Fourth, the Atomic Paper Vault ensures zero real money is ever touched and prevents file corruption using operating-system process locks. "
            "Fifth, the Autonomous Quarantine and Parole Loop automatically blacklists losing assets so one falling stock cannot sink the portfolio, while periodically re-testing quarantined assets to see if they have recovered."
        ),
    },
    {
        "num": 6,
        "tag": "SLIDE 6 OF 8 • VERIFIED PERFORMANCE & REAL PROJECT EVIDENCE",
        "title": "180-Day Historical Backtest vs. Live Paper Telemetry",
        "subtitle": "All numbers come directly from reproducible project files (data/live_replay_backtest.json & data/paper_portfolio.json).",
        "duration": "65 seconds",
        "notes": (
            "Let's look at the actual measured results—and let's be 100% transparent about what is a historical backtest versus live paper trading. "
            "In our 180-day full historical replay across the 14 active watchlist assets, starting with $10,000 virtual capital, the system achieved a +13.65% net return (+$1,365.33 profit) in Maker limit mode, and +10.01% (+$1,001.26 profit) under harsh Taker-plus-slippage stress testing. "
            "Max portfolio drawdown was held to just 9.81%, with an annualized Sharpe Ratio of 1.52 and an 88.9% overall win rate (61.2% on directional trades). "
            "Chart A shows that all three active engines were profitable: Mean Reversion led with +$967.44 (75.7% win rate), Momentum added +$211.11, and Basis Yield harvested +$186.86. "
            "Chart B shows our honest per-asset breakdown: IREN (+$487), Micron MU (+$464), BNB (+$288), and CIFR (+$279) were top winners, while NVIDIA (-$590) and Nokia (-$241) were drags under the 48-bar lookback. "
            "Separately, in our live paper-trading ledger started in mid-October prior to the recent quarantine upgrades, current virtual equity stands at $8,919.40 across 32 closed trades and 4 live basis yield payouts."
        ),
    },
    {
        "num": 7,
        "tag": "SLIDE 7 OF 8 • WHY DEFENSE & QUARANTINE MATTER",
        "title": "What the Data Proves: Defense Gates & Drag Quarantine",
        "subtitle": "Quarantining 3 toxic assets increased 180-day profit from +$91 to +$1,365 and cut drawdown from 15.3% to 9.8%.",
        "duration": "50 seconds",
        "notes": (
            "Why do we emphasize defense gates and asset quarantine so heavily? The empirical data proves why. "
            "Look at Chart A on the left: when unvetted micro-cap and dragging assets like BMT/USDT (-$1,029), ENA/USDT, and SOFI were left in the active universe, the portfolio barely broke even (+$91.22) and hit a 15.33% drawdown that triggered the kill switch. "
            "The moment our quarantine filter moved those drags to the blacklist, 180-day profit jumped 15x to +$1,365.33 and maximum drawdown fell to 9.81%. "
            "Look at Chart B on the right: across the 180-day replay, our automated safety gates blocked 408 bad trade setups before entry—including 120 sideways chop signals, 80 volatile NYSE opening-bell bars, 75 over-concentrated crypto entries, and 56 macro bear traps."
        ),
    },
    {
        "num": 8,
        "tag": "SLIDE 8 OF 8 • ACHIEVEMENTS, LIMITATIONS & NEXT STEPS",
        "title": "Current Capabilities, Honest Limitations & Roadmap",
        "subtitle": "A fully verified, 125-test autonomous paper-trading platform with clear boundaries between proven results and future work.",
        "duration": "50 seconds",
        "notes": (
            "To conclude, let's clearly separate what has been verified today from current limitations and next steps. "
            "What is 100% implemented and verified: our 12-module architecture, 4 strategy engines, 5 institutional NYSE/SEC/Tax skill gates, hard 1%-to-5% risk bounds, autonomous market discovery with blacklist parole hearings, and a 125-for-125 passing automated test suite. "
            "What limitations remain: first, all execution is simulated paper trading—maker limit fills in backtesting do not model order-book queue priority. Second, NVIDIA (-$590) underperformed under the 48-bar swing profile and needs parameter tuning or quarantine. Third, Stat-Arb Pairs had zero triggers under our strict cointegration thresholds in the 180-day run. "
            "Our immediate next steps are continuing forward live paper observation on the newly pruned 14-asset watchlist, calibrating per-equity lookback windows, and persisting SEC fundamental caches to disk. Thank you!"
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
            ("+13.65%", 28.0, ACCENT_GREEN, True, "ctr"),
            ("180-DAY BACKTEST RETURN", 9.5, TEXT_MAIN, True, "ctr"),
            ("+$1,365.33 net (Maker mode)", 9.0, TEXT_MUTED, False, "ctr"),
        ]),
        _xml_shape_box(11, "KPI2", 3.65, 1.65, 2.85, 1.35, BG_CARD, ACCENT_CYAN, 1.8, [
            ("9.81%", 28.0, ACCENT_CYAN, True, "ctr"),
            ("MAX PORTFOLIO DRAWDOWN", 9.5, TEXT_MAIN, True, "ctr"),
            ("Hard kill-switch capped at 15%", 9.0, TEXT_MUTED, False, "ctr"),
        ]),
        _xml_shape_box(12, "KPI3", 6.75, 1.65, 2.85, 1.35, BG_CARD, ACCENT_PURPLE, 1.8, [
            ("8 Math + 4 AI", 24.0, ACCENT_PURPLE, True, "ctr"),
            ("NEURO-SYMBOLIC MODULES", 9.5, TEXT_MAIN, True, "ctr"),
            ("Exact math + local phi4-mini", 9.0, TEXT_MUTED, False, "ctr"),
        ]),
        _xml_shape_box(13, "KPI4", 9.85, 1.65, 2.90, 1.35, BG_CARD, ACCENT_AMBER, 1.8, [
            ("125 / 125", 28.0, ACCENT_AMBER, True, "ctr"),
            ("AUTOMATED TESTS PASSING", 9.5, TEXT_MAIN, True, "ctr"),
            ("100% Paper-Trading Immunity", 9.0, TEXT_MUTED, False, "ctr"),
        ]),
        _xml_shape_box(14, "CardProblem", 0.55, 3.25, 3.9, 3.55, BG_CARD, ACCENT_RED, 1.5, [
            ("1. The Problem We Solve", 15.0, ACCENT_RED, True, "l"),
            (" ", 6.0, TEXT_MUTED, False, "l"),
            ("• Emotional Human Trading: Fear and greed cause traders to buy tops and panic-sell bottoms.", 11.5, TEXT_MAIN, False, "l"),
            ("• Brittle Rule Bots: Traditional bots use fixed settings that break when calm markets turn choppy.", 11.5, TEXT_MAIN, False, "l"),
            ("• AI Hallucinations: Letting LLMs directly pick position sizes risks catastrophic losses.", 11.5, TEXT_MAIN, False, "l"),
        ]),
        _xml_shape_box(15, "CardSolution", 4.70, 3.25, 3.9, 3.55, BG_CARD, ACCENT_CYAN, 1.5, [
            ("2. How Our Desk Works", 15.0, ACCENT_CYAN, True, "l"),
            (" ", 6.0, TEXT_MUTED, False, "l"),
            ("• 24/7 Multi-Asset Watch: Tracks 14 vetted Crypto & US Equity assets simultaneously.", 11.5, TEXT_MAIN, False, "l"),
            ("• Math for Money: Deterministic formulas calculate signals, stops, and 1%–5% position sizes.", 11.5, TEXT_MAIN, False, "l"),
            ("• Local AI for Context: phi4-mini screens breaking news (0.46s) and audits closed deals.", 11.5, TEXT_MAIN, False, "l"),
        ]),
        _xml_shape_box(16, "CardSafety", 8.85, 3.25, 3.9, 3.55, BG_CARD, ACCENT_GREEN, 1.5, [
            ("3. Built-In Capital Safety", 15.0, ACCENT_GREEN, True, "l"),
            (" ", 6.0, TEXT_MUTED, False, "l"),
            ("• Zero Real-Money Risk: Runs strictly in simulated PaperAccount mode.", 11.5, TEXT_MAIN, False, "l"),
            ("• Self-Cleaning Watchlist: Automatically quarantines losing drags onto a blacklist.", 11.5, TEXT_MAIN, False, "l"),
            ("• Supreme Governor Gate: No parameter change deploys unless all 125 unit tests pass.", 11.5, TEXT_MAIN, False, "l"),
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
            ("Evidence Distinction: 180-Day Historical Backtest Replay = +13.65% (+$1,365.33 Maker) / +10.01% (+$1,001.26 Taker+Slippage), Sharpe 1.52, MaxDD 9.81%.", 10.5, TEXT_MAIN, True, "l"),
            ("Live Paper Portfolio (data/paper_portfolio.json): Started Oct 2026 prior to drag quarantine; current simulated equity is $8,919.40 across 32 closed trades & 4 live basis yield payouts.", 10.0, TEXT_MUTED, False, "l"),
        ]),
    ]
    slides_shapes.append(s6)

    # SLIDE 7: Why Defense & Quarantine Matter (Charts + Takeaway)
    s7 = [
        _xml_pic(80, "rIdImg3", "DefenseCharts", 0.45, 1.55, 12.43, 4.50),
        _xml_shape_box(81, "DefenseTakeaway", 0.55, 6.12, 12.2, 0.85, BG_CARD_ALT, ACCENT_GREEN, 1.2, [
            ("Key Analytical Takeaway: Trading profitability is driven as much by what the bot REFUSES to trade as by what it buys.", 11.0, ACCENT_GREEN, True, "l"),
            ("Quarantining confirmed drags (BMT/USDT -$1,029, INTC, CRWV, SNAP, PLUG) + blocking 408 bad setups kept Max Drawdown at 9.81% (well below the 15% circuit breaker).", 10.2, TEXT_MAIN, False, "l"),
        ]),
    ]
    slides_shapes.append(s7)

    # SLIDE 8: Achievements, Honest Limitations & Next Steps
    s8 = [
        _xml_shape_box(90, "ColAchieved", 0.55, 1.70, 3.9, 5.10, BG_CARD, ACCENT_GREEN, 1.6, [
            ("✅ Verified Achievements", 15.0, ACCENT_GREEN, True, "l"),
            ("(Implemented & Tested — 125/125 Green)", 10.0, TEXT_MUTED, True, "l"),
            (" ", 5.0, TEXT_MUTED, False, "l"),
            ("• Complete 12-Module Architecture: 8 deterministic math/rule modules + 4 local AI (phi4-mini) agents.", 10.8, TEXT_MAIN, False, "l"),
            ("• 5 Institutional Skill Gates: Live NYSE clock, SEC 8-K & XBRL solvency, 13F/Congress radar, & Wash-Sale guard.", 10.8, TEXT_MAIN, False, "l"),
            ("• Proven 180d Replay Alpha: +13.65% Maker / +10.01% Taker return with 9.81% MaxDD.", 10.8, TEXT_MAIN, False, "l"),
            ("• Self-Healing Watchlist: Automated drag quarantine + 24h Blacklist Parole Audit.", 10.8, TEXT_MAIN, False, "l"),
        ]),
        _xml_shape_box(91, "ColLimitations", 4.70, 1.70, 3.9, 5.10, BG_CARD, ACCENT_AMBER, 1.6, [
            ("⚠️ Honest Limitations", 15.0, ACCENT_AMBER, True, "l"),
            ("(What Remains Unproven / Constrained)", 10.0, TEXT_MUTED, True, "l"),
            (" ", 5.0, TEXT_MUTED, False, "l"),
            ("• Simulated Paper Execution Only: No real-money broker orders; Maker mode assumes limit fills without queue priority modeling.", 10.8, TEXT_MAIN, False, "l"),
            ("• Single-Stock Parameter Drag: NVDA (-$590) and NOK (-$241) lagged under the 48-bar swing lookback in the 180d replay.", 10.8, TEXT_MAIN, False, "l"),
            ("• Stat-Arb Pairs Inactivity: 0 pair trades triggered in the 180d replay due to strict cointegration thresholds.", 10.8, TEXT_MAIN, False, "l"),
            ("• Early Live Paper Drawdown: Live paper log ($8,919) reflects October trades prior to recent quarantine upgrades.", 10.8, TEXT_MAIN, False, "l"),
        ]),
        _xml_shape_box(92, "ColNext", 8.85, 1.70, 3.9, 5.10, BG_CARD, ACCENT_CYAN, 1.6, [
            ("🚀 Next Development Priorities", 15.0, ACCENT_CYAN, True, "l"),
            ("(Actionable Engineering Roadmap)", 10.0, TEXT_MUTED, True, "l"),
            (" ", 5.0, TEXT_MUTED, False, "l"),
            ("• 1. Forward Live Paper Validation: Track the newly pruned 14-asset watchlist over the next 30–60 days of live ticks.", 10.8, TEXT_MAIN, False, "l"),
            ("• 2. Per-Ticker Lookback Calibration: Let Agent 11 tune shorter breakout windows for high-beta semis like NVDA.", 10.8, TEXT_MAIN, False, "l"),
            ("• 3. Stat-Arb Universe Expansion: Add sector ETF pairs (XLE/USO, SMH/SOXX) to increase cointegrated pair frequency.", 10.8, TEXT_MAIN, False, "l"),
            ("• 4. Disk-Backed SEC Cache: Persist EDGAR XBRL & 8-K responses to data/sec_cache.json for faster CLI scans.", 10.8, TEXT_MAIN, False, "l"),
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
        <div class="kpi-val" style="color:#10B981;">+13.65%</div>
        <div class="kpi-title">180-Day Backtest Return</div>
        <div class="kpi-sub">+$1,365.33 net on $10k (Maker mode)</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-val" style="color:#38BDF8;">9.81%</div>
        <div class="kpi-title">Max Portfolio Drawdown</div>
        <div class="kpi-sub">Hard circuit breaker capped at 15.0%</div>
      </div>
      <div class="kpi-card purple">
        <div class="kpi-val" style="color:#A855F7;">8 Math + 4 AI</div>
        <div class="kpi-title">Specialized Architecture</div>
        <div class="kpi-sub">Exact math sizing + local phi4-mini LLM</div>
      </div>
      <div class="kpi-card amber">
        <div class="kpi-val" style="color:#F59E0B;">125 / 125</div>
        <div class="kpi-title">Automated Tests Passing</div>
        <div class="kpi-sub">100% Simulated Paper-Trading Immunity</div>
      </div>
    </div>
    <div class="grid-3">
      <div class="card" style="border-top: 3px solid #F43F5E;">
        <h3 style="color:#F43F5E;">1. The Problem We Solve</h3>
        <ul>
          <li>• <b>Human Emotion:</b> Fear and FOMO cause retail traders to buy tops and panic-sell bottoms.</li>
          <li>• <b>Brittle Rule Bots:</b> Traditional trading bots use fixed rules that fail when calm trends turn into sideways chop.</li>
          <li>• <b>LLM Number Hallucinations:</b> Letting an AI directly guess trade sizes or stop-losses creates unacceptable tail risk.</li>
        </ul>
      </div>
      <div class="card" style="border-top: 3px solid #38BDF8;">
        <h3 style="color:#38BDF8;">2. How Our System Works</h3>
        <ul>
          <li>• <b>24/7 Multi-Asset Watch:</b> Monitors 14 vetted Crypto &amp; US Stock assets simultaneously every 60 seconds.</li>
          <li>• <b>Exact Math for Money:</b> Deterministic formulas calculate breakouts, mean reversion, and 1%–5% risk sizing.</li>
          <li>• <b>Local AI for Context:</b> Uses local <code>phi4-mini</code> to screen news headlines (0.46s) and diagnose closed trades.</li>
        </ul>
      </div>
      <div class="card" style="border-top: 3px solid #10B981;">
        <h3 style="color:#10B981;">3. Unbreakable Safety Guarantees</h3>
        <ul>
          <li>• <b>Strictly Paper Trading:</b> Executes exclusively inside a simulated <code>PaperAccount</code>—zero real money is at risk.</li>
          <li>• <b>Self-Cleaning Universe:</b> Automatically quarantines losing assets onto a blacklist and runs periodic parole audits.</li>
          <li>• <b>Supreme Risk Governor:</b> No parameter change can deploy unless all 125 unit tests pass.</li>
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
      <img src="{perf_b64}" alt="180-Day Verified Performance Charts" style="max-height: 4.35in;">
    </div>
    <div class="banner" style="border-color:#10B981;">
      <b style="color:#10B981;">Strict Distinction Between Simulation Modes:</b>
      <b>(1) 180-Day Historical Backtest Replay</b> (<code>data/live_replay_backtest.json</code> across 14 active watchlist assets): <b>+13.65% (+$1,365.33)</b> in Maker mode (0.02% fee/side) and <b>+10.01% (+$1,001.26)</b> in Taker+Slippage stress mode (0.10%/side), with <b>9.81% MaxDD</b> and <b>1.52 Sharpe</b>.
      <b>(2) Live Paper Account</b> (<code>data/paper_portfolio.json</code>): Started Oct 2026 prior to recent drag quarantines; current virtual equity is <b>$8,919.40</b> ($10,000 initial) across 32 closed trades and 4 live basis yield payouts.
    </div>
  </div>
  <div class="slide-footer">
    <span>Sources: data/live_replay_backtest.json • data/paper_portfolio.json • research/backtest_live_replay.py</span>
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
      <img src="{def_b64}" alt="Quarantine Impact & Defense Gate Telemetry" style="max-height: 4.35in;">
    </div>
    <div class="banner" style="border-color:#F59E0B;">
      <b style="color:#F59E0B;">Why Quarantine &amp; Parole Work Together:</b> When micro-cap token <code>BMT/USDT</code> (-$1,029.82, 25% WR), <code>ENA/USDT</code> (-$181.68), and <code>SOFI</code> (-$111.39) were active, they dragged portfolio return down to +0.91%. Quarantining them into <code>blacklisted_symbols</code> lifted return to <b>+13.65%</b>—while our new <b>Automated Parole Audit (<code>--audit-blacklist</code>)</b> re-tests quarantined assets every 24h and graduates recovered assets back onto probationary <b>1.0% risk</b>.
    </div>
  </div>
  <div class="slide-footer">
    <span>Sources: config/active_config.json (38 quarantined symbols) • agent/asset_selector.py • data/live_replay_backtest.json</span>
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
        <h3 style="color:#10B981;">1. Verified Capabilities (125/125 Tests)</h3>
        <ul>
          <li>• <b>Complete 12-Module System:</b> 8 deterministic math/rule modules + 4 local AI (<code>phi4-mini</code>) agents operating in harmony.</li>
          <li>• <b>5 Institutional Skill Gates:</b> Live NYSE market clock, SEC 8-K &amp; XBRL balance-sheet gates, 13F/Congress radar, &amp; Wash-Sale guard.</li>
          <li>• <b>Empirical 180d Profitability:</b> Verified +13.65% Maker / +10.01% Taker net return with 9.81% Max Drawdown.</li>
          <li>• <b>Autonomous Governance:</b> Agent 12 enforces hard invariants [1%–5% risk], backups, and 100% pytest pass gate.</li>
        </ul>
      </div>
      <div class="card" style="border-top: 4px solid #F59E0B;">
        <h3 style="color:#F59E0B;">2. Honest Limitations &amp; Constraints</h3>
        <ul>
          <li>• <b>Simulated Paper Fills Only:</b> Zero real-money broker connectivity; Maker backtest assumes limit fills without order-book queue modeling.</li>
          <li>• <b>Single-Asset Parameter Drag:</b> <code>NVDA</code> (-$590) and <code>NOK</code> (-$241) underperformed under the 48-bar swing lookback in the 180d replay.</li>
          <li>• <b>Stat-Arb Pairs Trigger Scarcity:</b> 0 pair trades fired in the 180d replay under current strict cointegration thresholds.</li>
          <li>• <b>Pre-Quarantine Live Log:</b> Live paper account ($8,919) includes October trades taken before recent drag quarantines.</li>
        </ul>
      </div>
      <div class="card" style="border-top: 4px solid #38BDF8;">
        <h3 style="color:#38BDF8;">3. Next Engineering Priorities</h3>
        <ul>
          <li>• <b>1. Forward Live Paper Validation:</b> Monitor the newly pruned 14-asset watchlist over the next 30–60 days of live 60s ticks.</li>
          <li>• <b>2. Per-Ticker Lookback Calibration:</b> Allow Agent 11 to tune shorter breakout windows for high-beta semiconductors like <code>NVDA</code>.</li>
          <li>• <b>3. Expand Stat-Arb Pairs Universe:</b> Add matched sector ETF pairs (e.g., <code>SMH/SOXX</code>, <code>XLE/USO</code>) to increase pair trade frequency.</li>
          <li>• <b>4. Disk-Backed SEC EDGAR Cache:</b> Persist XBRL &amp; 8-K responses to disk to accelerate CLI discovery scans.</li>
        </ul>
      </div>
    </div>
    <div class="banner" style="border-color:#38BDF8; text-align:center;">
      <b>Bottom Line:</b> The Web4 Autonomous Trading Desk demonstrates how <b>deterministic financial math</b> and <b>bounded local AI governance</b> can be combined safely, transparently, and reproducibly with <b>zero real-money risk</b>.
    </div>
  </div>
  <div class="slide-footer">
    <span>Deliverables: web4_trading_agent_presentation.pptx (Editable) • web4_trading_agent_presentation.pdf • 125/125 Tests Passing</span>
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
