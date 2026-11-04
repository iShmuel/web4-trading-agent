"""Authoritative Multi-Strategy Portfolio Coordinator.

Reconciles proposals from independent strategy engines (Momentum and Mean Reversion),
resolves conflicts, prevents duplicate orders, enforces centralized risk limits,
and coordinates paper execution and portfolio accounting.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd

from core.signals import TradingSignal, SignalDirection, SignalStatus, create_no_trade_signal
from core.risk_engine import RiskEngine, RiskVerdict
from core.paper_account import PaperAccount
from core.data_loader import fetch_funding_rate, is_crypto_symbol
from core.strategy import (
    calculate_multi_timeframe_confluence,
    calculate_relative_strength,
    detect_liquidation_absorption,
)
from strategies.momentum import MomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy
from core.journal import log_decision, get_gate_alpha_feedback
from core.polymarket_radar import PolymarketRadar, MacroEventRisk
from core.fred_radar import FredMacroRadar, MacroRegimeReport
from core.basis_yield import BasisYieldEngine, BasisOpportunity
from core.preflight import (
    PreFlightChecker,
    PreFlightVerdict,
    LocalLLMScreener,
    fetch_ticker_headlines,
)


@dataclass
class CoordinatorDecision:
    symbol: str
    action: str  # "ENTER_LONG", "ENTER_SHORT", "EXIT", "HOLD", "REJECTED", "CONFLICT"
    strategy_id: str
    units: float = 0.0
    price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    reason: str = ""
    signal: Optional[TradingSignal] = None
    risk_verdict: Optional[RiskVerdict] = None
    indicators: Dict[str, Any] = field(default_factory=dict)



@dataclass
class CycleReport:
    timestamp: datetime
    symbols_evaluated: int
    signals_generated: int
    conflicts_detected: int
    orders_executed: int
    decisions: List[CoordinatorDecision] = field(default_factory=list)
    portfolio_equity: float = 0.0
    available_cash: float = 0.0
    open_positions: int = 0


def resolve_symbol_config(config: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    """Return the effective config for one symbol.

    Top-level keys are the defaults; if ``use_asset_profiles`` is enabled (default)
    and the symbol is listed in an ``asset_profiles`` entry, that profile's keys
    override the defaults. Risk limits are never taken from profiles.
    """
    base = {k: v for k, v in config.items() if k != "asset_profiles"}
    if not config.get("use_asset_profiles", True):
        base["profile_name"] = "default"
        return base
    for name, profile in (config.get("asset_profiles") or {}).items():
        if symbol in profile.get("symbols", []):
            merged = dict(base)
            merged.update({k: v for k, v in profile.items() if k not in PROFILE_PROTECTED_KEYS})
            merged["profile_name"] = name
            return merged
    base["profile_name"] = "default"
    return base


# Keys a per-asset profile may never override (risk bounds stay global).
PROFILE_PROTECTED_KEYS = {"symbols", "account_risk_pct", "max_concurrent_positions"}


class PortfolioCoordinator:
    """Central authority coordinating multiple trading strategies into one portfolio."""

    def __init__(
        self,
        account: PaperAccount,
        risk_engine: RiskEngine,
        config: Dict[str, Any],
    ):
        self.account = account
        self.risk_engine = risk_engine
        self.config = config

        # Default strategy engines (top-level config)
        self.momentum_strategy = MomentumStrategy(config)
        self.mean_reversion_strategy = MeanReversionStrategy(config)
        # Per-symbol engines built from asset profiles (lazily cached)
        self._symbol_engines: Dict[str, Tuple[Dict[str, Any], MomentumStrategy, MeanReversionStrategy]] = {}
        # Derivatives funding rate cache: symbol -> (timestamp, funding_rate)
        self._funding_cache: Dict[str, Tuple[float, Optional[float]]] = {}
        # Timestamp of most recent crypto stop-loss exit for contagion cooldown protection
        self._last_crypto_stop_time: Optional[pd.Timestamp] = None

        # Institutional Alpha Radars & Execution Pre-Flight
        self.polymarket_radar = PolymarketRadar()
        self.fred_radar = FredMacroRadar()
        self.preflight_checker = PreFlightChecker(
            max_spread_pct=float(config.get("preflight_max_spread_pct", 0.0015)),
            max_staleness_seconds=int(config.get("preflight_max_staleness_seconds", 180)),
            max_volume_participation_pct=float(config.get("preflight_max_volume_participation_pct", 0.02)),
        )
        self.llm_screener = LocalLLMScreener(
            model=config.get("local_llm_model", "phi4-mini:latest"),
            enabled=config.get("enable_local_llm_preflight", False),
        )
        self.basis_yield_engine = BasisYieldEngine(
            hurdle_apr=float(config.get("basis_hurdle_apr", 8.0)),
            max_portfolio_allocation_pct=float(config.get("max_basis_allocation_pct", 0.30)),
        )

    def symbol_config(self, symbol: str) -> Dict[str, Any]:
        return self._engines_for(symbol)[0]

    def _engines_for(self, symbol: str) -> Tuple[Dict[str, Any], MomentumStrategy, MeanReversionStrategy]:
        if symbol not in self._symbol_engines:
            cfg = resolve_symbol_config(self.config, symbol)
            self._symbol_engines[symbol] = (cfg, MomentumStrategy(cfg), MeanReversionStrategy(cfg))
        return self._symbol_engines[symbol]

    def reconcile_signals(
        self,
        symbol: str,
        momentum_sig: TradingSignal,
        mr_sig: TradingSignal,
    ) -> Tuple[Optional[TradingSignal], str]:
        """Reconcile signals from both strategies with Automated Regime Awareness.

        Rules:
        1. Macro Bear Gate: If price < macro_ema (200 EMA), block Long breakout entries
           (protects capital from bear-market cascade liquidation).
        2. Range Chop Gate: If ADX < adx_chop_threshold (default 20.0), suppress breakout
           entries and prioritize Mean Reversion.
        3. Strong Trend Gate: If ADX >= adx_trend_threshold (default 25.0), suppress
           counter-trend Mean Reversion.
        4. Standard Conflict / Tiebreak resolution.
        """
        mom_entry = (momentum_sig.status == SignalStatus.ENTRY)
        mr_entry = (mr_sig.status == SignalStatus.ENTRY)

        enable_regime_filter = self.config.get("enable_regime_filter", True)
        adx_chop_threshold = self.config.get("adx_chop_threshold", 20.0)
        adx_trend_threshold = self.config.get("adx_trend_threshold", 25.0)

        if enable_regime_filter:
            mom_ind = momentum_sig.indicators or {}
            mr_ind = mr_sig.indicators or {}
            cur_price = momentum_sig.reference_price or mr_sig.reference_price
            macro_ema = mom_ind.get("macro_ema") or mr_ind.get("macro_ema")
            adx = mr_ind.get("adx") if "adx" in mr_ind else mom_ind.get("adx")

            # Rule 1: Macro Bear Gate (Block long breakouts when below 200 EMA)
            if (
                macro_ema is not None
                and not pd.isna(macro_ema)
                and cur_price < macro_ema
                and momentum_sig.direction == SignalDirection.LONG
            ):
                if mom_entry:
                    mom_entry = False
                    momentum_sig = create_no_trade_signal(
                        "momentum", symbol, cur_price,
                        f"Blocked by Macro Bear Gate (${cur_price:.2f} < EMA200 ${macro_ema:.2f})",
                        indicators=momentum_sig.indicators,
                    )

            # Rule 2: Range Chop Gate (Suppress breakouts when ADX < chop threshold)
            if adx is not None and adx < adx_chop_threshold and mom_entry:
                mom_entry = False
                momentum_sig = create_no_trade_signal(
                    "momentum", symbol, cur_price,
                    f"Blocked by Chop Gate (ADX {adx:.1f} < {adx_chop_threshold:.1f})",
                    indicators=momentum_sig.indicators,
                )

            # Rule 3: Strong Trend Gate (Suppress counter-trend MR when ADX >= trend threshold)
            if adx is not None and adx >= adx_trend_threshold and mr_entry:
                mr_entry = False
                mr_sig = create_no_trade_signal(
                    "mean_reversion", symbol, cur_price,
                    f"Suppressed counter-trend MR in strong trend (ADX {adx:.1f} >= {adx_trend_threshold:.1f})",
                    indicators=mr_sig.indicators,
                )

            # Rule 4: Funding Rate Overheat Filter (Crypto Derivatives Radar)
            funding_regime = mom_ind.get("funding_regime") or mr_ind.get("funding_regime")
            if funding_regime == "OVERHEATED_LONGS" and mom_entry and momentum_sig.direction == SignalDirection.LONG:
                mom_entry = False
                momentum_sig = create_no_trade_signal(
                    "momentum", symbol, cur_price,
                    f"Blocked by Overheated Funding Rate ({mom_ind.get('funding_rate_pct', 0.0):+.3f}% per 8h)",
                    indicators=momentum_sig.indicators,
                )

            # Rule 5: Macro Crash Defense Gate (VIX Panic & Credit Spread Radar)
            macro_report = self.fred_radar.evaluate()
            if enable_regime_filter and not macro_report.allow_long_breakouts and mom_entry and momentum_sig.direction == SignalDirection.LONG:
                mom_entry = False
                momentum_sig = create_no_trade_signal(
                    "momentum", symbol, cur_price,
                    f"Blocked by Macro Crash Defense ({macro_report.reason})",
                    indicators=momentum_sig.indicators,
                )

        # Case 1: Both propose entries
        if mom_entry and mr_entry:
            if momentum_sig.direction != mr_sig.direction:
                # Opposite directions: CONFLICT
                return None, f"Conflict: Momentum proposes {momentum_sig.direction.value} while MeanReversion proposes {mr_sig.direction.value}"
            else:
                # Same direction: pick strongest signal
                if mr_sig.signal_strength > momentum_sig.signal_strength:
                    return mr_sig, f"Both agree on {mr_sig.direction.value}; selected MeanReversion (strength {mr_sig.signal_strength:.2f} > {momentum_sig.signal_strength:.2f})"
                else:
                    return momentum_sig, f"Both agree on {momentum_sig.direction.value}; selected Momentum (strength {momentum_sig.signal_strength:.2f} >= {mr_sig.signal_strength:.2f})"

        # Case 2: Momentum entry only
        if mom_entry:
            return momentum_sig, "Momentum entry proposed"

        # Case 3: Mean Reversion entry only
        if mr_entry:
            return mr_sig, "Mean Reversion entry proposed"

        # Case 4: Neither proposes entry
        if momentum_sig.no_trade_reason:
            return None, momentum_sig.no_trade_reason
        if mr_sig.no_trade_reason:
            return None, mr_sig.no_trade_reason
        return None, "No entry signal proposed"

    def process_symbol(
        self,
        symbol: str,
        df: pd.DataFrame,
        current_prices: Dict[str, float],
    ) -> CoordinatorDecision:
        """Evaluate one symbol across strategies and execute valid actions."""
        if df is None or len(df) == 0:
            return CoordinatorDecision(
                symbol=symbol,
                action="REJECTED",
                strategy_id="coordinator",
                reason="No market data provided",
            )

        cur_price = float(df.iloc[-1]["close"])
        current_prices[symbol] = cur_price
        existing_pos = self.account.get_position(symbol)
        sym_cfg, momentum_strategy, mean_reversion_strategy = self._engines_for(symbol)

        # 1. Evaluate exit conditions if already holding
        if existing_pos is not None:
            pos_strategy = existing_pos.get("strategy_id", "momentum")
            if pos_strategy == MeanReversionStrategy.STRATEGY_ID:
                sig = mean_reversion_strategy.evaluate(symbol, df, existing_position=existing_pos)
            else:
                sig = momentum_strategy.evaluate(symbol, df, existing_position=existing_pos)

            if sig.status == SignalStatus.EXIT:
                trade = self.account.close_position(
                    symbol=symbol,
                    exit_price=cur_price * (0.9996 if sig.direction == SignalDirection.LONG else 1.0004),
                    exit_reason=sig.exit_reason or "Strategy Exit Signal",
                    fee_pct=sym_cfg.get("fee_pct", 0.0006),
                )
                if is_crypto_symbol(symbol) and trade.get("pnl", 0.0) < 0 and "Stop" in trade.get("exit_reason", ""):
                    bar_ts = df.index[-1]
                    self._last_crypto_stop_time = bar_ts if isinstance(bar_ts, pd.Timestamp) else pd.Timestamp.now(tz="UTC")
                return CoordinatorDecision(
                    symbol=symbol,
                    action="EXIT",
                    strategy_id=pos_strategy,
                    units=trade["units"],
                    price=trade["exit_price"],
                    reason=sig.exit_reason or "Exit signal executed",
                    signal=sig,
                )
            elif sig.status == SignalStatus.PARTIAL_EXIT:
                ratio = sig.take_profit_ratio if sig.take_profit_ratio > 0 else 0.5
                scale_units = existing_pos["units"] * ratio
                exec_price = cur_price * (0.9996 if sig.direction == SignalDirection.LONG else 1.0004)
                # Mark first so a restart can never scale out the same position twice.
                self.account.set_position_fields(symbol, tp_hit=True)
                trade = self.account.partial_close_position(
                    symbol=symbol,
                    exit_price=exec_price,
                    exit_units=scale_units,
                    exit_reason=sig.exit_reason or "Partial Scale-Out Take-Profit",
                    fee_pct=sym_cfg.get("fee_pct", 0.0006),
                )
                # Fix #1: Automatically lock stop-loss to Break-Even (+fee buffer) upon 50% scale-out
                if self.config.get("enable_breakeven_lock", True):
                    ep = existing_pos["entry_price"]
                    is_short = (existing_pos.get("side", "LONG") == "SHORT")
                    be_stop = ep * (0.9985 if is_short else 1.0015)
                    cur_stop = existing_pos["trailing_stop"]
                    locked_stop = min(cur_stop, be_stop) if is_short else max(cur_stop, be_stop)
                    self.account.update_trailing_stop(symbol, locked_stop, cur_price)
                return CoordinatorDecision(
                    symbol=symbol,
                    action="PARTIAL_EXIT",
                    strategy_id=pos_strategy,
                    units=trade["units"],
                    price=trade["exit_price"],
                    reason=sig.exit_reason or "Partial take-profit executed",
                    signal=sig,
                )
            else:
                # Ratchet trailing stop if applicable
                atr = sig.atr if sig.atr > 0 else (cur_price * 0.02)
                atr_mult = sym_cfg.get("atr_multiplier", 2.5)
                is_short = (existing_pos.get("side", "LONG") == "SHORT")
                new_stop = self.risk_engine.update_trailing_stop(
                    current_price=cur_price,
                    current_atr=atr,
                    atr_multiplier=atr_mult,
                    existing_stop=existing_pos["trailing_stop"],
                    is_short=is_short,
                )
                # Fix #1: Break-Even Stop Lock once 50% TP hit OR unrealized profit >= breakeven_atr_mult * ATR
                if self.config.get("enable_breakeven_lock", True):
                    ep = existing_pos["entry_price"]
                    be_atr_mult = sym_cfg.get("breakeven_atr_mult", self.config.get("breakeven_atr_mult", 1.2))
                    tp_hit = existing_pos.get("tp_hit", False)
                    if not is_short:
                        be_stop = ep * 1.0015
                        if tp_hit or (cur_price - ep) >= (be_atr_mult * atr):
                            new_stop = max(new_stop, be_stop)
                    else:
                        be_stop = ep * 0.9985
                        if tp_hit or (ep - cur_price) >= (be_atr_mult * atr):
                            new_stop = min(new_stop, be_stop)
                self.account.update_trailing_stop(symbol, new_stop, cur_price)
                return CoordinatorDecision(
                    symbol=symbol,
                    action="HOLD",
                    strategy_id=pos_strategy,
                    units=existing_pos["units"],
                    price=cur_price,
                    stop_loss=new_stop,
                    reason="Position active, trailing stop updated",
                    signal=sig,
                )

        # 2. No open position: generate signals from both strategies
        mom_sig = momentum_strategy.evaluate(symbol, df, existing_position=None)
        mr_sig = mean_reversion_strategy.evaluate(symbol, df, existing_position=None)

        # Merge indicator snapshot from both strategies for full transparency
        merged_ind = {}
        if mom_sig and mom_sig.indicators:
            merged_ind.update(mom_sig.indicators)
        if mr_sig and mr_sig.indicators:
            for k, v in mr_sig.indicators.items():
                if k not in merged_ind:
                    merged_ind[k] = v

        # Multi-timeframe confluence evaluation
        has_confluence, conf_score, conf_desc = calculate_multi_timeframe_confluence(df, indicators=merged_ind)
        merged_ind["confluence"] = conf_desc
        merged_ind["confluence_score"] = conf_score

        # Volume Ratio & Order-Flow Liquidation Absorption
        vol_ratio = 1.0
        absorption_val = None
        if "volume" in df and len(df) >= 20:
            vol_series = df["volume"].astype(float)
            vol_sma20 = float(vol_series.iloc[-20:].mean())
            if vol_sma20 > 0:
                vol_ratio = float(vol_series.iloc[-1]) / vol_sma20
            if "absorption" in df.columns:
                last_abs = df["absorption"].iloc[-1]
                if pd.notna(last_abs):
                    absorption_val = str(last_abs)
            else:
                abs_series = detect_liquidation_absorption(df.iloc[-25:])
                if len(abs_series) > 0 and pd.notna(abs_series.iloc[-1]):
                    absorption_val = str(abs_series.iloc[-1])
        merged_ind["vol_ratio"] = round(vol_ratio, 3)
        merged_ind["absorption"] = absorption_val

        # Derivatives Funding Rate Radar (Crypto only, cached with 1h TTL to avoid network spam)
        if is_crypto_symbol(symbol) and self.config.get("enable_funding_radar", True):
            now_epoch = time.time()
            if symbol not in self._funding_cache or (now_epoch - self._funding_cache[symbol][0]) > 3600:
                fr = fetch_funding_rate(symbol)
                self._funding_cache[symbol] = (now_epoch, fr)
            else:
                fr = self._funding_cache[symbol][1]

            if fr is not None:
                merged_ind["funding_rate_pct"] = fr * 100
                if fr > 0.0005:
                    merged_ind["funding_regime"] = "OVERHEATED_LONGS"
                elif fr < -0.0002:
                    merged_ind["funding_regime"] = "SHORT_SQUEEZE_PRONE"
                else:
                    merged_ind["funding_regime"] = "NEUTRAL"

        # Determine current market regime
        macro_ema_val = merged_ind.get("macro_ema")
        adx_val = merged_ind.get("adx", 25.0)
        if macro_ema_val is not None and not pd.isna(macro_ema_val) and cur_price < macro_ema_val:
            regime = "MACRO_BEAR"
        elif adx_val >= self.config.get("adx_trend_threshold", 25.0):
            regime = "TREND_EXPANSION"
        elif adx_val < self.config.get("adx_chop_threshold", 20.0):
            regime = "RANGE_CHOP"
        else:
            regime = "TRANSITION"
        merged_ind["regime"] = regime

        chosen_sig, reason = self.reconcile_signals(symbol, mom_sig, mr_sig)

        if chosen_sig is None:
            action = "CONFLICT" if "Conflict" in reason else "REJECTED"
            return CoordinatorDecision(
                symbol=symbol,
                action=action,
                strategy_id="coordinator",
                price=cur_price,
                reason=reason,
                indicators=merged_ind,
            )

        # Multi-timeframe confluence evaluation for chosen trade direction
        chosen_dir_str = chosen_sig.direction.value if chosen_sig is not None else "LONG"
        has_confluence, conf_score, conf_desc = calculate_multi_timeframe_confluence(
            df,
            direction=chosen_dir_str,
            indicators=merged_ind,
        )
        merged_ind["confluence"] = conf_desc
        merged_ind["confluence_score"] = conf_score

        # Upgrade 2: Multi-Timeframe Trend Anchor Gate with Closed-Loop Web4 Feedback
        if self.config.get("enable_confluence_gate", False) and chosen_sig.strategy_id == "momentum":
            base_score = float(self.config.get("min_confluence_score", 0.70))
            feedback = get_gate_alpha_feedback()
            delta = feedback.get("recommended_confluence_delta", 0.0)
            min_score = max(0.60, min(0.85, base_score + delta))
            if conf_score < min_score:
                return CoordinatorDecision(
                    symbol=symbol,
                    action="REJECTED",
                    strategy_id=chosen_sig.strategy_id,
                    price=cur_price,
                    reason=f"Blocked by Multi-Timeframe Trend Anchor (Score {conf_score:.2f} < {min_score:.2f} [base {base_score:.2f}{delta:+.2f}]: {conf_desc})",
                    signal=chosen_sig,
                    indicators=merged_ind,
                )

        # Fix #3: Sector Crash Contagion Cooldown (Block same-bar / immediate contagion entries after a crypto stop-loss)
        if is_crypto_symbol(symbol) and self.config.get("enable_crash_cooldown", True):
            cooldown_hours = float(self.config.get("crypto_crash_cooldown_hours", 3.0))
            bar_ts = df.index[-1] if isinstance(df.index[-1], pd.Timestamp) else pd.Timestamp.now(tz="UTC")
            if self._last_crypto_stop_time is not None:
                try:
                    t_cur = bar_ts.tz_localize("UTC") if bar_ts.tzinfo is None else bar_ts
                    t_stop = (
                        self._last_crypto_stop_time.tz_localize("UTC")
                        if self._last_crypto_stop_time.tzinfo is None
                        else self._last_crypto_stop_time
                    )
                    elapsed_hours = (t_cur - t_stop).total_seconds() / 3600.0
                    if 0.0 <= elapsed_hours <= cooldown_hours:
                        return CoordinatorDecision(
                            symbol=symbol,
                            action="REJECTED",
                            strategy_id=chosen_sig.strategy_id,
                            price=cur_price,
                            reason=f"Blocked by Crypto Crash Contagion Cooldown ({elapsed_hours*60:.0f}m <= {cooldown_hours*60:.0f}m after stop-loss)",
                            signal=chosen_sig,
                            indicators=merged_ind,
                        )
                except Exception:
                    pass

        # 3. Validate chosen proposal through RiskEngine
        total_equity = self.account.get_total_equity(current_prices)
        last_ts = df.index[-1] if hasattr(df.index[-1], 'tzinfo') else None
        
        # Confluence stop tightening: Triple-timeframe alignment permits tighter stop loss
        base_atr_mult = sym_cfg.get("atr_multiplier", self.config.get("atr_multiplier", 2.25))
        effective_atr_mult = base_atr_mult * (0.85 if (has_confluence and conf_score >= 0.90) else 1.0)

        # Chop-Zone Risk Throttling: Cut risk in half (e.g. 1.0% vs 2.0%) during low-ADX range chop
        base_risk_pct = sym_cfg.get("account_risk_pct", self.config.get("account_risk_pct", 0.02))
        adx_val = merged_ind.get("adx")
        chop_thresh = self.config.get("adx_chop_threshold", 20.0)
        if self.config.get("throttle_chop_risk", False) and adx_val is not None and adx_val < chop_thresh:
            effective_risk_pct = max(0.01, base_risk_pct * 0.50)
        else:
            effective_risk_pct = base_risk_pct

        # Upgrade 5: Dynamic Volatility Targeting (Risk Parity)
        if self.config.get("enable_volatility_targeting", False):
            ref_atr_pct = float(self.config.get("target_atr_pct", 0.025))  # 2.5% baseline reference ATR
            actual_atr = chosen_sig.atr if (chosen_sig and chosen_sig.atr > 0) else (cur_price * 0.02)
            actual_atr_pct = (actual_atr / cur_price) if cur_price > 0 else ref_atr_pct
            vol_scale = ref_atr_pct / max(0.005, actual_atr_pct)
            vol_scale = max(0.60, min(1.40, vol_scale))
            fred_report = self.fred_radar.evaluate()
            if fred_report.vix_level > 25.0:
                vol_scale *= 0.80
            effective_risk_pct = max(0.01, min(0.05, effective_risk_pct * vol_scale))

        # Surge-Volume Conviction Boost: Scale risk by 1.2x on >=1.5x volume surge or aligned absorption wick (clamped <= 5%)
        vol_risk_mult = 1.0
        if self.config.get("enable_volume_boost", True):
            surge_mult = float(self.config.get("volume_surge_mult", 1.5))
            boost_factor = float(self.config.get("volume_boost_factor", 1.2))
            aligned_absorption = (
                (chosen_sig.direction == SignalDirection.LONG and absorption_val == "BULLISH_ABSORPTION")
                or (chosen_sig.direction == SignalDirection.SHORT and absorption_val == "BEARISH_ABSORPTION")
            )
            if vol_ratio >= surge_mult or aligned_absorption:
                vol_risk_mult = boost_factor

        # Institutional Macro Radar: Polymarket Binary Event Odds + FRED VIX Regime
        poly_risk = self.polymarket_radar.evaluate()
        fred_report = self.fred_radar.evaluate()
        macro_risk_mult = poly_risk.recommended_risk_multiplier * fred_report.risk_scale_factor

        merged_ind["polymarket_risk"] = poly_risk.risk_level
        merged_ind["recession_prob"] = poly_risk.recession_probability
        merged_ind["vix_level"] = fred_report.vix_level
        merged_ind["vix_regime"] = fred_report.regime

        combined_risk_mult = vol_risk_mult * macro_risk_mult

        risk_verdict = self.risk_engine.validate_proposed_entry(
            symbol=symbol,
            side=chosen_sig.direction.value,
            strategy_id=chosen_sig.strategy_id,
            entry_price=chosen_sig.reference_price,
            suggested_stop=chosen_sig.suggested_stop,
            atr=chosen_sig.atr,
            atr_multiplier=effective_atr_mult,
            equity=total_equity,
            cash=self.account.cash,
            active_positions=self.account.positions,
            fee_pct=chosen_sig.estimated_fee_pct,
            requested_risk_pct=effective_risk_pct,
            current_time=last_ts,
            risk_multiplier=combined_risk_mult,
        )

        if not risk_verdict.approved:
            return CoordinatorDecision(
                symbol=symbol,
                action="REJECTED",
                strategy_id=chosen_sig.strategy_id,
                price=cur_price,
                reason=f"Risk check blocked: {risk_verdict.reason}",
                signal=chosen_sig,
                risk_verdict=risk_verdict,
                indicators=merged_ind,
            )

        # Institutional Pre-Flight Execution Safety Guard
        hourly_vol = float(df["volume"].iloc[-1]) if "volume" in df.columns and len(df) > 0 else None
        quote_time = getattr(chosen_sig, "timestamp", last_ts)
        order_notional = risk_verdict.suggested_units * cur_price

        preflight = self.preflight_checker.check(
            symbol=symbol,
            side="BUY" if chosen_sig.direction == SignalDirection.LONG else "SELL",
            price=cur_price,
            bid=cur_price * 0.9997,
            ask=cur_price * 1.0003,
            last_quote_time=quote_time,
            hourly_volume=hourly_vol,
            proposed_units=risk_verdict.suggested_units,
            available_cash=self.account.cash,
            order_notional=order_notional,
        )

        if not preflight.passed:
            return CoordinatorDecision(
                symbol=symbol,
                action="REJECTED",
                strategy_id=chosen_sig.strategy_id,
                price=cur_price,
                reason=f"Blocked by Pre-Flight: {preflight.reason}",
                signal=chosen_sig,
                risk_verdict=risk_verdict,
                indicators=merged_ind,
            )

        # Local LLM Devil's Advocate (Headline & Breaking Hazard Screener)
        if self.config.get("enable_local_llm_preflight", False):
            headlines = fetch_ticker_headlines(symbol, limit=3)
            llm_verdict = self.llm_screener.screen_entry_proposal(
                symbol=symbol,
                side="LONG" if chosen_sig.direction == SignalDirection.LONG else "SHORT",
                price=cur_price,
                strategy_id=chosen_sig.strategy_id,
                headlines=headlines,
                indicators=merged_ind,
            )
            merged_ind["llm_screen_decision"] = "APPROVED" if llm_verdict.approved else "REJECTED"
            merged_ind["llm_screen_reason"] = llm_verdict.reason
            merged_ind["llm_latency_s"] = llm_verdict.latency_seconds

            if not llm_verdict.approved:
                return CoordinatorDecision(
                    symbol=symbol,
                    action="REJECTED",
                    strategy_id=chosen_sig.strategy_id,
                    price=cur_price,
                    reason=f"Blocked by Local LLM Devil's Advocate: {llm_verdict.reason}",
                    signal=chosen_sig,
                    risk_verdict=risk_verdict,
                    indicators=merged_ind,
                )

        # 4. Execute approved paper entry
        # 4. Smart Order Routing (Execution Alpha: Maker vs Taker)
        exec_mode = sym_cfg.get("execution_mode", self.config.get("execution_mode", "maker"))
        if exec_mode == "maker":
            # Maker post-only limit execution: zero slippage, reduced maker fee (0.02% vs 0.06%)
            applied_fee_pct = sym_cfg.get("maker_fee_pct", self.config.get("maker_fee_pct", 0.0002))
            exec_price = cur_price
        else:
            applied_fee_pct = chosen_sig.estimated_fee_pct
            slippage_factor = 1.0 + (chosen_sig.estimated_slippage_pct if chosen_sig.direction == SignalDirection.LONG else -chosen_sig.estimated_slippage_pct)
            exec_price = cur_price * slippage_factor

        new_pos = self.account.open_position(
            symbol=symbol,
            entry_price=exec_price,
            units=risk_verdict.suggested_units,
            stop_loss=risk_verdict.suggested_stop,
            dollar_risk=risk_verdict.dollar_risk,
            max_concurrent_positions=self.risk_engine.max_open_positions,
            fee_pct=applied_fee_pct,
            side=chosen_sig.direction.value,
            strategy_id=chosen_sig.strategy_id,
        )

        # Persist the momentum take-profit level so the exit logic can scale out later.
        if chosen_sig.suggested_take_profit > 0 and chosen_sig.take_profit_ratio > 0:
            self.account.set_position_fields(
                symbol,
                tp_target=chosen_sig.suggested_take_profit,
                tp_ratio=chosen_sig.take_profit_ratio,
                tp_hit=False,
            )

        action_name = "ENTER_LONG" if chosen_sig.direction == SignalDirection.LONG else "ENTER_SHORT"
        return CoordinatorDecision(
            symbol=symbol,
            action=action_name,
            strategy_id=chosen_sig.strategy_id,
            units=new_pos["units"],
            price=exec_price,
            stop_loss=new_pos["trailing_stop"],
            take_profit=chosen_sig.suggested_take_profit,
            reason=f"Executed {action_name} via {chosen_sig.strategy_id} ({reason})",
            signal=chosen_sig,
            risk_verdict=risk_verdict,
            indicators=merged_ind,
        )

    def run_cycle(
        self,
        market_data: Dict[str, pd.DataFrame],
    ) -> CycleReport:
        """Run a coordinated portfolio evaluation cycle across all symbols."""
        now = datetime.now(timezone.utc)
        current_prices: Dict[str, float] = {}
        decisions: List[CoordinatorDecision] = []

        conflicts = 0
        executed = 0
        signals_count = 0

        # Relative Strength Prioritization: Evaluate alpha leaders first
        btc_df = market_data.get("BTC/USDT")
        spy_df = market_data.get("SPY")

        def symbol_priority(sym: str) -> float:
            df = market_data[sym]
            bench = btc_df if is_crypto_symbol(sym) else spy_df
            if bench is not None and len(df) >= 24 and len(bench) >= 24:
                return calculate_relative_strength(df["close"], bench["close"], lookback=24)
            return 0.0

        sorted_symbols = sorted(market_data.keys(), key=symbol_priority, reverse=True)

        for symbol in sorted_symbols:
            df = market_data[symbol]
            decision = self.process_symbol(symbol, df, current_prices)
            decision.indicators["rs_score"] = symbol_priority(symbol)
            decisions.append(decision)

            # Persist decision into persistent audit journal
            try:
                log_decision(
                    symbol=symbol,
                    action=decision.action,
                    strategy_id=decision.strategy_id,
                    reason=decision.reason,
                    reference_price=decision.price,
                    indicators=decision.indicators,
                )
            except Exception:
                pass

            if decision.action == "CONFLICT":
                conflicts += 1
            if decision.action in ("ENTER_LONG", "ENTER_SHORT", "EXIT"):
                executed += 1
            if decision.signal is not None and decision.signal.is_actionable:
                signals_count += 1

        total_equity = self.account.get_total_equity(current_prices)

        return CycleReport(
            timestamp=now,
            symbols_evaluated=len(market_data),
            signals_generated=signals_count,
            conflicts_detected=conflicts,
            orders_executed=executed,
            decisions=decisions,
            portfolio_equity=total_equity,
            available_cash=self.account.cash,
            open_positions=len(self.account.positions),
        )
