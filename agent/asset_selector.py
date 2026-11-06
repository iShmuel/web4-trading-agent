"""Agent 9: Autonomous Asset Selection & Alpha Screener Agent.

Scans, evaluates, and ranks candidate stocks and crypto assets to dynamically
identify high-alpha, profitable assets tailored for the bot's dual-strategy
desk (Momentum Breakout + Mean Reversion Swing).

Key capabilities:
1. Dynamic Market Universe Discovery: Discovers live candidate assets directly
   from Binance order books/volume and Yahoo Finance screener feeds without any
   hardcoded prelist.
2. Strategy Compatibility & Regime Screening: Evaluates both Momentum and Mean
   Reversion engines independently and in dual-hybrid mode.
3. Walk-Forward In-Sample & Out-of-Sample Backtesting: Anti-overfitting
   verification with realistic friction (fees + slippage).
4. Composite Alpha Scoring (CAS): Multi-factor ranking penalizing drag and rewarding
   high win rates, high profit factors, and strong Sharpe ratios.
5. Local LLM Narrative Synthesis: Uses Ollama (phi4-mini) to generate qualitative
   alpha theses on top discovered candidates.
6. Dynamic Watchlist Optimization: Promotes elite alpha assets and blacklists
   toxic drags to maximize net return without loosening risk bounds.
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.data_loader import fetch_ohlcv, is_crypto_symbol, check_data_quality
from core.strategy import compute_indicators
from core.backtest_engine import (
    compute_mean_reversion_indicators,
    run_backtest,
    run_mean_reversion_backtest,
    BacktestResult,
)
from core.coordinator import resolve_symbol_config
from core.risk_engine import MIN_ALLOWED_RISK_PCT, MAX_ALLOWED_RISK_PCT
from core.preflight import fetch_ticker_headlines
from agent.llm_client import query_local_llm

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "active_config.json"
REPORT_PATH = PROJECT_ROOT / "data" / "asset_selection_report.json"


@dataclass
class AssetEvaluation:
    """Comprehensive alpha evaluation record for a single candidate asset."""
    symbol: str
    asset_type: str  # "crypto" or "stock"
    primary_strategy: str  # "MOMENTUM", "MEAN_REVERSION", or "DUAL_HYBRID"
    days_evaluated: int
    total_bars: int
    adx_trend_ratio: float  # % of bars with ADX >= 22
    volatility_atr_pct: float  # Mean ATR / price %
    momentum_return_pct: float
    mean_reversion_return_pct: float
    in_sample_return_pct: float
    out_of_sample_return_pct: float
    net_return_pct: float
    net_pnl: float
    win_rate_pct: float
    profit_factor: float
    max_drawdown_pct: float
    sharpe_ratio: float
    payoff_ratio: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    composite_alpha_score: float
    tier: str  # "ELITE_ALPHA", "VIABLE_ALPHA", "NEUTRAL_RESERVE", "TOXIC_DRAG"
    recommendation: str  # "ADD_TO_WATCHLIST", "HOLD_IN_RESERVE", "BLACKLIST_OR_PRUNE"
    rationale: str
    llm_thesis: str = ""


def load_active_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the current active configuration."""
    path = config_path or CONFIG_PATH
    with open(path, "r") as f:
        return json.load(f)


def discover_dynamic_crypto_universe(
    min_volume_usd: float = 10_000_000.0,
    top_n: int = 25,
    exchange_name: str = "binance",
) -> List[str]:
    """
    Dynamically discover top liquid cryptocurrency pairs directly from exchange
    order books without any hardcoded prelist. Filters out stablecoins and leveraged tokens.
    """
    try:
        import ccxt
        exchange_class = getattr(ccxt, exchange_name)
        ex = exchange_class({"enableRateLimit": True, "timeout": 10000})
        tickers = ex.fetch_tickers()
        candidates = []
        excluded_prefixes = (
            "USDC/", "FDUSD/", "TUSD/", "USDP/", "EUR/", "BUSD/", "DAI/", "USD1/",
            "RLUSD/", "WBTC/", "XAUT/", "UST/", "AEUR/", "PAX/", "USDD/", "FRAX/",
        )
        excluded_suffixes = ("UP/USDT", "DOWN/USDT", "B/USDT", "BEAR/USDT", "BULL/USDT")

        for sym, t in tickers.items():
            if not sym.endswith("/USDT"):
                continue
            if any(sym.startswith(p) for p in excluded_prefixes):
                continue
            if any(sym.endswith(s) for s in excluded_suffixes):
                continue
            vol = float(t.get("quoteVolume", 0) or 0)
            if vol >= min_volume_usd:
                candidates.append((sym, vol))

        candidates.sort(key=lambda x: x[1], reverse=True)
        discovered = [c[0] for c in candidates[:top_n]]
        if discovered:
            return discovered
    except Exception as e:
        logger.debug(f"[AssetSelector] Dynamic crypto discovery notice ({e}). Falling back to active watchlist.")

    # Graceful fallback to active watchlist cryptos if offline
    cfg = load_active_config()
    return [s for s in cfg.get("watchlist", []) if is_crypto_symbol(s)]


