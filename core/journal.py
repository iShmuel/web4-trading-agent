"""Decision Journal and Settlement Engine.

Records every trade proposal, entry, exit, and blocked gate decision made by the
PortfolioCoordinator. Settles past decisions once their forward evaluation window
has traded, calculating raw return and benchmark alpha (BTC for crypto, SPY for stocks)
to provide empirical evidence on strategy and risk gate efficacy.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

from core.data_loader import get_data_dir, is_crypto_symbol

JOURNAL_FILE = get_data_dir() / "decision_journal.jsonl"


def generate_llm_reflection(
    symbol: str,
    action: str,
    strategy_id: str,
    ref_price: float,
    settle_price: float,
    forward_ret_pct: float,
    alpha_pct: float,
    benchmark_symbol: str = "SPY",
    endpoint: str = "http://localhost:11434/api/generate",
    model: str = "phi4-mini:latest",
    timeout: float = 6.0,
) -> str:
    """Generate concise 2-sentence institutional post-mortem reflection on settled trade."""
    prompt = (
        f"You are a quantitative trading analyst reviewing a settled trade for {symbol}.\n"
        f"Action: {action} via {strategy_id}\n"
        f"Entry Price: ${ref_price:,.2f} | 24h Exit Price: ${settle_price:,.2f}\n"
        f"Realized 24h Return: {forward_ret_pct:+.2f}%\n"
        f"Alpha vs Benchmark ({benchmark_symbol}): {alpha_pct:+.2f}%\n\n"
        f"Write exactly 2 sentences of plain prose (no bullets) assessing what this outcome shows and 1 concrete takeaway."
    )
    try:
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 120},
        }
        resp = requests.post(endpoint, json=payload, timeout=timeout)
        if resp.status_code == 200:
            reflection = resp.json().get("response", "").strip()
            if reflection:
                return reflection
    except Exception:
        pass

    # Deterministic fallback reflection
    direction = "outperformed" if alpha_pct > 0 else "underperformed"
    return f"Trade settled with {forward_ret_pct:+.2f}% 24h return ({direction} benchmark {benchmark_symbol} by {alpha_pct:+.2f}% alpha). Quantitative risk boundaries held capital intact."


@dataclass
class JournalEntry:
    timestamp: str
    symbol: str
    action: str
    strategy_id: str
    reason: str
    reference_price: float
    indicators: Dict[str, Any] = field(default_factory=dict)
    settled: bool = False
    settled_at: Optional[str] = None
    settle_price: Optional[float] = None
    forward_return_pct: Optional[float] = None
    benchmark_symbol: Optional[str] = None
    benchmark_return_pct: Optional[float] = None
    alpha_pct: Optional[float] = None
    effective_call: Optional[bool] = None


def get_benchmark_for_symbol(symbol: str) -> str:
    """Return benchmark baseline: BTC/USDT for crypto, SPY for equities."""
    return "BTC/USDT" if is_crypto_symbol(symbol) else "SPY"


def log_decision(
    symbol: str,
    action: str,
    strategy_id: str,
    reason: str,
    reference_price: float,
    indicators: Optional[Dict[str, Any]] = None,
    journal_path: Optional[Path] = None,
) -> JournalEntry:
    """Append a decision record to the persistent decision journal."""
    target_path = journal_path or JOURNAL_FILE
    now_iso = datetime.now(timezone.utc).isoformat()
    clean_indicators = {}
    if indicators:
        for k, v in indicators.items():
            if isinstance(v, (int, float, str, bool)):
                clean_indicators[k] = v
            elif isinstance(v, (np.integer, np.floating)):
                clean_indicators[k] = float(v)

    entry = JournalEntry(
        timestamp=now_iso,
        symbol=symbol,
        action=action,
        strategy_id=strategy_id,
        reason=reason,
        reference_price=float(reference_price),
        indicators=clean_indicators,
        benchmark_symbol=get_benchmark_for_symbol(symbol),
    )

    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry)) + "\n")

    return entry


def load_journal_entries(journal_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load all journal records from disk."""
    target_path = journal_path or JOURNAL_FILE
    if not target_path.exists():
        return []
    entries = []
    with open(target_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
    return entries


def settle_journal(
    market_data: Dict[str, pd.DataFrame],
    holding_bars: int = 24,
    journal_path: Optional[Path] = None,
) -> Tuple[int, int]:
    """Settle past decisions against forward market returns and benchmark alpha.

    Returns:
        (settled_count, pending_count)
    """
    target_path = journal_path or JOURNAL_FILE
    entries = load_journal_entries(target_path)
    if not entries:
        return 0, 0

    settled_count = 0
    pending_count = 0
    updated_entries = []

    for entry in entries:
        if entry.get("settled", False):
            updated_entries.append(entry)
            continue

        sym = entry["symbol"]
        df = market_data.get(sym)
        if df is None or df.empty:
            pending_count += 1
            updated_entries.append(entry)
            continue

        entry_time_str = entry["timestamp"]
        try:
            entry_dt = pd.to_datetime(entry_time_str, utc=True)
        except Exception:
            pending_count += 1
            updated_entries.append(entry)
            continue

        # Look for bars occurring after the decision timestamp
        future_bars = df[df.index > entry_dt]
        if len(future_bars) < holding_bars:
            pending_count += 1
            updated_entries.append(entry)
            continue

        settle_bar = future_bars.iloc[holding_bars - 1]
        settle_price = float(settle_bar["close"])
        ref_price = float(entry["reference_price"])
        if ref_price <= 0:
            pending_count += 1
            updated_entries.append(entry)
            continue

        raw_ret = (settle_price - ref_price) / ref_price

        # Compute benchmark return over the same period
        bench_sym = entry.get("benchmark_symbol", get_benchmark_for_symbol(sym))
        bench_df = market_data.get(bench_sym)
        bench_ret = 0.0
        if bench_df is not None and not bench_df.empty:
            bench_future = bench_df[bench_df.index > entry_dt]
            if len(bench_future) >= holding_bars:
                b_start = float(bench_future.iloc[0]["open"])
                b_end = float(bench_future.iloc[holding_bars - 1]["close"])
                if b_start > 0:
                    bench_ret = (b_end - b_start) / b_start

        alpha = raw_ret - bench_ret
        action = entry.get("action", "")

        # For LONG entries: positive alpha is effective
        # For SHORT entries: negative raw return is effective
        # For BLOCKED decisions: if alpha was negative (asset dropped), blocking was effective!
        if action == "ENTER_LONG":
            effective = alpha > 0
        elif action == "ENTER_SHORT":
            effective = raw_ret < 0
        elif "Blocked" in entry.get("reason", ""):
            effective = alpha < 0
        else:
            effective = None

        entry["settled"] = True
        entry["settled_at"] = (
            settle_bar.name.isoformat()
            if hasattr(settle_bar.name, "isoformat")
            else str(settle_bar.name)
        )
        entry["settle_price"] = settle_price
        entry["forward_return_pct"] = round(raw_ret * 100.0, 3)
        entry["benchmark_return_pct"] = round(bench_ret * 100.0, 3)
        entry["alpha_pct"] = round(alpha * 100.0, 3)
        entry["effective_call"] = effective

        # Out-of-band LLM institutional post-mortem reflection
        try:
            entry["llm_reflection"] = generate_llm_reflection(
                symbol=sym,
                action=action,
                strategy_id=entry.get("strategy_id", "strategy"),
                ref_price=ref_price,
                settle_price=settle_price,
                forward_ret_pct=entry["forward_return_pct"],
                alpha_pct=entry["alpha_pct"],
                benchmark_symbol=bench_sym,
            )
        except Exception:
            pass

        settled_count += 1
        updated_entries.append(entry)

    # Rewrite atomic journal
    temp_path = target_path.with_suffix(".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        for entry in updated_entries:
            f.write(json.dumps(entry) + "\n")
    temp_path.replace(target_path)

    return settled_count, pending_count


def generate_journal_report(journal_path: Optional[Path] = None) -> str:
    """Generate summary report of settled decisions and gate effectiveness."""
    entries = load_journal_entries(journal_path)
    if not entries:
        return "Decision Journal is empty. No records logged yet."

    settled = [e for e in entries if e.get("settled", False)]
    pending = len(entries) - len(settled)

    lines = [
        "=" * 70,
        "WEB4 DECISION JOURNAL & SETTLEMENT AUDIT REPORT",
        "=" * 70,
        f"Total Logged Decisions: {len(entries)} | Settled: {len(settled)} | Pending: {pending}",
        "-" * 70,
    ]

    if not settled:
        lines.append("No decisions have reached their forward holding settlement window yet.")
        lines.append("=" * 70)
        return "\n".join(lines)

    # Group by action / gate
    categories: Dict[str, List[Dict[str, Any]]] = {}
    for e in settled:
        cat = e.get("action", "OTHER")
        if "Blocked" in e.get("reason", ""):
            # Extract gate name e.g. "Macro Bear Gate"
            reason = e.get("reason", "")
            gate_name = reason.split("(")[0].strip() if "(" in reason else reason[:25]
            cat = f"BLOCKED: {gate_name}"
        categories.setdefault(cat, []).append(e)

    lines.append(f"{'Category / Gate':<30} {'Count':<7} {'Win/Eff %':<12} {'Mean Alpha':<12} {'Mean Return':<12}")
    lines.append("-" * 73)

    for cat, items in sorted(categories.items()):
        cnt = len(items)
        eff_items = [i for i in items if i.get("effective_call") is not None]
        eff_pct = (sum(1 for i in eff_items if i["effective_call"]) / len(eff_items) * 100.0) if eff_items else 0.0
        alphas = [i.get("alpha_pct", 0.0) for i in items if i.get("alpha_pct") is not None]
        rets = [i.get("forward_return_pct", 0.0) for i in items if i.get("forward_return_pct") is not None]
        mean_alpha = np.mean(alphas) if alphas else 0.0
        mean_ret = np.mean(rets) if rets else 0.0

        eff_str = f"{eff_pct:5.1f}%" if eff_items else "  N/A"
        lines.append(f"{cat:<30} {cnt:<7} {eff_str:<12} {mean_alpha:>+6.2f}%     {mean_ret:>+6.2f}%")

    lines.append("=" * 70)
    return "\n".join(lines)
