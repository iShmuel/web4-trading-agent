"""Decision Journal and Settlement Engine.

Records every trade proposal, entry, exit, and blocked gate decision made by the
PortfolioCoordinator. Settles past decisions once their forward evaluation window
has traded, calculating raw return and benchmark alpha (BTC for crypto, SPY for stocks)
to provide empirical evidence on strategy and risk gate efficacy.
"""

import gzip
import json
import os
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


ARCHIVE_DIRNAME = "journal_archive"


def _archive_dir(target_path: Path) -> Path:
    return target_path.parent / ARCHIVE_DIRNAME


def _iter_jsonl(path: Path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except Exception:
                    continue


def load_journal_entries(
    journal_path: Optional[Path] = None,
    include_archives: bool = False,
) -> List[Dict[str, Any]]:
    """Load journal records from disk (optionally including rotated gzip archives, oldest first)."""
    target_path = journal_path or JOURNAL_FILE
    entries: List[Dict[str, Any]] = []
    if include_archives:
        arch = _archive_dir(target_path)
        if arch.exists():
            for gz in sorted(arch.glob("decision_journal_*.jsonl.gz")):
                entries.extend(_iter_jsonl(gz))
    if target_path.exists():
        entries.extend(_iter_jsonl(target_path))
    return entries


def load_recent_settled_entries(
    journal_path: Optional[Path] = None,
    count: int = 50,
    block_bytes: int = 1 << 20,
) -> List[Dict[str, Any]]:
    """Return the last ``count`` settled entries in file order WITHOUT parsing the whole file.

    Reads the file backwards in blocks, so cost is bounded by the number of lines needed rather
    than total journal size (previously every call re-parsed the entire multi-MB journal).
    """
    target_path = journal_path or JOURNAL_FILE
    if not target_path.exists() or count <= 0:
        return []
    found: List[Dict[str, Any]] = []
    with open(target_path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        carry = b""
        while pos > 0 and len(found) < count:
            read = min(block_bytes, pos)
            pos -= read
            f.seek(pos)
            chunk = f.read(read) + carry
            lines = chunk.split(b"\n")
            carry = lines[0] if pos > 0 else b""
            candidates = lines[1:] if pos > 0 else lines
            for raw in reversed(candidates):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    e = json.loads(raw)
                except Exception:
                    continue
                if e.get("settled", False):
                    found.append(e)
                    if len(found) >= count:
                        break
    found.reverse()
    return found


def _as_utc_index(df: pd.DataFrame) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(df.index)
    return idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")


def settle_journal(
    market_data: Dict[str, pd.DataFrame],
    holding_bars: int = 24,
    journal_path: Optional[Path] = None,
    max_pending_days: float = 14.0,
    max_reflections: int = 5,
) -> Tuple[int, int]:
    """Settle past decisions against forward market returns and benchmark alpha.

    Efficiency/safety changes vs. the original implementation (results are identical for
    entries that settle):
      * forward bars are located with ``searchsorted`` instead of a boolean mask per entry;
      * the (potentially tens-of-MB) journal is only rewritten when something actually changed
        (the old code rewrote it on every 60s tick even when nothing settled);
      * unsettled entries older than ``max_pending_days`` are tagged ``expired`` and no longer
        re-scanned forever (their symbol may have left the watchlist, so they can never settle);
      * at most ``max_reflections`` LLM post-mortems are generated per call to bound tick latency.

    Returns:
        (settled_count, pending_count)
    """
    target_path = journal_path or JOURNAL_FILE
    entries = load_journal_entries(target_path)
    if not entries:
        return 0, 0

    settled_count = 0
    pending_count = 0
    changed = False
    reflections_done = 0
    now_utc = pd.Timestamp.now(tz="UTC")
    expiry_cutoff = now_utc - pd.Timedelta(days=max_pending_days)

    index_cache: Dict[str, pd.DatetimeIndex] = {}

    def _idx(sym: str, frame: pd.DataFrame) -> pd.DatetimeIndex:
        if sym not in index_cache:
            index_cache[sym] = _as_utc_index(frame)
        return index_cache[sym]

    for entry in entries:
        if entry.get("settled", False) or entry.get("expired", False):
            continue

        sym = entry["symbol"]
        try:
            entry_dt = pd.to_datetime(entry["timestamp"], utc=True)
        except Exception:
            pending_count += 1
            continue

        df = market_data.get(sym)
        if df is None or df.empty:
            if entry_dt < expiry_cutoff:
                entry["expired"] = True
                changed = True
            else:
                pending_count += 1
            continue

        idx = _idx(sym, df)
        first_future = int(idx.searchsorted(entry_dt, side="right"))
        if len(idx) - first_future < holding_bars:
            if entry_dt < expiry_cutoff:
                entry["expired"] = True
                changed = True
            else:
                pending_count += 1
            continue

        settle_bar = df.iloc[first_future + holding_bars - 1]
        settle_price = float(settle_bar["close"])
        ref_price = float(entry["reference_price"])
        if ref_price <= 0:
            pending_count += 1
            continue

        raw_ret = (settle_price - ref_price) / ref_price

        # Compute benchmark return over the same period
        bench_sym = entry.get("benchmark_symbol", get_benchmark_for_symbol(sym))
        bench_df = market_data.get(bench_sym)
        bench_ret = 0.0
        if bench_df is not None and not bench_df.empty:
            b_idx = _idx(bench_sym, bench_df)
            b_first = int(b_idx.searchsorted(entry_dt, side="right"))
            if len(b_idx) - b_first >= holding_bars:
                b_start = float(bench_df.iloc[b_first]["open"])
                b_end = float(bench_df.iloc[b_first + holding_bars - 1]["close"])
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

        # Out-of-band LLM institutional post-mortem reflection (qualitative only, rate-limited)
        if reflections_done < max_reflections:
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
                reflections_done += 1
            except Exception:
                pass

        settled_count += 1
        changed = True

    if changed:
        # Atomic rewrite only when at least one record was settled/expired
        temp_path = target_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())
        temp_path.replace(target_path)

    return settled_count, pending_count


def rotate_journal(
    journal_path: Optional[Path] = None,
    max_active_bytes: int = 25 * 1024 * 1024,
    keep_days: float = 7.0,
    force: bool = False,
) -> Dict[str, Any]:
    """Archive old, finished journal records into gzip files without losing or reordering data.

    Archived: records that are ``settled`` or ``expired`` AND older than ``keep_days``.
    Kept in the active file (original order): everything else, including pending decisions and any
    line that fails to parse (never dropped).

    Safety:
      * single-writer assumption is guaranteed by the bot's SingleInstanceLock;
      * archive is written (temp + fsync + rename) BEFORE the active file is replaced, and its
        name is deterministic for identical input, so a crash between the two steps cannot create
        duplicate or missing history on retry;
      * counts are verified (kept + archived == input) before the active file is replaced.
    """
    target_path = journal_path or JOURNAL_FILE
    if not target_path.exists():
        return {"rotated": False, "reason": "no journal"}
    size = target_path.stat().st_size
    if not force and size <= max_active_bytes:
        return {"rotated": False, "reason": "below size threshold", "active_bytes": size}

    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=keep_days)
    kept_lines: List[str] = []
    archived_lines: List[str] = []
    first_ts = last_ts = None
    total = 0
    with open(target_path, "r", encoding="utf-8") as f:
        for raw in f:
            if not raw.strip():
                continue
            total += 1
            line = raw.rstrip("\n")
            archive_it = False
            try:
                e = json.loads(line)
                if e.get("settled", False) or e.get("expired", False):
                    ts = pd.to_datetime(e["timestamp"], utc=True)
                    archive_it = ts < cutoff
                    if archive_it:
                        first_ts = ts if first_ts is None else min(first_ts, ts)
                        last_ts = ts if last_ts is None else max(last_ts, ts)
            except Exception:
                archive_it = False
            (archived_lines if archive_it else kept_lines).append(line)

    if not archived_lines:
        return {"rotated": False, "reason": "nothing old enough to archive", "active_bytes": size}
    if len(kept_lines) + len(archived_lines) != total:
        raise RuntimeError("journal rotation count mismatch; aborting without changes")

    arch_dir = _archive_dir(target_path)
    arch_dir.mkdir(parents=True, exist_ok=True)
    stamp = lambda t: t.strftime("%Y%m%dT%H%M%SZ")  # noqa: E731
    arch_path = arch_dir / f"decision_journal_{stamp(first_ts)}_{stamp(last_ts)}_{len(archived_lines)}.jsonl.gz"
    tmp_arch = arch_path.with_suffix(".gz.tmp")
    with gzip.open(tmp_arch, "wt", encoding="utf-8") as gz:
        for line in archived_lines:
            gz.write(line + "\n")
    os.replace(tmp_arch, arch_path)

    tmp_active = target_path.with_suffix(".rotate.tmp")
    with open(tmp_active, "w", encoding="utf-8") as f:
        for line in kept_lines:
            f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_active, target_path)

    return {
        "rotated": True,
        "archived_records": len(archived_lines),
        "kept_records": len(kept_lines),
        "archive_file": str(arch_path),
        "active_bytes_before": size,
        "active_bytes_after": target_path.stat().st_size,
    }