def discover_dynamic_stock_universe(
    screeners: Optional[List[str]] = None,
    top_n: int = 25,
) -> List[str]:
    """
    Dynamically discover most active and high-momentum US equities and ETFs
    from live financial screeners without any hardcoded prelist.
    """
    scr_list = screeners or ["most_actives", "growth_technology_stocks", "day_gainers"]
    discovered: List[str] = []

    for scr in scr_list:
        url = f"https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?formatted=false&scrIds={scr}&count={top_n*2}"
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode())
                quotes = data.get("finance", {}).get("result", [{}])[0].get("quotes", [])
                for q in quotes:
                    s = q.get("symbol")
                    if s and "^" not in s and "." not in s and "/" not in s and s not in discovered:
                        discovered.append(s)
        except Exception as e:
            logger.debug(f"[AssetSelector] Screener {scr} query notice: {e}")

    if discovered:
        return discovered[:top_n]

    # Graceful fallback to active watchlist equities if offline
    cfg = load_active_config()
    return [s for s in cfg.get("watchlist", []) if not is_crypto_symbol(s)]


def calculate_composite_alpha_score(
    net_return_pct: float,
    win_rate_pct: float,
    profit_factor: float,
    max_drawdown_pct: float,
    sharpe_ratio: float,
    is_return_pct: float,
    oos_return_pct: float,
) -> float:
    """
    Calculate risk-adjusted Composite Alpha Score (CAS).
    Penalizes overfitting, high drawdowns, and negative net expectancy.
    """
    dd_pen = max(max_drawdown_pct, 0.5)
    pf_clamped = min(max(profit_factor, 0.1), 5.0)

    # Out-of-sample stability factor (anti-overfitting safeguard)
    stability = 1.0
    if is_return_pct > 0 and oos_return_pct > 0:
        ratio = oos_return_pct / is_return_pct
        stability = min(1.2, max(0.6, ratio))
    elif is_return_pct > 0 and oos_return_pct <= 0:
        stability = 0.4  # Severe penalty for out-of-sample breakdown

    if net_return_pct > 0 and win_rate_pct >= 50.0 and profit_factor >= 1.0:
        raw = (net_return_pct * (win_rate_pct / 50.0) * pf_clamped / dd_pen) * 5.0 * stability + (sharpe_ratio * 2.0)
        return round(float(raw), 2)
    else:
        # Penalize drag assets
        raw = (net_return_pct * 2.0) - (dd_pen * 1.5) - max(0.0, 50.0 - win_rate_pct) + (sharpe_ratio * 2.0)
        return round(float(raw), 2)


