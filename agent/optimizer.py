"""Autonomous optimization and self-improvement loop with Git deployment.

Implements Web4 self-improvement:
1. In-sample discovery + out-of-sample walk-forward verification.
2. Multi-asset profile support (crypto_micro_swing, stocks_growth, macro_hedges).
3. Strict invariant boundaries (1%-5% risk, maximum drawdown limits).
4. Automatic pytest regression gating before atomic git commit and push.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional

from core.data_loader import fetch_ohlcv, is_crypto_symbol
from core.backtest_engine import run_backtest
from agent.validator import split_data, validate_improvement, evaluate_parameters

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "active_config.json"


def load_active_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def save_active_config(config: Dict[str, Any]):
    """Atomically write updated configuration with backup protection."""
    bak_path = CONFIG_PATH.with_suffix(".json.bak")
    if CONFIG_PATH.exists():
        shutil.copyfile(CONFIG_PATH, bak_path)
    tmp_path = CONFIG_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w") as f:
        json.dump(config, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, CONFIG_PATH)


def run_unit_tests() -> bool:
    """Run pytest suite to verify no invariants were broken."""
    cmd = [
        str(PROJECT_ROOT / ".venv" / "bin" / "pytest"),
        "-c",
        str(PROJECT_ROOT / "pytest.ini"),
        str(PROJECT_ROOT / "tests"),
    ]
    res = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True)
    return res.returncode == 0


def commit_and_push_patch(message: str) -> bool:
    """Commit updated config and push to GitHub."""
    try:
        subprocess.run(["git", "-C", str(PROJECT_ROOT), "add", "config/active_config.json"], check=True)
        subprocess.run(["git", "-C", str(PROJECT_ROOT), "commit", "-m", message], check=True)
        subprocess.run(["git", "-C", str(PROJECT_ROOT), "push", "origin", "main"], check=True)
        return True
    except Exception as e:
        print(f"[Optimizer] Git push warning: {e}")
        return False


def run_optimization_cycle(
    auto_commit: bool = True,
    target_profile: Optional[str] = None,
    target_symbol: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute one full self-improvement cycle:
    1. Load current active config & resolve target symbol/profile.
    2. Split data into In-Sample (70%) and Out-of-Sample (30%).
    3. Explore parameter mutations across lookbacks, EMAs, and ATR multipliers.
    4. Validate best candidate against unseen data (anti-overfitting).
    5. Safely update config, verify unit tests, commit and deploy if verified.
    """
    base_config = load_active_config()
    timeframe = base_config.get("timeframe", "1h")

    # Resolve target symbol and active profile
    profiles = base_config.get("asset_profiles", {})
    if target_profile and target_profile in profiles:
        prof_data = profiles[target_profile]
        symbols = prof_data.get("symbols", [])
        symbol = target_symbol if (target_symbol and target_symbol in symbols) else (symbols[0] if symbols else base_config.get("symbol", "SOL/USDT"))
        eval_config = dict(base_config)
        eval_config.update({k: v for k, v in prof_data.items() if k != "symbols"})
    else:
        symbol = target_symbol or base_config.get("symbol", "SOL/USDT")
        eval_config = dict(base_config)
        target_profile = None

    print("=" * 65)
    prof_label = f" [Profile: {target_profile}]" if target_profile else " [Global]"
    print(f"   STARTING WEB4 SELF-IMPROVEMENT CYCLE: {symbol} ({timeframe}){prof_label}")
    print("=" * 65)

    # Load 180 days of market data
    fetch_days = 90 if is_crypto_symbol(symbol) else 180
    try:
        df = fetch_ohlcv(symbol=symbol, timeframe=timeframe, days=fetch_days)
    except Exception as e:
        print(f"[Optimizer Error] Could not fetch data for {symbol}: {e}")
        return {"status": "error", "reason": str(e)}

    train_df, test_df = split_data(df, train_ratio=0.7)
    print(f"[Split] In-Sample (Train): {len(train_df)} bars | Out-of-Sample (Test): {len(test_df)} bars")

    # Evaluate baseline
    base_train_res = evaluate_parameters(train_df, eval_config)
    base_test_res = evaluate_parameters(test_df, eval_config)
    print(f"[Baseline In-Sample]  Return: {base_train_res.total_return_pct:+.2f}% | Sharpe: {base_train_res.sharpe_ratio:.2f} | MaxDD: {base_train_res.max_drawdown_pct:.1f}%")
    print(f"[Baseline Out-Sample] Return: {base_test_res.total_return_pct:+.2f}% | Sharpe: {base_test_res.sharpe_ratio:.2f} | MaxDD: {base_test_res.max_drawdown_pct:.1f}%")
    print("-" * 65)

    # Search space
    lookbacks = [16, 20, 24, 32]
    ema_filters = [50, 100, 150, 200]
    multipliers = [2.0, 2.5, 3.0]
    risk_pcts = [0.015, 0.02, 0.025]  # strictly 1.5% to 2.5%

    best_candidate = None
    best_train_sharpe = base_train_res.sharpe_ratio

    print("[Search] Evaluating parameter candidates on In-Sample training data...")
    evaluated_count = 0

    for lb in lookbacks:
        for ema in ema_filters:
            for mult in multipliers:
                for rsk in risk_pcts:
                    evaluated_count += 1
                    cand = dict(eval_config)
                    cand["breakout_lookback"] = lb
                    cand["ema_trend_filter"] = ema
                    cand["atr_multiplier"] = mult
                    cand["account_risk_pct"] = rsk

                    res = evaluate_parameters(train_df, cand)
                    if res.sharpe_ratio > best_train_sharpe and res.max_drawdown_pct < 12.0:
                        best_train_sharpe = res.sharpe_ratio
                        best_candidate = cand

    print(f"[Search] Tested {evaluated_count} permutations.")

    if not best_candidate:
        print("\n[Result] Baseline parameters are already optimal on training data. No mutation proposed.")
        return {"status": "unchanged", "reason": "No candidate beat baseline"}

    print("\n[Validation] Best In-Sample candidate found:")
    print(f"  Breakout Lookback: {best_candidate['breakout_lookback']} bars")
    print(f"  EMA Trend Filter:  {best_candidate['ema_trend_filter']}")
    print(f"  ATR Multiplier:    {best_candidate['atr_multiplier']}x")
    print(f"  Risk Per Trade:    {best_candidate['account_risk_pct']*100:.1f}%")

    # Out-of-sample validation
    report = validate_improvement(
        base_params=eval_config,
        candidate_params=best_candidate,
        train_df=train_df,
        test_df=test_df,
        max_dd_limit=14.0,
    )

    print(f"\n[Validation Report] {report.reason}")
    print(f"  Train Return Delta: {report.train_return_delta:+.2f}%")
    print(f"  Test Return Delta:  {report.test_return_delta:+.2f}%")
    print(f"  Out-of-Sample MaxDD: {report.test_max_dd:.1f}%")

    if not report.is_valid:
        print("\n[Safety Gate] Candidate rejected to protect against overfitting. Retaining current config.")
        return {"status": "rejected", "report": report}

    # Candidate passed! Update active_config.json
    print("\n[Deployment] Candidate passed all safety gates! Updating active_config.json...")
    updated_config = dict(base_config)
    if target_profile and target_profile in updated_config.get("asset_profiles", {}):
        prof = dict(updated_config["asset_profiles"][target_profile])
        prof["breakout_lookback"] = best_candidate["breakout_lookback"]
        prof["ema_trend_filter"] = best_candidate["ema_trend_filter"]
        prof["atr_multiplier"] = best_candidate["atr_multiplier"]
        updated_config["asset_profiles"][target_profile] = prof
    else:
        updated_config["breakout_lookback"] = best_candidate["breakout_lookback"]
        updated_config["ema_trend_filter"] = best_candidate["ema_trend_filter"]
        updated_config["atr_multiplier"] = best_candidate["atr_multiplier"]

    save_active_config(updated_config)

    # Run unit tests to verify system integrity
    if not run_unit_tests():
        print("[Rollback] Unit tests failed! Rolling back to baseline config.")
        save_active_config(base_config)
        return {"status": "rolled_back", "reason": "Unit tests failed"}

    commit_msg = (
        f"feat(self-improvement): optimize {target_profile or symbol} parameters\n\n"
        f"In-Sample Sharpe: {report.train_sharpe:.2f} (Delta: {report.train_return_delta:+.2f}%)\n"
        f"Out-of-Sample Sharpe: {report.test_sharpe:.2f} (Delta: {report.test_return_delta:+.2f}%)"
    )

    if auto_commit:
        committed = commit_and_push_patch(commit_msg)
        print(f"[Deployment] Git commit and push status: {'SUCCESS' if committed else 'LOCAL ONLY'}")

    print("=" * 65)
    print("   SELF-IMPROVEMENT CYCLE COMPLETED SUCCESSFULLY")
    print("=" * 65)
    return {"status": "deployed", "candidate": best_candidate, "report": report}


if __name__ == "__main__":
    run_optimization_cycle(auto_commit=True)