def generate_journal_report(journal_path: Optional[Path] = None) -> str:
    """Generate summary report of settled decisions and gate effectiveness."""
    entries = load_journal_entries(journal_path, include_archives=True)
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


_GATE_FEEDBACK_CACHE: Dict[Tuple[str, int], Tuple[float, Dict[str, Any]]] = {}


def get_gate_alpha_feedback(
    journal_path: Optional[Path] = None,
    lookback_entries: int = 50,
) -> Dict[str, Any]:
    """Analyze settled decisions to provide closed-loop alpha feedback.
    
    Evaluates empirical effectiveness of gate blocks and entries:
    - If macro bear gate & confluence blocks averted losses (effective_call == True),
      it indicates high-risk market chop where tightening confluence is prudent.
    - Returns a recommended delta to min_confluence_score (between -0.05 and +0.05).
    """
    target_path = journal_path or JOURNAL_FILE
    cache_key = (str(target_path), lookback_entries)
    mtime = 0.0
    if target_path.exists():
        try:
            mtime = target_path.stat().st_mtime
            if cache_key in _GATE_FEEDBACK_CACHE and _GATE_FEEDBACK_CACHE[cache_key][0] == mtime:
                return _GATE_FEEDBACK_CACHE[cache_key][1]
        except OSError:
            pass

    settled = load_recent_settled_entries(journal_path, count=lookback_entries)
    if len(settled) < lookback_entries:
        # Active file may have been rotated: fall back to archives for the remaining history
        full = load_journal_entries(journal_path, include_archives=True)
        settled = [e for e in full if e.get("settled", False)][-lookback_entries:]
    if not settled:
        res = {
            "has_feedback": False,
            "sample_size": 0,
            "recommended_confluence_delta": 0.0,
            "mean_alpha": 0.0,
        }
        if mtime > 0:
            _GATE_FEEDBACK_CACHE[cache_key] = (mtime, res)
        return res

    recent = settled[-lookback_entries:]
    alphas = [e.get("alpha_pct", 0.0) for e in recent if e.get("alpha_pct") is not None]
    mean_alpha = float(np.mean(alphas)) if alphas else 0.0

    blocked = [e for e in recent if "Blocked" in e.get("reason", "") or e.get("action") == "REJECTED"]
    eff_blocks = [e for e in blocked if e.get("effective_call") is True]
    eff_ratio = (len(eff_blocks) / len(blocked)) if blocked else 0.5

    rec_delta = 0.0
    if len(blocked) >= 5:
        if eff_ratio >= 0.70:
            rec_delta = +0.03
        elif eff_ratio <= 0.35:
            rec_delta = -0.02

    res = {
        "has_feedback": True,
        "sample_size": len(recent),
        "blocked_count": len(blocked),
        "block_efficacy_ratio": round(eff_ratio, 3),
        "mean_alpha": round(mean_alpha, 3),
        "recommended_confluence_delta": rec_delta,
    }
    if mtime > 0:
        _GATE_FEEDBACK_CACHE[cache_key] = (mtime, res)
    return res