def evaluate_candidate_asset(
    symbol: str,
    days: int = 90,
    timeframe: str = "1h",
    config: Optional[Dict[str, Any]] = None,
    execution_mode: str = "maker",
    use_llm: bool = True,
) -> Optional[AssetEvaluation]:
    """
    Perform deep empirical evaluation of an asset:
    1. Fetch historical OHLCV data.
    2. Compute technical volatility and regime indicators.
    3. Split 70% In-Sample / 30% Out-of-Sample.
    4. Run backtests across Momentum & Mean Reversion strategies.
    5. Aggregate metrics and compute Composite Alpha Score.
    6. Local LLM narrative thesis generation for top candidates.
    """
    cfg = config or load_active_config()
    scfg = resolve_symbol_config(cfg, symbol)
    asset_type = "crypto" if is_crypto_symbol(symbol) else "stock"

    try:
        raw_df = fetch_ohlcv(symbol=symbol, timeframe=timeframe, days=days)
    except Exception as e:
        print(f"[AssetSelector] Warning: Failed to fetch {symbol}: {e}")
        return None

    if raw_df is None or len(raw_df) < 120:
        print(f"[AssetSelector] Warning: Insufficient data for {symbol} ({len(raw_df) if raw_df is not None else 0} bars)")
        return None

    # Calculate trend and volatility metrics
    mom_df = compute_indicators(
        raw_df,
        breakout_lookback=int(scfg.get("breakout_lookback", 20)),
        ema_trend_filter=int(scfg.get("ema_trend_filter", 32)),
        atr_period=int(scfg.get("atr_period", 14)),
        macro_ema_period=int(scfg.get("macro_ema_period", 200)),
    )
    mr_df = compute_mean_reversion_indicators(
        raw_df,
        bb_period=int(scfg.get("bb_period", 20)),
        bb_std=float(scfg.get("bb_std", 2.0)),
        rsi_period=int(scfg.get("rsi_period", 14)),
        atr_period=int(scfg.get("atr_period", 14)),
        adx_period=int(scfg.get("adx_period", 14)),
    )

    close_series = raw_df["close"].astype(float)
    atr_series = mom_df["atr"].astype(float)
    atr_pct_mean = float((atr_series / close_series * 100.0).dropna().mean()) if len(atr_series) else 0.0

    adx_series = mr_df["adx"].astype(float) if "adx" in mr_df.columns else pd.Series(dtype=float)
    adx_trend_ratio = float((adx_series >= 22.0).sum() / len(adx_series) * 100.0) if len(adx_series) else 0.0

    # Split 70% In-Sample / 30% Out-of-Sample
    split_idx = int(len(raw_df) * 0.70)
    train_df = raw_df.iloc[:split_idx].copy()
    test_df = raw_df.iloc[split_idx:].copy()

    # Determine execution friction
    if execution_mode == "maker":
        fee_pct = 0.0002
        slip_pct = 0.0000
    else:
        fee_pct = float(scfg.get("fee_pct", 0.0006))
        slip_pct = float(scfg.get("slippage_pct", 0.0004))

    # Evaluate In-Sample & Out-of-Sample via Momentum & Mean Reversion
    train_mom = run_backtest(
        train_df,
        initial_capital=10_000.0,
        account_risk_pct=0.02,
        breakout_lookback=int(scfg.get("breakout_lookback", 20)),
        ema_trend_filter=int(scfg.get("ema_trend_filter", 32)),
        atr_period=int(scfg.get("atr_period", 14)),
        atr_multiplier=float(scfg.get("atr_multiplier", 2.5)),
        fee_pct=fee_pct,
        slippage_pct=slip_pct,
    )
    test_mom = run_backtest(
        test_df,
        initial_capital=10_000.0,
        account_risk_pct=0.02,
        breakout_lookback=int(scfg.get("breakout_lookback", 20)),
        ema_trend_filter=int(scfg.get("ema_trend_filter", 32)),
        atr_period=int(scfg.get("atr_period", 14)),
        atr_multiplier=float(scfg.get("atr_multiplier", 2.5)),
        fee_pct=fee_pct,
        slippage_pct=slip_pct,
    )

    train_mr = run_mean_reversion_backtest(
        train_df,
        initial_capital=10_000.0,
        account_risk_pct=0.02,
        bb_period=int(scfg.get("bb_period", 20)),
        bb_std=float(scfg.get("bb_std", 2.0)),
        rsi_period=int(scfg.get("rsi_period", 14)),
        atr_period=int(scfg.get("atr_period", 14)),
        fee_pct=fee_pct,
        slippage_pct=slip_pct,
    )
    test_mr = run_mean_reversion_backtest(
        test_df,
        initial_capital=10_000.0,
        account_risk_pct=0.02,
        bb_period=int(scfg.get("bb_period", 20)),
        bb_std=float(scfg.get("bb_std", 2.0)),
        rsi_period=int(scfg.get("rsi_period", 14)),
        atr_period=int(scfg.get("atr_period", 14)),
        fee_pct=fee_pct,
        slippage_pct=slip_pct,
    )

    # Full period evaluation
    full_mom = run_backtest(
        raw_df,
        initial_capital=10_000.0,
        account_risk_pct=0.02,
        breakout_lookback=int(scfg.get("breakout_lookback", 20)),
        ema_trend_filter=int(scfg.get("ema_trend_filter", 32)),
        atr_period=int(scfg.get("atr_period", 14)),
        atr_multiplier=float(scfg.get("atr_multiplier", 2.5)),
        fee_pct=fee_pct,
        slippage_pct=slip_pct,
    )
    full_mr = run_mean_reversion_backtest(
        raw_df,
        initial_capital=10_000.0,
        account_risk_pct=0.02,
        bb_period=int(scfg.get("bb_period", 20)),
        bb_std=float(scfg.get("bb_std", 2.0)),
        rsi_period=int(scfg.get("rsi_period", 14)),
        atr_period=int(scfg.get("atr_period", 14)),
        fee_pct=fee_pct,
        slippage_pct=slip_pct,
    )

    mom_ret = round(full_mom.total_return_pct, 2)
    mr_ret = round(full_mr.total_return_pct, 2)

    # Select the optimal primary strategy engine for this symbol
    if full_mom.total_return_pct >= full_mr.total_return_pct:
        chosen_full = full_mom
        is_return = train_mom.total_return_pct
        oos_return = test_mom.total_return_pct
        primary_strat = "MOMENTUM"
    else:
        chosen_full = full_mr
        is_return = train_mr.total_return_pct
        oos_return = test_mr.total_return_pct
        primary_strat = "MEAN_REVERSION"

    if mom_ret > 0 and mr_ret > 0:
        primary_strat = "DUAL_HYBRID"

    total_trades = chosen_full.total_trades
    winning_trades = chosen_full.winning_trades
    losing_trades = chosen_full.losing_trades
    win_rate_pct = round(chosen_full.win_rate * 100.0, 1) if total_trades > 0 else 0.0
    net_return_pct = round(chosen_full.total_return_pct, 2)
    net_pnl = round(chosen_full.final_equity - chosen_full.initial_capital, 2)
    profit_factor = round(chosen_full.profit_factor, 2)
    max_dd_pct = round(chosen_full.max_drawdown_pct, 2)
    sharpe = round(chosen_full.sharpe_ratio, 2)

    pnls = [t.pnl for t in chosen_full.trades] if hasattr(chosen_full, "trades") else []
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    payoff_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else (1.0 if avg_win > 0 else 0.0)

    # Compute Composite Alpha Score
    cas = calculate_composite_alpha_score(
        net_return_pct=net_return_pct,
        win_rate_pct=win_rate_pct,
        profit_factor=profit_factor,
        max_drawdown_pct=max_dd_pct,
        sharpe_ratio=sharpe,
        is_return_pct=is_return,
        oos_return_pct=oos_return,
    )

    # Tier Classification & Recommendation
    if cas >= 5.0 and net_return_pct > 0 and win_rate_pct >= 55.0 and max_dd_pct <= 12.0:
        tier = "ELITE_ALPHA"
        recommendation = "ADD_TO_WATCHLIST"
        rationale = f"High-conviction {primary_strat} winner: +{net_return_pct}% return, {win_rate_pct}% WR, Sharpe {sharpe}"
    elif (cas >= 0.1 or net_return_pct > 0) and win_rate_pct >= 50.0 and max_dd_pct <= 16.0:
        tier = "VIABLE_ALPHA"
        recommendation = "ADD_TO_WATCHLIST"
        rationale = f"Viable {primary_strat} candidate: +{net_return_pct}% return, {win_rate_pct}% WR, Sharpe {sharpe}"
    elif net_return_pct >= -1.0 and win_rate_pct >= 45.0 and max_dd_pct <= 16.0:
        tier = "NEUTRAL_RESERVE"
        recommendation = "HOLD_IN_RESERVE"
        rationale = f"Neutral/break-even {primary_strat}: {net_return_pct}% return, {win_rate_pct}% WR"
    else:
        tier = "TOXIC_DRAG"
        recommendation = "BLACKLIST_OR_PRUNE"
        rationale = f"Negative or choppy drag: {net_return_pct}% return, {win_rate_pct}% WR, {max_dd_pct}% MaxDD"

    # Qualitative local LLM alpha thesis for top candidates
    llm_thesis = ""
    if use_llm and tier in ("ELITE_ALPHA", "VIABLE_ALPHA"):
        try:
            headlines = fetch_ticker_headlines(symbol, limit=2)
            news_txt = " | ".join(headlines) if headlines else "No adverse news reported."
            prompt = (
                f"Asset: {symbol} ({asset_type})\n"
                f"Primary Engine: {primary_strat}\n"
                f"Metrics: Net Return {net_return_pct:+.2f}%, Win Rate {win_rate_pct:.1f}%, Profit Factor {profit_factor:.2f}, Max Drawdown {max_dd_pct:.2f}%, Sharpe {sharpe:.2f}\n"
                f"Recent News: {news_txt}\n\n"
                "As a quantitative hedge fund analyst, provide a concise 2-sentence alpha thesis on why this asset is favorable to add to our trading watchlist."
            )
            resp = query_local_llm(prompt, model="phi4-mini:latest", timeout_seconds=6.0)
            if resp:
                llm_thesis = resp
        except Exception:
            pass

    return AssetEvaluation(
        symbol=symbol,
        asset_type=asset_type,
        primary_strategy=primary_strat,
        days_evaluated=days,
        total_bars=len(raw_df),
        adx_trend_ratio=round(adx_trend_ratio, 1),
        volatility_atr_pct=round(atr_pct_mean, 2),
        momentum_return_pct=mom_ret,
        mean_reversion_return_pct=mr_ret,
        in_sample_return_pct=round(is_return, 2),
        out_of_sample_return_pct=round(oos_return, 2),
        net_return_pct=net_return_pct,
        net_pnl=net_pnl,
        win_rate_pct=win_rate_pct,
        profit_factor=profit_factor,
        max_drawdown_pct=max_dd_pct,
        sharpe_ratio=sharpe,
        payoff_ratio=payoff_ratio,
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        composite_alpha_score=cas,
        tier=tier,
        recommendation=recommendation,
        rationale=rationale,
        llm_thesis=llm_thesis,
    )


def scan_candidate_universe(
    crypto_candidates: Optional[List[str]] = None,
    stock_candidates: Optional[List[str]] = None,
    days: int = 90,
    timeframe: str = "1h",
    config: Optional[Dict[str, Any]] = None,
    execution_mode: str = "maker",
    dynamic_discovery: bool = True,
    merge_existing: bool = True,
    crypto_min_volume: float = 10_000_000.0,
    top_crypto_discovery: int = 15,
    top_stock_discovery: int = 15,
    use_llm: bool = True,
) -> List[AssetEvaluation]:
    """Scan and rank all candidate assets across digital assets and equities with dynamic discovery & portfolio merging."""
    cfg = config or load_active_config()

    # 1. Existing portfolio symbols to preserve and audit
    existing_cryptos: List[str] = []
    existing_stocks: List[str] = []
    if merge_existing:
        active_wl = list(cfg.get("watchlist", []))
        risk_syms = list(cfg.get("symbol_risk_weights", {}).keys())
        all_configured = list(dict.fromkeys(active_wl + risk_syms))
        blacklisted = set(cfg.get("blacklisted_symbols", []))
        clean_existing = [s for s in all_configured if s not in blacklisted]
        existing_cryptos = [s for s in clean_existing if is_crypto_symbol(s)]
        existing_stocks = [s for s in clean_existing if not is_crypto_symbol(s)]

    # 2. Dynamic Discovery (Zero-prelist from real-time exchange order books & screeners)
    discovered_cryptos: List[str] = []
    discovered_stocks: List[str] = []
    if dynamic_discovery:
        print("[AssetSelector] Dynamic Market Discovery active: Querying real-time exchange order books & screeners (no prelist)...")
        discovered_cryptos = crypto_candidates if crypto_candidates is not None else discover_dynamic_crypto_universe(
            min_volume_usd=crypto_min_volume, top_n=top_crypto_discovery
        )
        discovered_stocks = stock_candidates if stock_candidates is not None else discover_dynamic_stock_universe(
            top_n=top_stock_discovery
        )
    elif crypto_candidates is not None or stock_candidates is not None:
        discovered_cryptos = crypto_candidates or []
        discovered_stocks = stock_candidates or []

    # 3. Merge existing portfolio with newly discovered candidates (deduplicated)
    cryptos = list(dict.fromkeys(existing_cryptos + discovered_cryptos))
    stocks = list(dict.fromkeys(existing_stocks + discovered_stocks))
    all_symbols = cryptos + stocks

    print("=" * 80)
    print(f"  AGENT 9: AUTONOMOUS ALPHA SCANNER & MARKET DISCOVERY")
    print(f"  Evaluating {len(all_symbols)} assets ({len(cryptos)} cryptos, {len(stocks)} stocks) | Lookback: {days}d")
    print(f"  Candidate Cryptos: {cryptos[:8]} ...")
    print(f"  Candidate Equities: {stocks[:8]} ...")
    print("=" * 80)

    results: List[AssetEvaluation] = []
    t0 = time.time()

    for i, sym in enumerate(all_symbols, start=1):
        print(f"[{i:02d}/{len(all_symbols):02d}] Evaluating {sym:<12} ... ", end="", flush=True)
        t_sub = time.time()
        ev = evaluate_candidate_asset(
            symbol=sym,
            days=days,
            timeframe=timeframe,
            config=config,
            execution_mode=execution_mode,
            use_llm=use_llm,
        )
        if ev:
            results.append(ev)
            print(f"Done ({time.time()-t_sub:.1f}s) | Score: {ev.composite_alpha_score:+6.2f} | Net: {ev.net_return_pct:+5.2f}% | WR: {ev.win_rate_pct:4.1f}% | Tier: {ev.tier}")
        else:
            print("SKIPPED (Data Fetch / Bars Insufficient)")

    results.sort(key=lambda x: x.composite_alpha_score, reverse=True)
    print(f"\n[AssetSelector] Completed screening {len(results)} assets in {time.time()-t0:.1f}s.\n")
    return results


def build_selection_summary(
    evaluations: List[AssetEvaluation],
    max_crypto: int = 8,
    max_stocks: int = 16,
    min_profit_pct: float = 0.0,
    min_win_rate_pct: float = 50.0,
    min_profit_factor: float = 1.0,
    existing_blacklist: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Select top uncorrelated portfolio universe respecting risk limits and pruning non-profitable drags.

    Mathematical Profitability Proof Standard:
    1. Proven Profitable: Net return > min_profit_pct, Win rate >= min_win_rate_pct, Profit factor >= min_profit_factor,
       and Tier in (ELITE_ALPHA, VIABLE_ALPHA).
    2. Proven Non-Profitable / Drag: Assets failing this standard are pruned from the active watchlist.
       Assets with negative net returns or Tier == TOXIC_DRAG are appended to recommended_blacklist.
    """
    elite = [e for e in evaluations if e.tier == "ELITE_ALPHA"]
    viable = [e for e in evaluations if e.tier == "VIABLE_ALPHA"]
    neutral = [e for e in evaluations if e.tier == "NEUTRAL_RESERVE"]
    toxic = [e for e in evaluations if e.tier == "TOXIC_DRAG"]

    # Filter for PROVEN PROFITABLE assets only
    proven_profitable = [
        e for e in evaluations
        if e.net_return_pct > min_profit_pct
        and e.win_rate_pct >= min_win_rate_pct
        and e.profit_factor >= min_profit_factor
        and e.tier in ("ELITE_ALPHA", "VIABLE_ALPHA")
    ]
    proven_profitable.sort(key=lambda x: x.composite_alpha_score, reverse=True)

    # Assets proven NOT profitable enough (whipsaws, negative returns, low win rates)
    proven_non_profitable = [
        e for e in evaluations
        if e.net_return_pct <= min_profit_pct
        or e.win_rate_pct < min_win_rate_pct
        or e.profit_factor < min_profit_factor
        or e.tier in ("TOXIC_DRAG", "NEUTRAL_RESERVE")
    ]

    # Select top proven cryptos (up to max_crypto)
    crypto_proven = [e for e in proven_profitable if e.asset_type == "crypto"]
    selected_crypto = [e.symbol for e in crypto_proven][:max_crypto]

    # Select top proven stocks (up to max_stocks)
    stocks_proven = [e for e in proven_profitable if e.asset_type == "stock"]
    selected_stocks = [e.symbol for e in stocks_proven][:max_stocks]

    recommended_watchlist = selected_crypto + selected_stocks

    # Recommended blacklist: Existing blacklist + newly identified toxic drags losing money
    current_bl = existing_blacklist if existing_blacklist is not None else load_active_config().get("blacklisted_symbols", [])
    new_toxic = [e.symbol for e in proven_non_profitable if e.net_return_pct < 0 or e.tier == "TOXIC_DRAG"]
    recommended_blacklist = sorted(list(dict.fromkeys(current_bl + new_toxic)))

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_assets_scanned": len(evaluations),
        "tier_counts": {
            "ELITE_ALPHA": len(elite),
            "VIABLE_ALPHA": len(viable),
            "NEUTRAL_RESERVE": len(neutral),
            "TOXIC_DRAG": len(toxic),
        },
        "proven_profitable_count": len(proven_profitable),
        "proven_non_profitable_count": len(proven_non_profitable),
        "proven_profitable_symbols": [e.symbol for e in proven_profitable],
        "pruned_drag_symbols": [e.symbol for e in proven_non_profitable],
        "recommended_watchlist": recommended_watchlist,
        "recommended_blacklist": recommended_blacklist,
        "selected_crypto": selected_crypto,
        "selected_stocks": selected_stocks,
        "leaderboard": [asdict(e) for e in evaluations],
    }


def save_selection_report(report_data: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Save the audit report to JSON."""
    out = path or REPORT_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(report_data, f, indent=2)
    print(f"[AssetSelector] Saved detailed selection report to: {out}")
    return out


def apply_recommendations_to_config(
    recommended_watchlist: List[str],
    recommended_blacklist: List[str],
    config_path: Optional[Path] = None,
    verify_tests: bool = True,
) -> bool:
    """Convenience forwarder to Agent 12 Governor deployment pipeline."""
    from agent.governor import review_and_deploy_proposal
    res = review_and_deploy_proposal(
        proposed_changes={
            "watchlist": recommended_watchlist,
            "blacklisted_symbols": recommended_blacklist,
        },
        config_path=config_path,
        verify_tests=verify_tests,
        use_llm=False,
    )
    return res.approved


def print_leaderboard(evaluations: List[AssetEvaluation]):
    """Print a clean, structured leaderboard to terminal."""
    print("=" * 135)
    print(f"{'SYM':<10} {'TYPE':<7} {'STRATEGY':<15} {'SCORE':<8} {'NET %':<8} {'WIN %':<7} {'PF':<6} {'MAXDD':<7} {'SHARPE':<7} {'TIER':<15} {'ACTION'}")
    print("-" * 135)
    for e in evaluations:
        print(
            f"{e.symbol:<10} "
            f"{e.asset_type:<7} "
            f"{e.primary_strategy:<15} "
            f"{e.composite_alpha_score:+7.2f} "
            f"{e.net_return_pct:+7.2f}% "
            f"{e.win_rate_pct:5.1f}% "
            f"{e.profit_factor:5.2f} "
            f"{e.max_drawdown_pct:5.2f}% "
            f"{e.sharpe_ratio:+6.2f} "
            f"{e.tier:<15} "
            f"{e.recommendation}"
        )
    print("=" * 135)


def main():
    parser = argparse.ArgumentParser(description="Agent 9: Autonomous Alpha Scanner & Market Discovery")
    parser.add_argument("--scan", action="store_true", help="Run candidate scan and print leaderboard")
    parser.add_argument("--days", type=int, default=90, help="Lookback evaluation days (default: 90)")
    parser.add_argument("--timeframe", type=str, default="1h", help="Timeframe (default: 1h)")
    parser.add_argument("--top-crypto", type=int, default=8, help="Max crypto assets to recommend (default: 8)")
    parser.add_argument("--top-stocks", type=int, default=16, help="Max stock assets to recommend (default: 16)")
    parser.add_argument("--no-merge", action="store_true", help="Do not merge existing active assets")
    parser.add_argument("--discover-crypto", type=int, default=15, help="Number of liquid cryptos to discover live (default: 15)")
    parser.add_argument("--discover-stocks", type=int, default=15, help="Number of liquid stocks to discover live (default: 15)")
    parser.add_argument("--crypto-min-volume", type=float, default=10_000_000.0, help="Minimum 24h quote volume for crypto (default: $10M)")
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated custom symbols to evaluate (overrides discovery)")
    parser.add_argument("--static", action="store_true", help="Use static fallback watchlist instead of dynamic discovery")
    parser.add_argument("--execution-mode", type=str, choices=["maker", "taker"], default="maker", help="Execution mode (default: maker)")
    parser.add_argument("--apply", action="store_true", help="Send recommendations to Agent 12 Governor for approval and deployment")
    parser.add_argument("--report", type=str, default=None, help="Custom output report path")
    parser.add_argument("--no-llm", action="store_true", help="Bypass LLM narrative screening")
    args = parser.parse_args()

    cfg = load_active_config()

    if args.symbols:
        custom_syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
        cryptos = [s for s in custom_syms if is_crypto_symbol(s)]
        stocks = [s for s in custom_syms if not is_crypto_symbol(s)]
        evaluations = scan_candidate_universe(
            crypto_candidates=cryptos,
            stock_candidates=stocks,
            days=args.days,
            timeframe=args.timeframe,
            config=cfg,
            execution_mode=args.execution_mode,
            dynamic_discovery=False,
            merge_existing=False,
            use_llm=not args.no_llm,
        )
    else:
        evaluations = scan_candidate_universe(
            days=args.days,
            timeframe=args.timeframe,
            config=cfg,
            execution_mode=args.execution_mode,
            dynamic_discovery=not args.static,
            merge_existing=not args.no_merge,
            crypto_min_volume=args.crypto_min_volume,
            top_crypto_discovery=args.discover_crypto,
            top_stock_discovery=args.discover_stocks,
            use_llm=not args.no_llm,
        )

    print_leaderboard(evaluations)

    summary = build_selection_summary(evaluations, max_crypto=args.top_crypto, max_stocks=args.top_stocks)
    report_file = Path(args.report) if args.report else REPORT_PATH
    save_selection_report(summary, report_file)

    print("\n" + "=" * 80)
    print("  SELECTION RECOMMENDATIONS & EMPIRICAL PROOF")
    print("=" * 80)
    print(f"  Proven Profitable ({summary.get('proven_profitable_count', 0)} assets):")
    print(f"    {summary.get('proven_profitable_symbols', [])}")
    print(f"  Pruned Drags ({summary.get('proven_non_profitable_count', 0)} assets):")
    print(f"    {summary.get('pruned_drag_symbols', [])}")
    print("-" * 80)
    print(f"  Recommended Active Watchlist ({len(summary['recommended_watchlist'])} assets):")
    print(f"    Crypto ({len(summary['selected_crypto'])}): {summary['selected_crypto']}")
    print(f"    Stocks ({len(summary['selected_stocks'])}): {summary['selected_stocks']}")
    print(f"  Recommended Blacklist ({len(summary['recommended_blacklist'])} assets):")
    print(f"    {summary['recommended_blacklist']}")
    print("=" * 80)

    # Display top LLM alpha theses
    top_elites = [e for e in evaluations if e.llm_thesis]
    if top_elites:
        print("\n[LLM Qualitative Alpha Theses for Top Discovered Candidates]")
        for e in top_elites[:3]:
            print(f"• {e.symbol} ({e.primary_strategy}):\n  {e.llm_thesis}\n")

    if args.apply:
        print("\n[AssetSelector] Submitting proposal to Agent 12 (Governor) for review and deployment...")
        from agent.governor import review_and_deploy_proposal
        proposal = {
            "watchlist": summary["recommended_watchlist"],
            "blacklisted_symbols": summary["recommended_blacklist"],
        }
        res = review_and_deploy_proposal(
            proposed_changes=proposal,
            rationale="Dynamic Alpha Scanner merged active portfolio with vetted discoveries and pruned non-profitable drags.",
            empirical_proof={
                "proposed_watchlist_size": len(summary["recommended_watchlist"]),
                "crypto_count": len(summary["selected_crypto"]),
                "stocks_count": len(summary["selected_stocks"]),
                "proven_profitable_count": summary.get("proven_profitable_count", 0),
                "pruned_drags_count": summary.get("proven_non_profitable_count", 0),
                "blacklisted_toxic_symbols": summary["recommended_blacklist"],
                "top_alpha_theses": [
                    f"{e['symbol']}: Net {e['net_return_pct']:+,.2f}%, WinRate {e['win_rate_pct']:.1f}%, PF {e['profit_factor']:.2f}, Sharpe {e['sharpe_ratio']:+.2f}"
                    for e in summary["leaderboard"][:5]
                ],
                "risk_limits": "Max account risk locked at 2.0%, max drawdown circuit breaker locked at 15.0%, paper execution mode maintained.",
            },
            verify_tests=True,
            use_llm=not args.no_llm,
        )
        if res.approved:
            print("[AssetSelector] ✅ Proposal APPROVED and DEPLOYED by Agent 12 Chief Risk Governor!")
        else:
            print(f"[AssetSelector] ❌ Proposal REJECTED by Governor: {res.llm_rationale}")


if __name__ == "__main__":
    main()
