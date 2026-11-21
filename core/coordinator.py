"""Authoritative Multi-Strategy Portfolio Coordinator.

Reconciles proposals from independent strategy engines (Momentum and Mean Reversion),
resolves conflicts, prevents duplicate orders, enforces centralized risk limits,
and coordinates paper execution and portfolio accounting.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import math
import time
from typing import Dict, Any, List, Optional, Tuple
import numpy as np
import pandas as pd

from core.signals import TradingSignal, SignalDirection, SignalStatus, create_no_trade_signal
from core.risk_engine import RiskEngine, RiskVerdict
from core.paper_account import PaperAccount
from core.data_loader import fetch_funding_rate, is_crypto_symbol
from core.strategy import (
    calculate_multi_timeframe_confluence,
    calculate_relative_strength,
    compute_hurst_exponent,
    detect_liquidation_absorption,
)
from strategies.momentum import MomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.stat_arb_pairs import StatArbPairsStrategy, PairTradeSignal
from strategies.volatility_squeeze import VolatilitySqueezeStrategy
from strategies.trend_pullback import TrendPullbackStrategy
from strategies.vwap_reversion import VwapReversionStrategy
from strategies.nr7_breakout import Nr7BreakoutStrategy
from strategies.range_envelope import RangeEnvelopeStrategy
from strategies.supertrend_cta import SupertrendCtaStrategy
from strategies.donchian_range_fade import DonchianRangeFadeStrategy
from core.regime_playbook import (
    compute_macro_regime,
    extract_causal_daily_return_pct,
    get_playbook_allowed_strategies as _get_playbook_allowed_strategies,
    load_macro_benchmark_df,
)
from core.dynamic_router import select_rolling_strategy
from core.journal import log_decision, get_gate_alpha_feedback, log_maker_telemetry
from core.polymarket_radar import PolymarketRadar, MacroEventRisk
from core.fred_radar import FredMacroRadar, MacroRegimeReport
from core.basis_yield import BasisYieldEngine, BasisOpportunity
from core.preflight import (
    PreFlightChecker,
    PreFlightVerdict,
    LocalLLMScreener,
    fetch_ticker_headlines,
)
from core.skill_intelligence_gate import (
    NyseMarketClock,
    SecEdgar8KMonitor,
    SecFundamentalGate,
    SmartMoneyRadar,
    WashSaleAndLossStreakGuard,
    PortfolioDriftRebalancer,
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
    macro_regime: Optional[str] = None
    macro_return_pct: Optional[float] = None


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

        if self.risk_engine is not None and self.account is not None:
            if "max_drawdown_limit_pct" in config:
                dd_cfg = float(config["max_drawdown_limit_pct"])
                self.risk_engine.max_drawdown_limit_pct = dd_cfg / 100.0 if dd_cfg > 1.0 else dd_cfg
            if "enable_drawdown_throttle" in config:
                self.risk_engine.enable_drawdown_throttle = bool(config["enable_drawdown_throttle"])
            if "drawdown_throttle_pct" in config:
                t_pct = float(config["drawdown_throttle_pct"])
                self.risk_engine.drawdown_throttle_pct = t_pct / 100.0 if t_pct > 1.0 else t_pct
            if "drawdown_throttle_mult" in config:
                self.risk_engine.drawdown_throttle_mult = max(0.25, min(1.0, float(config["drawdown_throttle_mult"])))
            self.risk_engine.bind_account(self.account)

        # Default strategy engines (top-level config)
        self.momentum_strategy = MomentumStrategy(config)
        self.mean_reversion_strategy = MeanReversionStrategy(config)
        # Per-symbol engines built from asset profiles (lazily cached)
        self._symbol_engines: Dict[str, Tuple[Dict[str, Any], MomentumStrategy, MeanReversionStrategy]] = {}
        # Dynamic multi-strategy registry supporting N independent strategies
        self._custom_strategies: Dict[str, Any] = {}
        self._strategy_enabled: Dict[str, bool] = {
            MomentumStrategy.STRATEGY_ID: bool(config.get("enable_momentum", True)),
            MeanReversionStrategy.STRATEGY_ID: bool(config.get("enable_mean_reversion", True)),
            StatArbPairsStrategy.STRATEGY_ID: bool(
                config.get("enable_pairs_trading", config.get("enable_stat_arb_pairs", True))
            ),
            "basis_yield": bool(config.get("enable_basis_yield", True)),
        }
        self._custom_strategies[VolatilitySqueezeStrategy.STRATEGY_ID] = VolatilitySqueezeStrategy(config)
        self._strategy_enabled[VolatilitySqueezeStrategy.STRATEGY_ID] = bool(config.get("enable_volatility_squeeze", True))
        self._custom_strategies[TrendPullbackStrategy.STRATEGY_ID] = TrendPullbackStrategy(config)
        self._strategy_enabled[TrendPullbackStrategy.STRATEGY_ID] = bool(config.get("enable_trend_pullback", True))
        self._custom_strategies[VwapReversionStrategy.STRATEGY_ID] = VwapReversionStrategy(config)
        self._strategy_enabled[VwapReversionStrategy.STRATEGY_ID] = bool(config.get("enable_vwap_reversion", True))
        self._custom_strategies[Nr7BreakoutStrategy.STRATEGY_ID] = Nr7BreakoutStrategy(config)
        self._strategy_enabled[Nr7BreakoutStrategy.STRATEGY_ID] = bool(config.get("enable_nr7_breakout", True))
        self._custom_strategies[RangeEnvelopeStrategy.STRATEGY_ID] = RangeEnvelopeStrategy(config)
        self._strategy_enabled[RangeEnvelopeStrategy.STRATEGY_ID] = bool(config.get("enable_range_envelope", True))
        self._custom_strategies[SupertrendCtaStrategy.STRATEGY_ID] = SupertrendCtaStrategy(config)
        self._strategy_enabled[SupertrendCtaStrategy.STRATEGY_ID] = bool(config.get("enable_supertrend_cta", True))
        self._custom_strategies[DonchianRangeFadeStrategy.STRATEGY_ID] = DonchianRangeFadeStrategy(config)
        self._strategy_enabled[DonchianRangeFadeStrategy.STRATEGY_ID] = bool(config.get("enable_donchian_range_fade", True))
        if "enabled_strategies" in config and isinstance(config["enabled_strategies"], (list, set, tuple)):
            enabled_set = set(config["enabled_strategies"])
            for sid in list(self._strategy_enabled.keys()):
                self._strategy_enabled[sid] = sid in enabled_set
        # Derivatives funding rate cache: symbol -> (timestamp, funding_rate)
        self._funding_cache: Dict[str, Tuple[float, Optional[float]]] = {}
        # Dynamic Rolling Expectancy Router cache: symbol -> (epoch_ts, chosen_strategy)
        self._rolling_router_cache: Dict[str, Tuple[float, str]] = {}
        # Macro Regime Playbook B cache: (epoch_ts, regime_label, return_pct)
        self._macro_regime_cache: Optional[Tuple[float, str, float]] = None
        self.last_macro_regime: str = "CHOPPY"
        self.last_macro_return_pct: float = 0.0
        self._latest_market_data: Dict[str, pd.DataFrame] = {}
        self._latest_benchmark_data: Dict[str, pd.DataFrame] = {}
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
        self.pairs_strategy = StatArbPairsStrategy(config)
        self.last_basis_accrual_time: Optional[datetime] = None
        saved_basis_ts = self.account.state.get("last_basis_accrual_time") if hasattr(self.account, "state") else None
        if saved_basis_ts:
            try:
                parsed_ts = datetime.fromisoformat(str(saved_basis_ts))
                self.last_basis_accrual_time = (
                    parsed_ts.replace(tzinfo=timezone.utc) if parsed_ts.tzinfo is None else parsed_ts
                )
            except Exception:
                self.last_basis_accrual_time = None

        self.pair_cooldown_until: Dict[str, datetime] = {}
        self.pair_stop_counts: Dict[str, int] = {}
        self._load_pair_stop_state()

        # 5 Institutional AGY Skill Gates (trader-alerts, trader-news, trader-analyst, trader-copycat/pelosi, trader-tax/rebalance)
        self.nyse_clock = NyseMarketClock(
            opening_bell_buffer_minutes=int(config.get("nyse_opening_bell_buffer_minutes", 15))
        )
        self.sec_8k_monitor = SecEdgar8KMonitor(
            earnings_cooldown_hours=float(config.get("sec_8k_earnings_cooldown_hours", 24.0)),
            adverse_cooldown_hours=float(config.get("sec_8k_adverse_cooldown_hours", 48.0)),
            sec_user_agent=config.get("sec_user_agent"),
        )
        self.sec_fundamental_gate = SecFundamentalGate(
            min_revenue_growth_yoy=float(config.get("min_sec_revenue_growth_yoy", -5.0)),
            min_operating_margin_pct=float(config.get("min_sec_operating_margin_pct", 0.0)),
        )
        self.smart_money_radar = SmartMoneyRadar()
        self.wash_and_streak_guard = WashSaleAndLossStreakGuard(
            max_consecutive_losses=int(config.get("max_consecutive_ticker_losses", 2)),
            crypto_cooldown_hours=float(config.get("ticker_loss_streak_cooldown_hours", 48.0)),
            equity_wash_sale_days=int(config.get("equity_wash_sale_days", 30)),
            wash_sale_min_loss_usd=float(config.get("wash_sale_min_loss_usd", 50.0)),
        )
        self.drift_rebalancer = PortfolioDriftRebalancer(
            max_position_pct=float(config.get("max_position_pct", 25.0)),
            rebalance_band_pct=float(config.get("rebalance_band_pct", 5.0)),
        )

    def symbol_config(self, symbol: str) -> Dict[str, Any]:
        return self._engines_for(symbol)[0]

    def get_playbook_allowed_strategies(self, regime: str) -> set:
        """Delegate to core.regime_playbook.get_playbook_allowed_strategies."""
        return _get_playbook_allowed_strategies(regime)

    @staticmethod
    def _extract_causal_daily_return_pct(df: Optional[pd.DataFrame], lookback_days: int) -> Optional[float]:
        """Delegate to core.regime_playbook.extract_causal_daily_return_pct."""
        return extract_causal_daily_return_pct(df, lookback_days)

    def _load_macro_benchmark_df(
        self,
        symbol: str,
        market_data: Optional[Dict[str, pd.DataFrame]] = None,
        benchmark_data: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> Optional[pd.DataFrame]:
        """Delegate to core.regime_playbook.load_macro_benchmark_df."""
        return load_macro_benchmark_df(
            symbol,
            benchmark_data=benchmark_data,
            latest_benchmark_data=self._latest_benchmark_data,
            market_data=market_data,
            latest_market_data=self._latest_market_data,
        )

    def get_macro_playbook_regime(
        self,
        market_data: Optional[Dict[str, pd.DataFrame]] = None,
        force_refresh: bool = False,
        benchmark_data: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> Tuple[str, float]:
        """Compute causal macro regime ('BULL', 'CHOPPY', 'BEAR') and 20-day 50/50 benchmark return (%)."""
        now_epoch = time.time()
        ttl = float(self.config.get("macro_regime_cache_seconds", 3600.0))
        if (
            market_data is None
            and benchmark_data is None
            and not force_refresh
            and self._macro_regime_cache is not None
            and (now_epoch - self._macro_regime_cache[0]) < ttl
        ):
            _, cached_regime, cached_ret = self._macro_regime_cache
            return cached_regime, cached_ret

        regime, macro_ret = compute_macro_regime(
            self.config,
            benchmark_data=benchmark_data,
            latest_benchmark_data=self._latest_benchmark_data,
            market_data=market_data,
            latest_market_data=self._latest_market_data,
        )
        self.last_macro_regime = regime
        self.last_macro_return_pct = macro_ret
        self._macro_regime_cache = (now_epoch, regime, macro_ret)
        return regime, macro_ret

    def _resolve_dynamic_rolling_strategy(
        self,
        symbol: str,
        df: pd.DataFrame,
        sym_cfg: Dict[str, Any],
    ) -> str:
        """100% Dynamic Rolling Expectancy & Regime Router.

        Evaluates enabled strategies over a rolling historical window (cached for 6h per
        symbol) and dynamically selects the highest-Sharpe strategy with PF >= threshold,
        or parks choppy crypto in `basis_yield` until a directional trend re-emerges.
        When `enable_macro_regime_playbook` is enabled, restricts directional candidates
        to the Regime Playbook B whitelist for the current causal macro regime.
        """
        playbook_on = bool(self.config.get("enable_macro_regime_playbook", False))
        macro_regime = "ALL"
        allowed_playbook: Optional[set] = None
        if playbook_on:
            macro_regime, _ = self.get_macro_playbook_regime()
            allowed_playbook = self.get_playbook_allowed_strategies(macro_regime)

        if not self.config.get("enable_dynamic_rolling_router", False):
            return "all"

        now_epoch = time.time()
        ttl = float(self.config.get("dynamic_router_cache_seconds", 21600.0))
        cache_key = f"{symbol}:{macro_regime}" if playbook_on else symbol
        cached = self._rolling_router_cache.get(cache_key)
        if cached is not None and (now_epoch - cached[0]) < ttl:
            return cached[1]

        choice = select_rolling_strategy(
            symbol=symbol,
            df=df,
            sym_cfg=sym_cfg,
            config=self.config,
            is_strategy_enabled_fn=self.is_strategy_enabled,
            allowed_playbook=allowed_playbook,
        )
        if choice == "all" and (df is None or len(df) < (120 if not is_crypto_symbol(symbol) else 250)):
            return "all"

        self._rolling_router_cache[cache_key] = (now_epoch, choice)
        return choice

    def register_strategy(self, strategy_id: str, strategy: Any, enabled: bool = True) -> None:
        """Dynamically register an independent strategy plugin with the coordinator."""
        if not strategy_id or not isinstance(strategy_id, str):
            raise ValueError("strategy_id must be a non-empty string")
        self._custom_strategies[strategy_id] = strategy
        self._strategy_enabled[strategy_id] = bool(enabled)

    def unregister_strategy(self, strategy_id: str) -> None:
        """Remove a dynamically registered strategy plugin."""
        self._custom_strategies.pop(strategy_id, None)
        self._strategy_enabled.pop(strategy_id, None)

    def set_strategy_enabled(self, strategy_id: str, enabled: bool) -> None:
        """Enable or disable any registered strategy independently at runtime."""
        self._strategy_enabled[strategy_id] = bool(enabled)

    def is_strategy_enabled(self, strategy_id: str) -> bool:
        """Return whether a strategy is currently active."""
        if "enabled_strategies" in self.config and isinstance(self.config["enabled_strategies"], (list, set, tuple)):
            return strategy_id in set(self.config["enabled_strategies"])
        if strategy_id == MomentumStrategy.STRATEGY_ID and "enable_momentum" in self.config:
            return bool(self.config["enable_momentum"])
        if strategy_id == MeanReversionStrategy.STRATEGY_ID and "enable_mean_reversion" in self.config:
            return bool(self.config["enable_mean_reversion"])
        if strategy_id == StatArbPairsStrategy.STRATEGY_ID:
            if "enable_pairs_trading" in self.config:
                return bool(self.config["enable_pairs_trading"])
            if "enable_stat_arb_pairs" in self.config:
                return bool(self.config["enable_stat_arb_pairs"])
        if strategy_id == "basis_yield" and "enable_basis_yield" in self.config:
            return bool(self.config["enable_basis_yield"])
        return bool(self._strategy_enabled.get(strategy_id, True))

    def get_registered_strategies(self) -> Dict[str, Dict[str, Any]]:
        """Return metadata and active state for all registered strategies in the Web4 system."""
        registry: Dict[str, Dict[str, Any]] = {
            MomentumStrategy.STRATEGY_ID: {
                "strategy_id": MomentumStrategy.STRATEGY_ID,
                "name": "Donchian Momentum Breakout",
                "type": "single_asset_directional",
                "enabled": self.is_strategy_enabled(MomentumStrategy.STRATEGY_ID),
                "instance": self.momentum_strategy,
            },
            MeanReversionStrategy.STRATEGY_ID: {
                "strategy_id": MeanReversionStrategy.STRATEGY_ID,
                "name": "Bollinger/RSI Mean Reversion",
                "type": "single_asset_directional",
                "enabled": self.is_strategy_enabled(MeanReversionStrategy.STRATEGY_ID),
                "instance": self.mean_reversion_strategy,
            },
            StatArbPairsStrategy.STRATEGY_ID: {
                "strategy_id": StatArbPairsStrategy.STRATEGY_ID,
                "name": "Cointegrated Statistical Arbitrage Pairs",
                "type": "multi_asset_relative_value",
                "enabled": self.is_strategy_enabled(StatArbPairsStrategy.STRATEGY_ID),
                "instance": self.pairs_strategy,
            },
            "basis_yield": {
                "strategy_id": "basis_yield",
                "name": "Delta-Neutral Perpetual Basis Yield",
                "type": "delta_neutral_carry",
                "enabled": self.is_strategy_enabled("basis_yield"),
                "instance": self.basis_yield_engine,
            },
        }
        for sid, strat in self._custom_strategies.items():
            registry[sid] = {
                "strategy_id": sid,
                "name": getattr(strat, "STRATEGY_NAME", sid),
                "type": "custom_plugin",
                "enabled": self.is_strategy_enabled(sid),
                "instance": strat,
            }
        return registry

    def reload_config(self, config: Dict[str, Any]) -> None:
        """Hot-reload configuration and rebuild strategy engines without resetting portfolio state."""
        self.config = config
        self.momentum_strategy = MomentumStrategy(config)
        self.mean_reversion_strategy = MeanReversionStrategy(config)
        self.pairs_strategy = StatArbPairsStrategy(config)
        if VolatilitySqueezeStrategy.STRATEGY_ID in self._custom_strategies:
            self._custom_strategies[VolatilitySqueezeStrategy.STRATEGY_ID] = VolatilitySqueezeStrategy(config)
        if TrendPullbackStrategy.STRATEGY_ID in self._custom_strategies:
            self._custom_strategies[TrendPullbackStrategy.STRATEGY_ID] = TrendPullbackStrategy(config)
        if VwapReversionStrategy.STRATEGY_ID in self._custom_strategies:
            self._custom_strategies[VwapReversionStrategy.STRATEGY_ID] = VwapReversionStrategy(config)
        if Nr7BreakoutStrategy.STRATEGY_ID in self._custom_strategies:
            self._custom_strategies[Nr7BreakoutStrategy.STRATEGY_ID] = Nr7BreakoutStrategy(config)
        if RangeEnvelopeStrategy.STRATEGY_ID in self._custom_strategies:
            self._custom_strategies[RangeEnvelopeStrategy.STRATEGY_ID] = RangeEnvelopeStrategy(config)
        if SupertrendCtaStrategy.STRATEGY_ID in self._custom_strategies:
            self._custom_strategies[SupertrendCtaStrategy.STRATEGY_ID] = SupertrendCtaStrategy(config)
        if DonchianRangeFadeStrategy.STRATEGY_ID in self._custom_strategies:
            self._custom_strategies[DonchianRangeFadeStrategy.STRATEGY_ID] = DonchianRangeFadeStrategy(config)

        # Refresh strategy enable flags dynamically on hot-reload
        self._strategy_enabled[MomentumStrategy.STRATEGY_ID] = bool(config.get("enable_momentum", True))
        self._strategy_enabled[MeanReversionStrategy.STRATEGY_ID] = bool(config.get("enable_mean_reversion", True))
        self._strategy_enabled[StatArbPairsStrategy.STRATEGY_ID] = bool(
            config.get("enable_pairs_trading", config.get("enable_stat_arb_pairs", True))
        )
        self._strategy_enabled["basis_yield"] = bool(config.get("enable_basis_yield", True))
        self._strategy_enabled[VolatilitySqueezeStrategy.STRATEGY_ID] = bool(config.get("enable_volatility_squeeze", True))
        self._strategy_enabled[TrendPullbackStrategy.STRATEGY_ID] = bool(config.get("enable_trend_pullback", True))
        self._strategy_enabled[VwapReversionStrategy.STRATEGY_ID] = bool(config.get("enable_vwap_reversion", True))
        self._strategy_enabled[Nr7BreakoutStrategy.STRATEGY_ID] = bool(config.get("enable_nr7_breakout", True))
        self._strategy_enabled[RangeEnvelopeStrategy.STRATEGY_ID] = bool(config.get("enable_range_envelope", True))
        self._strategy_enabled[SupertrendCtaStrategy.STRATEGY_ID] = bool(config.get("enable_supertrend_cta", True))
        self._strategy_enabled[DonchianRangeFadeStrategy.STRATEGY_ID] = bool(config.get("enable_donchian_range_fade", True))
        if "enabled_strategies" in config and isinstance(config["enabled_strategies"], (list, set, tuple)):
            enabled_set = set(config["enabled_strategies"])
            for sid in list(self._strategy_enabled.keys()):
                self._strategy_enabled[sid] = sid in enabled_set

        self._symbol_engines.clear()
        self._rolling_router_cache.clear()
        self._macro_regime_cache = None
        self.llm_screener.enabled = bool(config.get("enable_local_llm_preflight", self.llm_screener.enabled))
        self.llm_screener.model = config.get("local_llm_model", self.llm_screener.model)
        self.basis_yield_engine.hurdle_apr = float(config.get("basis_hurdle_apr", self.basis_yield_engine.hurdle_apr))
        self.basis_yield_engine.max_portfolio_allocation_pct = float(
            config.get("max_basis_allocation_pct", self.basis_yield_engine.max_portfolio_allocation_pct)
        )

        if self.risk_engine is not None:
            self.risk_engine.risk_pct = self.risk_engine.enforce_risk_bounds(
                config.get("account_risk_pct", self.risk_engine.risk_pct)
            )
            self.risk_engine.max_open_positions = int(
                config.get("max_concurrent_positions", self.risk_engine.max_open_positions)
            )
            self.risk_engine.max_strategy_allocation_pct = float(
                config.get("max_strategy_allocation_pct", self.risk_engine.max_strategy_allocation_pct)
            )
            self.risk_engine.max_crypto_positions = int(
                config.get("max_crypto_positions", self.risk_engine.max_crypto_positions)
            )
            if "symbol_risk_weights" in config and config["symbol_risk_weights"]:
                self.risk_engine.symbol_risk_weights = {
                    k: self.risk_engine.enforce_risk_bounds(v)
                    for k, v in config["symbol_risk_weights"].items()
                }
            if "symbol_exposure_caps" in config and config["symbol_exposure_caps"]:
                self.risk_engine.symbol_exposure_caps = dict(config["symbol_exposure_caps"])

    def _engines_for(self, symbol: str) -> Tuple[Dict[str, Any], MomentumStrategy, MeanReversionStrategy]:
        if symbol not in self._symbol_engines:
            cfg = resolve_symbol_config(self.config, symbol)
            self._symbol_engines[symbol] = (cfg, MomentumStrategy(cfg), MeanReversionStrategy(cfg))
        return self._symbol_engines[symbol]

    def _pair_slippage_pct(self) -> float:
        """Per-side adverse slippage applied to every pair leg fill (pairs always cross the spread)."""
        return float(self.config.get("pair_slippage_pct", self.config.get("slippage_pct", 0.0004)))

    def _pair_fill_price(self, price: float, side: str, opening: bool) -> float:
        """Apply adverse slippage to a pair-leg fill.

        Buying (opening a LONG or closing a SHORT) pays up; selling (opening a SHORT or
        closing a LONG) receives less. Previously pair legs filled at the raw bar price, which
        understated the real round-trip friction by 4 fills x slippage.
        """
        slip = self._pair_slippage_pct()
        buying = (side == "LONG") if opening else (side == "SHORT")
        return price * (1.0 + slip) if buying else price * (1.0 - slip)

    def _load_pair_stop_state(self) -> None:
        """Restore persisted pair cooldowns and stop-out strike counts from account state."""
        state = getattr(self.account, "state", None)
        if not isinstance(state, dict):
            return
        raw_cooldowns = state.get("pair_cooldown_until", {})
        if isinstance(raw_cooldowns, dict):
            for pid, ts_str in raw_cooldowns.items():
                try:
                    parsed = datetime.fromisoformat(str(ts_str))
                    self.pair_cooldown_until[str(pid)] = (
                        parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
                    )
                except Exception:
                    continue
        raw_counts = state.get("pair_stop_counts", {})
        if isinstance(raw_counts, dict):
            for pid, cnt in raw_counts.items():
                try:
                    c_val = int(cnt)
                except Exception:
                    continue
                self.pair_stop_counts[str(pid)] = c_val
                if c_val >= 2:
                    self.pairs_strategy.blacklisted_pairs.add(str(pid))

    def _record_pair_stop(self, pair_id: str, now: datetime) -> None:
        """Record a pair stop-out, apply cooldown/2-strike blacklist, and persist to disk."""
        if not pair_id:
            return
        if not hasattr(self, "pair_cooldown_until"):
            self.pair_cooldown_until = {}
        if not hasattr(self, "pair_stop_counts"):
            self.pair_stop_counts = {}
        cooldown_hours = float(self.config.get("pair_stop_cooldown_hours", 12.0))
        self.pair_cooldown_until[pair_id] = now + timedelta(hours=cooldown_hours)
        self.pair_stop_counts[pair_id] = self.pair_stop_counts.get(pair_id, 0) + 1
        if self.pair_stop_counts[pair_id] >= 2:
            self.pairs_strategy.blacklisted_pairs.add(pair_id)

        state = getattr(self.account, "state", None)
        if isinstance(state, dict):
            state["pair_cooldown_until"] = {
                k: v.isoformat() for k, v in self.pair_cooldown_until.items()
            }
            state["pair_stop_counts"] = dict(self.pair_stop_counts)
            if hasattr(self.account, "_save"):
                self.account._save()

    @staticmethod
    def _recent_atr_pct(df: Optional[pd.DataFrame], window: int = 14) -> float:
        """Estimate recent per-bar volatility as mean (high-low)/close or |close diff|/close."""
        if df is None or len(df) < 3 or "close" not in df.columns:
            return 0.0
        sub = df.iloc[-max(3, window):]
        close = pd.to_numeric(sub["close"], errors="coerce")
        if "high" in sub.columns and "low" in sub.columns:
            high = pd.to_numeric(sub["high"], errors="coerce")
            low = pd.to_numeric(sub["low"], errors="coerce")
            bar_range = (high - low).abs()
        else:
            bar_range = close.diff().abs()
        pct = (bar_range / close.replace(0.0, np.nan)).dropna()
        return float(pct.mean()) if len(pct) > 0 else 0.0

    def _compute_pair_leg_sizing(
        self,
        df_a: Optional[pd.DataFrame],
        df_b: Optional[pd.DataFrame],
        total_eq: float,
        dollar_risk: float,
    ) -> Tuple[float, float, float]:
        """Compute volatility-scaled leg stop_pct, base_leg_notional, and total_cost cap.

        Prevents low-cap/volatile legs from sizing to the full 15% equity cap on a tight 5%
        stop where a single-bar gap can exceed intended dollar_risk by 3x+.
        """
        base_stop_pct = float(self.config.get("stat_arb_leg_stop_pct", 0.05))
        max_atr_pct = max(self._recent_atr_pct(df_a), self._recent_atr_pct(df_b))
        effective_stop_pct = min(0.25, max(base_stop_pct, 2.5 * max_atr_pct))

        max_leg_cap = float(self.config.get("stat_arb_max_leg_equity_pct", 0.15))
        if max_atr_pct >= 0.03:
            vol_leg_cap = min(max_leg_cap, 0.07)
        elif max_atr_pct >= 0.015:
            vol_leg_cap = min(max_leg_cap, 0.10)
        else:
            vol_leg_cap = max_leg_cap

        base_leg_notional = min(dollar_risk / max(0.01, effective_stop_pct), total_eq * vol_leg_cap)
        total_cost = min(base_leg_notional * 2.0, total_eq * (vol_leg_cap * 2.0), self.account.cash * 0.90)
        return effective_stop_pct, base_leg_notional, total_cost

    def _resolve_funding_state(
        self,
        symbol: str,
        signals: List[TradingSignal],
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[str], Optional[float], bool, str]:
        """Resolve and validate unified funding regime and funding rate across context, signals, and config.

        Returns:
            (funding_regime, funding_rate_pct, is_valid, error_reason)
        """
        sym_cfg = self.symbol_config(symbol) if symbol else {}
        ctx = context or {}
        valid_regimes = {"NEUTRAL", "OVERHEATED_LONGS", "SHORT_SQUEEZE_PRONE", "OVERHEATED_SHORTS"}

        # 1. Extract raw funding_rate_pct and funding_regime in priority order: context -> signals -> sym_cfg -> config
        raw_rate = ctx.get("funding_rate_pct")
        raw_regime = ctx.get("funding_regime")

        for sig in signals:
            ind = sig.indicators or {}
            if raw_rate is None and "funding_rate_pct" in ind:
                raw_rate = ind.get("funding_rate_pct")
            if raw_regime is None and "funding_regime" in ind:
                raw_regime = ind.get("funding_regime")

        if raw_rate is None and "funding_rate_pct" in sym_cfg:
            raw_rate = sym_cfg.get("funding_rate_pct")
        if raw_rate is None and "funding_rate_pct" in self.config:
            raw_rate = self.config.get("funding_rate_pct")

        if raw_regime is None and "funding_regime" in sym_cfg:
            raw_regime = sym_cfg.get("funding_regime")
        if raw_regime is None and "funding_regime" in self.config:
            raw_regime = self.config.get("funding_regime")

        # 2. Validate raw_rate if provided
        parsed_rate: Optional[float] = None
        if raw_rate is not None:
            try:
                parsed_rate = float(raw_rate)
                if pd.isna(parsed_rate) or np.isinf(parsed_rate):
                    return None, None, False, "Blocked by Funding Gate: Invalid NaN/Inf funding rate data"
            except (TypeError, ValueError):
                return None, None, False, f"Blocked by Funding Gate: Malformed funding rate '{raw_rate}'"

        # 3. Validate raw_regime if provided
        parsed_regime: Optional[str] = None
        if raw_regime is not None:
            if not isinstance(raw_regime, str) or raw_regime.strip().upper() not in valid_regimes:
                return None, parsed_rate, False, f"Blocked by Funding Gate: Invalid funding regime '{raw_regime}'"
            parsed_regime = raw_regime.strip().upper()

        # 4. Derive regime from rate if regime was not explicitly provided
        max_fr_pct = float(sym_cfg.get("max_funding_rate_pct", self.config.get("max_funding_rate_pct", 0.05)))
        min_fr_pct = float(sym_cfg.get("min_funding_rate_pct", self.config.get("min_funding_rate_pct", -0.02)))
        if parsed_regime is None and parsed_rate is not None:
            if parsed_rate > max_fr_pct:
                parsed_regime = "OVERHEATED_LONGS"
            elif parsed_rate < min_fr_pct:
                parsed_regime = "SHORT_SQUEEZE_PRONE"
            else:
                parsed_regime = "NEUTRAL"

        # 5. If require_funding_data is set for crypto symbols, fail closed if funding data is missing
        require_funding = bool(sym_cfg.get("require_funding_data", self.config.get("require_funding_data", False)))
        if require_funding and parsed_regime is None and parsed_rate is None:
            return None, None, False, "Blocked by Funding Gate: Missing required funding data"

        return parsed_regime, parsed_rate, True, ""

    def reconcile_signals(
        self,
        symbol: str,
        momentum_sig: TradingSignal,
        mr_sig: TradingSignal,
        extra_signals: Optional[List[TradingSignal]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[TradingSignal], str]:
        """Reconcile signals across all registered directional strategies with Automated Regime Awareness.

        Rules:
        1. Macro Bear Gate: If price < macro_ema (200 EMA), block Long breakout entries
           (protects capital from bear-market cascade liquidation).
        2. Range Chop Gate: If ADX < adx_chop_threshold (default 20.0), suppress breakout
           entries and prioritize Mean Reversion.
        3. Strong Trend Gate: If ADX >= adx_trend_threshold (default 25.0), suppress
           counter-trend Mean Reversion.
        4. Funding Rate Overheat & Squeeze Gate: Reads unified funding regime from context,
           signals, and coordinator config; blocks Long entries across all strategies when
           OVERHEATED_LONGS, blocks Short entries when SHORT_SQUEEZE_PRONE / OVERHEATED_SHORTS,
           and fails safely when funding data is malformed or missing when required.
        5. Multi-Strategy Conflict / Tiebreak resolution across N registered strategies.
        """
        all_signals: List[TradingSignal] = [momentum_sig, mr_sig]
        if extra_signals:
            all_signals.extend([s for s in extra_signals if s is not None])

        mom_entry = (momentum_sig.status == SignalStatus.ENTRY)
        mr_entry = (mr_sig.status == SignalStatus.ENTRY)

        enable_regime_filter = self.config.get("enable_regime_filter", True)
        adx_chop_threshold = self.config.get("adx_chop_threshold", 20.0)
        adx_trend_threshold = self.config.get("adx_trend_threshold", 25.0)

        mom_ind = dict(momentum_sig.indicators or {})
        mr_ind = dict(mr_sig.indicators or {})
        cur_price = momentum_sig.reference_price or mr_sig.reference_price or (
            extra_signals[0].reference_price if extra_signals else 0.0
        )

        # Authoritative Funding Gate (applies whenever funding regime/data is present or required)
        funding_regime, funding_rate_pct, funding_valid, funding_err = self._resolve_funding_state(
            symbol, all_signals, context=context
        )
        if not funding_valid:
            return None, funding_err

        if funding_regime is not None:
            mom_ind["funding_regime"] = funding_regime
            mr_ind["funding_regime"] = funding_regime
            if context is not None:
                context["funding_regime"] = funding_regime
        if funding_rate_pct is not None:
            mom_ind["funding_rate_pct"] = funding_rate_pct
            mr_ind["funding_rate_pct"] = funding_rate_pct
            if context is not None:
                context["funding_rate_pct"] = funding_rate_pct

        if enable_regime_filter:
            ctx = context or {}
            macro_ema = mom_ind.get("macro_ema") or mr_ind.get("macro_ema") or ctx.get("macro_ema")
            adx = mr_ind.get("adx") if "adx" in mr_ind else mom_ind.get("adx", ctx.get("adx"))

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
                        indicators=mom_ind,
                    )

            # Rule 2: Range Chop Gate (Suppress breakouts when ADX < chop threshold)
            if adx is not None and adx < adx_chop_threshold and mom_entry:
                mom_entry = False
                momentum_sig = create_no_trade_signal(
                    "momentum", symbol, cur_price,
                    f"Blocked by Chop Gate (ADX {adx:.1f} < {adx_chop_threshold:.1f})",
                    indicators=mom_ind,
                )

            # Rule 3: Strong Trend Gate (Suppress counter-trend MR when ADX >= trend threshold)
            if adx is not None and adx >= adx_trend_threshold and mr_entry:
                mr_entry = False
                mr_sig = create_no_trade_signal(
                    "mean_reversion", symbol, cur_price,
                    f"Suppressed counter-trend MR in strong trend (ADX {adx:.1f} >= {adx_trend_threshold:.1f})",
                    indicators=mr_ind,
                )

            # Rule 4: Funding Rate Overheat & Squeeze Filter (propagated across all strategies)
            fr_display = funding_rate_pct if funding_rate_pct is not None else 0.0
            if funding_regime == "OVERHEATED_LONGS":
                if mom_entry and momentum_sig.direction == SignalDirection.LONG:
                    mom_entry = False
                    momentum_sig = create_no_trade_signal(
                        "momentum", symbol, cur_price,
                        f"Blocked by Overheated Funding Rate ({fr_display:+.3f}% per 8h)",
                        indicators=mom_ind,
                    )
                if mr_entry and mr_sig.direction == SignalDirection.LONG:
                    mr_entry = False
                    mr_sig = create_no_trade_signal(
                        "mean_reversion", symbol, cur_price,
                        f"Blocked by Overheated Funding Rate ({fr_display:+.3f}% per 8h)",
                        indicators=mr_ind,
                    )
            elif funding_regime in ("SHORT_SQUEEZE_PRONE", "OVERHEATED_SHORTS"):
                if mom_entry and momentum_sig.direction == SignalDirection.SHORT:
                    mom_entry = False
                    momentum_sig = create_no_trade_signal(
                        "momentum", symbol, cur_price,
                        f"Blocked by Negative Funding Squeeze Risk ({fr_display:+.3f}% per 8h)",
                        indicators=mom_ind,
                    )
                if mr_entry and mr_sig.direction == SignalDirection.SHORT:
                    mr_entry = False
                    mr_sig = create_no_trade_signal(
                        "mean_reversion", symbol, cur_price,
                        f"Blocked by Negative Funding Squeeze Risk ({fr_display:+.3f}% per 8h)",
                        indicators=mr_ind,
                    )

            # Rule 5: Macro Crash Defense Gate (VIX Panic & Credit Spread Radar)
            if self.config.get("enable_vix_regime_filter", False):
                macro_report = self.fred_radar.evaluate()
                if enable_regime_filter and not macro_report.allow_long_breakouts and mom_entry and momentum_sig.direction == SignalDirection.LONG:
                    mom_entry = False
                    momentum_sig = create_no_trade_signal(
                        "momentum", symbol, cur_price,
                        f"Blocked by Macro Crash Defense ({macro_report.reason})",
                        indicators=mom_ind,
                    )

        # Also apply funding gate to any extra registered strategy signals
        filtered_extra: List[TradingSignal] = []
        for esig in (extra_signals or []):
            if esig is None:
                continue
            e_ind = dict(esig.indicators or {})
            if funding_regime is not None:
                e_ind["funding_regime"] = funding_regime
            if funding_rate_pct is not None:
                e_ind["funding_rate_pct"] = funding_rate_pct
            if enable_regime_filter and esig.status == SignalStatus.ENTRY:
                fr_display = funding_rate_pct if funding_rate_pct is not None else 0.0
                if funding_regime == "OVERHEATED_LONGS" and esig.direction == SignalDirection.LONG:
                    esig = create_no_trade_signal(
                        esig.strategy_id, symbol, cur_price,
                        f"Blocked by Overheated Funding Rate ({fr_display:+.3f}% per 8h)",
                        indicators=e_ind,
                    )
                elif funding_regime in ("SHORT_SQUEEZE_PRONE", "OVERHEATED_SHORTS") and esig.direction == SignalDirection.SHORT:
                    esig = create_no_trade_signal(
                        esig.strategy_id, symbol, cur_price,
                        f"Blocked by Negative Funding Squeeze Risk ({fr_display:+.3f}% per 8h)",
                        indicators=e_ind,
                    )
            filtered_extra.append(esig)

        # If extra custom strategies are present and proposing entries, reconcile across all N strategies
        extra_entries = [s for s in filtered_extra if s.status == SignalStatus.ENTRY]
        if extra_entries:
            active_entries: List[TradingSignal] = []
            if mom_entry:
                active_entries.append(momentum_sig)
            if mr_entry:
                active_entries.append(mr_sig)
            active_entries.extend(extra_entries)

            directions = {s.direction for s in active_entries}
            if len(directions) > 1:
                strat_desc = ", ".join(f"{s.strategy_id}={s.direction.value}" for s in active_entries)
                return None, f"Conflict: Opposing multi-strategy signals ({strat_desc})"
            best_sig = max(active_entries, key=lambda s: float(s.signal_strength))
            if len(active_entries) == 1:
                return best_sig, f"{best_sig.strategy_id} entry proposed"
            return best_sig, (
                f"Multi-strategy agreement on {best_sig.direction.value}; "
                f"selected {best_sig.strategy_id} (strength {best_sig.signal_strength:.2f})"
            )

        # Case 1: Both Momentum and Mean Reversion propose entries
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
        for esig in filtered_extra:
            if esig.no_trade_reason:
                return None, esig.no_trade_reason
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

        cur_price = float(df.attrs.get("live_price", df.iloc[-1]["close"]))
        current_prices[symbol] = cur_price
        existing_pos = self.account.get_position(symbol)
        sym_cfg, momentum_strategy, mean_reversion_strategy = self._engines_for(symbol)

        # Asset Pruning Defense: Reject blacklisted underperforming assets unless exiting an open position
        blacklisted = self.config.get("blacklisted_symbols", [])
        if symbol in blacklisted and existing_pos is None:
            return CoordinatorDecision(
                symbol=symbol,
                action="REJECTED",
                strategy_id="coordinator",
                reason=f"Asset Pruned: {symbol} is blacklisted due to historical underperformance",
            )

        # 1. Evaluate exit conditions if already holding
        if existing_pos is not None:
            pos_strategy = existing_pos.get("strategy_id", "momentum")
            if pos_strategy == "stat_arb_pairs":
                pair_stop = float(existing_pos.get("trailing_stop", 0.0))
                pos_side = existing_pos.get("side", "LONG")
                if pair_stop > 0.0 and (
                    (pos_side == "LONG" and cur_price <= pair_stop)
                    or (pos_side == "SHORT" and cur_price >= pair_stop)
                ):
                    pair_id_val = existing_pos.get("pair_id")
                    trade = self.account.close_position(
                        symbol=symbol,
                        exit_price=self._pair_fill_price(cur_price, pos_side, opening=False),
                        exit_reason=f"Pair Leg Catastrophe Stop Hit (${cur_price:.2f} vs ${pair_stop:.2f})",
                        fee_pct=sym_cfg.get("fee_pct", 0.0006),
                    )
                    if pair_id_val:
                        for other_sym, other_pos in list(self.account.positions.items()):
                            if other_pos.get("pair_id") == pair_id_val:
                                other_px = current_prices.get(other_sym, float(other_pos.get("entry_price", 0.0)))
                                self.account.close_position(
                                    symbol=other_sym,
                                    exit_price=self._pair_fill_price(other_px, other_pos.get("side", "LONG"), opening=False),
                                    exit_reason=f"Partner Leg Catastrophe Stop ({pair_id_val})",
                                    fee_pct=sym_cfg.get("fee_pct", 0.0006),
                                )
                        self._record_pair_stop(pair_id_val, datetime.now(timezone.utc))
                    return CoordinatorDecision(
                        symbol=symbol,
                        action="EXIT",
                        strategy_id="stat_arb_pairs",
                        units=trade["units"],
                        price=trade["exit_price"],
                        stop_loss=pair_stop,
                        reason=trade["exit_reason"],
                    )
                # Pairs positions are managed by dynamic pairs pass; hold during single-asset evaluation
                return CoordinatorDecision(
                    symbol=symbol,
                    action="HOLD",
                    strategy_id="stat_arb_pairs",
                    units=existing_pos.get("units", 0.0),
                    price=cur_price,
                    stop_loss=pair_stop,
                    reason=f"Holding pair position {existing_pos.get('pair_id', 'dynamic_pair')}",
                )
            elif pos_strategy == MeanReversionStrategy.STRATEGY_ID:
                sig = mean_reversion_strategy.evaluate(symbol, df, existing_position=existing_pos)
            elif pos_strategy in self._custom_strategies:
                sig = self._custom_strategies[pos_strategy].evaluate(symbol, df, existing_position=existing_pos)
            else:
                sig = momentum_strategy.evaluate(symbol, df, existing_position=existing_pos)

            raw_bar_ts = df.index[-1] if isinstance(df.index[-1], pd.Timestamp) else pd.Timestamp.now(tz="UTC")
            bar_exit_dt = (
                raw_bar_ts.tz_localize("UTC").to_pydatetime()
                if raw_bar_ts.tzinfo is None
                else (raw_bar_ts.tz_convert("UTC").to_pydatetime() if hasattr(raw_bar_ts, "tz_convert") else raw_bar_ts)
            )

            if sig.status == SignalStatus.EXIT:
                trade = self.account.close_position(
                    symbol=symbol,
                    exit_price=cur_price * (0.9996 if sig.direction == SignalDirection.LONG else 1.0004),
                    exit_reason=sig.exit_reason or "Strategy Exit Signal",
                    fee_pct=sym_cfg.get("fee_pct", 0.0006),
                    current_time=bar_exit_dt,
                )
                if getattr(self.risk_engine, "in_probation", False):
                    self.risk_engine.record_probation_trade_outcome(
                        pnl=float(trade.get("pnl", 0.0)),
                        current_equity=self.account.get_total_equity(current_prices),
                        current_time=bar_exit_dt,
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
                    current_time=bar_exit_dt,
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
                # Respect native stop management for Supertrend CTA and Donchian Range Fade
                is_short = (existing_pos.get("side", "LONG") == "SHORT")
                if pos_strategy == SupertrendCtaStrategy.STRATEGY_ID and sig.suggested_stop > 0:
                    cur_stop = float(existing_pos.get("trailing_stop", sig.suggested_stop))
                    new_stop = min(cur_stop, sig.suggested_stop) if is_short else max(cur_stop, sig.suggested_stop)
                    self.account.update_trailing_stop(symbol, new_stop, cur_price)
                    return CoordinatorDecision(
                        symbol=symbol,
                        action="HOLD",
                        strategy_id=pos_strategy,
                        units=existing_pos["units"],
                        price=cur_price,
                        stop_loss=new_stop,
                        reason="Position active, Supertrend line stop updated",
                        signal=sig,
                    )
                if pos_strategy == DonchianRangeFadeStrategy.STRATEGY_ID:
                    fixed_stop = float(existing_pos.get("trailing_stop", sig.suggested_stop or 0.0))
                    return CoordinatorDecision(
                        symbol=symbol,
                        action="HOLD",
                        strategy_id=pos_strategy,
                        units=existing_pos["units"],
                        price=cur_price,
                        stop_loss=fixed_stop,
                        reason="Position active, targeting Donchian midline",
                        signal=sig,
                    )

                # Ratchet trailing stop if applicable
                ind_atr = float((sig.indicators or {}).get("atr", 0.0) or 0.0)
                atr = sig.atr if sig.atr > 0 else (ind_atr if ind_atr > 0 else (cur_price * 0.02))
                atr_mult = sym_cfg.get("atr_multiplier", 2.5)
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
                        units_held = float(existing_pos.get("units", 0.0))
                        notional_held = max(1.0, units_held * ep) if units_held > 0 else 2500.0
                        fee_lock_pct = (12.0 / notional_held) + 0.0016
                        be_stop = ep * max(1.0015, 1.0 + fee_lock_pct)
                        peak_px = max(cur_price, float(existing_pos.get("highest_price", cur_price)))
                        if tp_hit or (peak_px - ep) >= (min(1.0, be_atr_mult) * atr):
                            new_stop = max(new_stop, be_stop)
                    else:
                        be_stop = ep * 0.9985
                        if tp_hit or (ep - cur_price) >= (be_atr_mult * atr):
                            new_stop = min(new_stop, be_stop)

                # Upgrade 3: Adaptive Chandelier Trailing Ratchet on Trend Runners
                if self.config.get("enable_chandelier_ratchet", True):
                    ep = existing_pos["entry_price"]
                    init_stop = existing_pos.get("initial_stop")
                    if init_stop is not None and abs(ep - init_stop) > 0:
                        init_risk = abs(ep - init_stop)
                    else:
                        init_risk = atr * atr_mult
                    unrealized_gain = (cur_price - ep) if not is_short else (ep - cur_price)
                    r_mult = unrealized_gain / max(1e-6, init_risk)

                    if r_mult >= 8.0:
                        parabolic_stop = (cur_price - 0.75 * atr) if not is_short else (cur_price + 0.75 * atr)
                        new_stop = max(new_stop, parabolic_stop) if not is_short else min(new_stop, parabolic_stop)
                    elif r_mult >= 5.0:
                        tight_stop = (cur_price - 1.2 * atr) if not is_short else (cur_price + 1.2 * atr)
                        new_stop = max(new_stop, tight_stop) if not is_short else min(new_stop, tight_stop)
                    elif r_mult >= 3.0:
                        profit_lock = (ep + 1.0 * atr) if not is_short else (ep - 1.0 * atr)
                        new_stop = max(new_stop, profit_lock) if not is_short else min(new_stop, profit_lock)

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

        # 2. No open position: generate signals from all enabled directional strategies
        playbook_on = bool(self.config.get("enable_macro_regime_playbook", False))
        playbook_regime = ""
        playbook_allowed: Optional[set] = None
        if playbook_on:
            playbook_regime, _ = self.get_macro_playbook_regime()
            playbook_allowed = self.get_playbook_allowed_strategies(playbook_regime)

        dyn_route = self._resolve_dynamic_rolling_strategy(symbol, df, sym_cfg)
        allow_mom_dyn = dyn_route in ("all", MomentumStrategy.STRATEGY_ID) and (
            playbook_allowed is None or MomentumStrategy.STRATEGY_ID in playbook_allowed
        )
        allow_mr_dyn = dyn_route in ("all", MeanReversionStrategy.STRATEGY_ID) and (
            playbook_allowed is None or MeanReversionStrategy.STRATEGY_ID in playbook_allowed
        )

        if self.is_strategy_enabled(MomentumStrategy.STRATEGY_ID) and allow_mom_dyn:
            mom_sig = momentum_strategy.evaluate(symbol, df, existing_position=None)
        else:
            mom_sig = create_no_trade_signal(
                strategy_id=MomentumStrategy.STRATEGY_ID,
                symbol=symbol,
                price=cur_price,
                reason=(
                    f"Paused by Regime Playbook B (Macro={playbook_regime})"
                    if playbook_allowed is not None and MomentumStrategy.STRATEGY_ID not in playbook_allowed
                    else (
                        f"Dynamic Router selected '{dyn_route}' for {symbol}"
                        if not allow_mom_dyn
                        else "Momentum strategy disabled"
                    )
                ),
            )

        # Altcoin Strategy Specialization: Restrict high-beta crypto altcoins to Momentum only
        is_altcoin = is_crypto_symbol(symbol) and symbol.upper() not in ("BTC/USDT", "ETH/USDT")
        suppress_mr = is_altcoin and self.config.get("crypto_altcoin_momentum_only", False)
        hurst_gate_on = bool(self.config.get("enable_hurst_gate", False))
        hurst_mr_max = float(self.config.get("hurst_mr_max", 0.52))
        hurst_val = compute_hurst_exponent(df["close"], max_lag=20) if hurst_gate_on else None

        if not self.is_strategy_enabled(MeanReversionStrategy.STRATEGY_ID) or not allow_mr_dyn:
            mr_sig = create_no_trade_signal(
                strategy_id=MeanReversionStrategy.STRATEGY_ID,
                symbol=symbol,
                price=cur_price,
                reason=(
                    f"Paused by Regime Playbook B (Macro={playbook_regime})"
                    if playbook_allowed is not None and MeanReversionStrategy.STRATEGY_ID not in playbook_allowed
                    else (
                        f"Dynamic Router selected '{dyn_route}' for {symbol}"
                        if not allow_mr_dyn
                        else "Mean-Reversion strategy disabled"
                    )
                ),
            )
        elif suppress_mr:
            mr_sig = create_no_trade_signal(
                strategy_id=MeanReversionStrategy.STRATEGY_ID,
                symbol=symbol,
                price=cur_price,
                reason="Altcoin Mean-Reversion Suppressed (Momentum Only)",
            )
        elif hurst_gate_on and hurst_val is not None and hurst_val >= hurst_mr_max:
            mr_sig = create_no_trade_signal(
                strategy_id=MeanReversionStrategy.STRATEGY_ID,
                symbol=symbol,
                price=cur_price,
                reason=f"Blocked by Hurst Gate (H={hurst_val:.3f} >= {hurst_mr_max:.2f})",
            )
        else:
            mr_sig = mean_reversion_strategy.evaluate(symbol, df, existing_position=None)

        extra_signals: List[TradingSignal] = []
        for custom_id, custom_strat in self._custom_strategies.items():
            if custom_id in (
                VolatilitySqueezeStrategy.STRATEGY_ID,
                TrendPullbackStrategy.STRATEGY_ID,
                VwapReversionStrategy.STRATEGY_ID,
                Nr7BreakoutStrategy.STRATEGY_ID,
                RangeEnvelopeStrategy.STRATEGY_ID,
                SupertrendCtaStrategy.STRATEGY_ID,
                DonchianRangeFadeStrategy.STRATEGY_ID,
            ):
                if dyn_route not in ("all", custom_id):
                    continue
                if playbook_allowed is not None and custom_id not in playbook_allowed:
                    continue
            if self.is_strategy_enabled(custom_id) and hasattr(custom_strat, "evaluate"):
                csig = custom_strat.evaluate(symbol, df, existing_position=None)
                if csig is not None:
                    extra_signals.append(csig)

        # Merge indicator snapshot from all strategies for full transparency
        merged_ind = {}
        if mom_sig and mom_sig.indicators:
            merged_ind.update(mom_sig.indicators)
        if mr_sig and mr_sig.indicators:
            for k, v in mr_sig.indicators.items():
                if k not in merged_ind:
                    merged_ind[k] = v
        for csig in extra_signals:
            if csig.indicators:
                for k, v in csig.indicators.items():
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
        if hurst_val is not None:
            merged_ind["hurst"] = hurst_val

        chosen_sig, reason = self.reconcile_signals(
            symbol, mom_sig, mr_sig, extra_signals=extra_signals, context=merged_ind
        )

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
        # Smart-Money 13F Whale & Congressional Pelosi PTR Conviction Radar (trader-copycat + trader-pelosi)
        sm_verdict = self.smart_money_radar.evaluate_symbol(symbol)
        conf_score = max(0.0, min(1.0, conf_score + sm_verdict.confluence_delta))
        merged_ind["smart_money_flow"] = sm_verdict.summary
        merged_ind["confluence"] = conf_desc
        merged_ind["confluence_score"] = conf_score

        # Extract current bar UTC timestamp for time-sensitive institutional gates
        raw_bar_ts = df.index[-1] if isinstance(df.index[-1], pd.Timestamp) else pd.Timestamp.now(tz="UTC")
        bar_dt_utc = (
            raw_bar_ts.tz_localize("UTC").to_pydatetime()
            if raw_bar_ts.tzinfo is None
            else raw_bar_ts.tz_convert("UTC").to_pydatetime()
        )

        # Skill Gate 1: NYSE Market Clock & 9:30 AM Opening-Bell Gap Buffer (trader-alerts)
        if self.config.get("enable_nyse_session_gate", True) and not is_crypto_symbol(symbol):
            eq_session = self.nyse_clock.evaluate_equity_session(
                symbol=symbol,
                now_utc=bar_dt_utc,
                enforce_market_hours=bool(self.config.get("enforce_nyse_market_hours", False)),
            )
            merged_ind["nyse_session"] = eq_session.reason
            if not eq_session.allow_new_entry:
                return CoordinatorDecision(
                    symbol=symbol,
                    action="REJECTED",
                    strategy_id=chosen_sig.strategy_id,
                    price=cur_price,
                    reason=eq_session.reason,
                    signal=chosen_sig,
                    indicators=merged_ind,
                )

        # Skill Gate 2: SEC EDGAR 8-K Real-Time Filing & Earnings Shock Veto (trader-news)
        if self.config.get("enable_sec_8k_gate", True) and not is_crypto_symbol(symbol):
            sec_8k = self.sec_8k_monitor.evaluate_symbol(symbol, now_utc=bar_dt_utc)
            merged_ind["sec_8k_status"] = sec_8k.reason
            if not sec_8k.approved:
                return CoordinatorDecision(
                    symbol=symbol,
                    action="REJECTED",
                    strategy_id=chosen_sig.strategy_id,
                    price=cur_price,
                    reason=sec_8k.reason,
                    signal=chosen_sig,
                    indicators=merged_ind,
                )

        # Skill Gate 3: SEC XBRL 10-Q/10-K Fundamental Quality Gate (trader-analyst + trader-screener)
        if self.config.get("enable_sec_fundamental_gate", True) and not is_crypto_symbol(symbol):
            fund_verdict = self.sec_fundamental_gate.evaluate_equity(symbol)
            merged_ind["sec_xbrl_quality"] = fund_verdict.quality_score
            if not fund_verdict.passed:
                return CoordinatorDecision(
                    symbol=symbol,
                    action="REJECTED",
                    strategy_id=chosen_sig.strategy_id,
                    price=cur_price,
                    reason=fund_verdict.reason,
                    signal=chosen_sig,
                    indicators=merged_ind,
                )

        # Skill Gate 5A: Per-Ticker Repeat-Loss & 30-Day Wash-Sale Guard (trader-tax)
        if self.config.get("enable_wash_and_streak_guard", True):
            closed_list = getattr(self.account, "closed_trades", []) or self.account.state.get("closed_trades", [])
            wash_verdict = self.wash_and_streak_guard.check_entry_allowed(
                symbol=symbol,
                closed_trades=closed_list,
                now_utc=bar_dt_utc,
                enable_wash_sale=bool(self.config.get("enable_wash_sale_guard", True)),
            )
            if not wash_verdict.allowed:
                return CoordinatorDecision(
                    symbol=symbol,
                    action="REJECTED",
                    strategy_id=chosen_sig.strategy_id,
                    price=cur_price,
                    reason=wash_verdict.reason,
                    signal=chosen_sig,
                    indicators=merged_ind,
                )

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
        if chosen_sig.strategy_id == "mean_reversion":
            base_atr_mult = sym_cfg.get("mr_atr_stop_mult", self.config.get("mr_atr_stop_mult", 2.0))
            effective_atr_mult = base_atr_mult
            effective_suggested_stop = chosen_sig.suggested_stop
        else:
            base_atr_mult = sym_cfg.get("atr_multiplier", self.config.get("atr_multiplier", 2.25))
            effective_atr_mult = base_atr_mult * (0.85 if (has_confluence and conf_score >= 0.90) else 1.0)
            if has_confluence and conf_score >= 0.90 and chosen_sig.atr > 0:
                if chosen_sig.direction == SignalDirection.SHORT:
                    effective_suggested_stop = round(chosen_sig.reference_price + (chosen_sig.atr * effective_atr_mult), 4)
                else:
                    effective_suggested_stop = round(chosen_sig.reference_price - (chosen_sig.atr * effective_atr_mult), 4)
            else:
                effective_suggested_stop = chosen_sig.suggested_stop

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

        # Confluence-Weighted Position Sizing (strictly within 1%-5% hard bounds)
        conf_risk_mult = 1.0
        if self.config.get("enable_confluence_sizing", True):
            if conf_score >= 0.85:
                conf_risk_mult = 1.75  # Boost from 2.0% -> 3.5% on A+ setups
            elif conf_score >= 0.75:
                conf_risk_mult = 1.25  # Boost from 2.0% -> 2.5%
            elif conf_score < 0.70:
                conf_risk_mult = 0.60  # Scale down to 1.2% on marginal setups

        combined_risk_mult = vol_risk_mult * macro_risk_mult * sm_verdict.risk_multiplier * conf_risk_mult

        # Item 2: Dynamic Kill-Switch Half-Step Recovery Protocol (72h Probationary Reentry)
        if self.risk_engine.is_halted and hasattr(self.risk_engine, "evaluate_probationary_reentry"):
            ema200_val = merged_ind.get("macro_ema") or merged_ind.get("ema_filter")
            ema200_reclaimed = bool(ema200_val is not None and not pd.isna(ema200_val) and cur_price >= float(ema200_val))
            vix_stable = bool(fred_report.vix_level < 20.0 or fred_report.regime == "STABLE")
            strat_pf = float(sym_cfg.get("historical_profit_factor", 1.25))
            self.risk_engine.evaluate_probationary_reentry(
                current_equity=total_equity,
                vix_stable=vix_stable,
                ema_200_reclaimed=ema200_reclaimed,
                strategy_profit_factor=strat_pf,
                current_time=last_ts,
            )

        corr_map: Dict[str, float] = {}
        if self.config.get("enable_correlation_guard", True) and self.account.positions and df is not None and len(df) >= 24:
            cand_rets = df["close"].astype(float).pct_change().dropna().iloc[-168:]
            for active_sym in self.account.positions.keys():
                adf = self._latest_market_data.get(active_sym)
                if adf is not None and len(adf) >= 24:
                    act_rets = adf["close"].astype(float).pct_change().dropna().iloc[-168:]
                    aligned = pd.concat([cand_rets, act_rets], axis=1, join="inner").dropna()
                    if len(aligned) >= 20:
                        corr_val = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
                        if not math.isnan(corr_val):
                            corr_map[active_sym] = corr_val

        risk_verdict = self.risk_engine.validate_proposed_entry(
            symbol=symbol,
            side=chosen_sig.direction.value,
            strategy_id=chosen_sig.strategy_id,
            entry_price=chosen_sig.reference_price,
            suggested_stop=effective_suggested_stop,
            atr=chosen_sig.atr,
            atr_multiplier=effective_atr_mult,
            equity=total_equity,
            cash=self.account.cash,
            active_positions=self.account.positions,
            fee_pct=chosen_sig.estimated_fee_pct,
            requested_risk_pct=effective_risk_pct,
            current_time=last_ts,
            risk_multiplier=combined_risk_mult,
            correlation_map=corr_map,
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
        if "quote_time" in df.columns and len(df) > 0:
            raw_qt = df["quote_time"].iloc[-1]
            quote_time = raw_qt.to_pydatetime() if hasattr(raw_qt, "to_pydatetime") else raw_qt
        elif hasattr(df, "attrs") and df.attrs.get("quote_time") is not None:
            raw_qt = df.attrs["quote_time"]
            quote_time = raw_qt.to_pydatetime() if hasattr(raw_qt, "to_pydatetime") else raw_qt
        elif self.config.get("enforce_bar_staleness", False) and last_ts is not None:
            quote_time = last_ts.to_pydatetime() if hasattr(last_ts, "to_pydatetime") else last_ts
        else:
            quote_time = getattr(chosen_sig, "timestamp", last_ts)

        last_row = df.iloc[-1]
        est_half_spread = float(
            sym_cfg.get("estimated_half_spread_pct", sym_cfg.get("slippage_pct", 0.0003))
        )
        bid_px = (
            float(last_row["bid"])
            if "bid" in df.columns
            else float(df.attrs.get("bid", cur_price * (1.0 - est_half_spread)))
        )
        ask_px = (
            float(last_row["ask"])
            if "ask" in df.columns
            else float(df.attrs.get("ask", cur_price * (1.0 + est_half_spread)))
        )
        order_notional = risk_verdict.suggested_units * cur_price

        preflight = self.preflight_checker.check(
            symbol=symbol,
            side="BUY" if chosen_sig.direction == SignalDirection.LONG else "SELL",
            price=cur_price,
            bid=bid_px,
            ask=ask_px,
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

        # 4. Smart Order Routing & Item 3: Maker Queue Tracking & Adaptive Re-Quoting Engine
        exec_mode = sym_cfg.get("execution_mode", self.config.get("execution_mode", "maker"))
        if exec_mode == "maker":
            maker_fee_cfg = float(sym_cfg.get("maker_fee_pct", self.config.get("maker_fee_pct", 0.0002)))
            if hasattr(self.account, "evaluate_maker_fill_or_requote") and self.config.get("enable_adaptive_requoting", True):
                bar_open = float(last_row.get("open", cur_price))
                bar_high = float(last_row.get("high", cur_price))
                bar_low = float(last_row.get("low", cur_price))
                bar_vol = float(last_row.get("volume", 1.0)) if "volume" in last_row else 1.0
                avg_vol = float(df["volume"].iloc[-20:].mean()) if ("volume" in df.columns and len(df) >= 20) else bar_vol
                mq_res = self.account.evaluate_maker_fill_or_requote(
                    side=chosen_sig.direction.value,
                    quote_price=cur_price,
                    bar_open=bar_open,
                    bar_high=bar_high,
                    bar_low=bar_low,
                    bar_close=cur_price,
                    bar_volume=bar_vol,
                    avg_volume=avg_vol,
                    maker_fee_pct=maker_fee_cfg,
                    taker_fee_pct=float(chosen_sig.estimated_fee_pct),
                )
                merged_ind["maker_queue_mode"] = mq_res["execution_mode"]
                merged_ind["maker_filled"] = bool(mq_res["filled"])
                merged_ind["maker_fee_bps"] = round(float(mq_res["effective_fee_pct"]) * 10000.0, 4)
                merged_ind["maker_slippage_bps"] = round(
                    (abs(float(mq_res["exec_price"]) - cur_price) / cur_price * 10000.0) if cur_price > 0 else 0.0,
                    4,
                )
                log_maker_telemetry(
                    symbol=symbol,
                    side=chosen_sig.direction.value,
                    execution_mode=str(mq_res["execution_mode"]),
                    filled=bool(mq_res["filled"]),
                    quote_price=cur_price,
                    exec_price=float(mq_res["exec_price"]),
                    effective_fee_pct=float(mq_res["effective_fee_pct"]),
                )
                if not mq_res["filled"]:
                    return CoordinatorDecision(
                        symbol=symbol,
                        action="REJECTED",
                        strategy_id=chosen_sig.strategy_id,
                        price=cur_price,
                        reason=f"Blocked by Adaptive Maker Queue: {mq_res['execution_mode']} (unconfirmed low-depth spike)",
                        signal=chosen_sig,
                        risk_verdict=risk_verdict,
                        indicators=merged_ind,
                    )
                applied_fee_pct = float(mq_res["effective_fee_pct"])
                exec_price = float(mq_res["exec_price"])
            else:
                applied_fee_pct = maker_fee_cfg
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

        # Persist the take-profit level so the exit logic can exit / scale out later.
        if chosen_sig.suggested_take_profit > 0:
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
        now: Optional[datetime] = None,
        benchmark_data: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> CycleReport:
        """Run a coordinated portfolio evaluation cycle across all symbols."""
        now = now or datetime.now(timezone.utc)
        self._latest_market_data = market_data
        self._latest_benchmark_data = benchmark_data or {}
        macro_regime: Optional[str] = None
        macro_ret_pct: Optional[float] = None
        if self.config.get("enable_macro_regime_playbook", False):
            macro_regime, macro_ret_pct = self.get_macro_playbook_regime(
                market_data=market_data,
                force_refresh=True,
                benchmark_data=benchmark_data,
            )
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

            # Persist actionable decisions and active defense-gate blocks into audit journal
            should_journal = (
                self.config.get("journal_log_all_scans", False)
                or decision.action in ("ENTER_LONG", "ENTER_SHORT", "EXIT", "PARTIAL_EXIT", "CONFLICT")
                or (
                    decision.action == "REJECTED"
                    and any(
                        kw in decision.reason
                        for kw in ("Blocked by", "Suppressed", "Risk check", "Cooldown", "Veto", "Guard")
                    )
                )
            )
            if should_journal:
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

        # Dynamic Pairs Trading Multi-Asset Evaluation Pass
        if self.is_strategy_enabled(StatArbPairsStrategy.STRATEGY_ID):
            active_pairs: Dict[str, Any] = {}
            for sym, pos in self.account.positions.items():
                pid = pos.get("pair_id")
                if pid and ":" in str(pid):
                    leg_a, leg_b = str(pid).split(":", 1)
                    pos_a = self.account.positions.get(leg_a, {})
                    pos_b = self.account.positions.get(leg_b, {})
                    active_pairs[pid] = {
                        "side_a": pos_a.get("side", pos.get("side", "LONG")),
                        "entry_a": pos_a.get("entry_price", 0.0),
                        "entry_b": pos_b.get("entry_price", 0.0),
                        "units_a": pos_a.get("units", 0.0),
                        "units_b": pos_b.get("units", 0.0),
                        "hedge_ratio": pos.get("hedge_ratio", 1.0),
                        "entry_spread_mean": pos.get("entry_spread_mean"),
                        "entry_spread_std": pos.get("entry_spread_std"),
                        "entry_time": pos_a.get("entry_time") or pos.get("entry_time"),
                        "fees_paid": float(pos_a.get("fees_paid", 0.0) or 0.0) + float(pos_b.get("fees_paid", 0.0) or 0.0),
                    }

            raw_candidate_pairs = self.config.get("pairs_universe") or self.config.get("stat_arb_pairs")
            candidate_pairs = None
            if raw_candidate_pairs:
                cfg_pair_syms = {s for p in raw_candidate_pairs if len(p) == 2 for s in p}
                valid_cfg_pairs = [
                    tuple(p) for p in raw_candidate_pairs
                    if len(p) == 2 and p[0] in market_data and p[1] in market_data
                ]
                for pid in active_pairs:
                    parts = str(pid).split(":")
                    if len(parts) == 2 and parts[0] in market_data and parts[1] in market_data:
                        pt = (parts[0], parts[1])
                        if pt not in valid_cfg_pairs:
                            valid_cfg_pairs.append(pt)
                if valid_cfg_pairs:
                    candidate_pairs = valid_cfg_pairs
                elif any(s in cfg_pair_syms for s in market_data):
                    candidate_pairs = []

            pair_signals = self.pairs_strategy.scan_dynamic_pairs(
                market_data=market_data,
                candidate_universe=candidate_pairs,
                active_pair_positions=active_pairs,
            )

            for psig in pair_signals:
                if psig.action == "CLOSE_PAIR":
                    pa = current_prices.get(psig.asset_a, float(market_data[psig.asset_a]["close"].iloc[-1]))
                    pb = current_prices.get(psig.asset_b, float(market_data[psig.asset_b]["close"].iloc[-1]))
                    pair_fee_pct = float(self.config.get("fee_pct", 0.0006))
                    if self.account.has_position(psig.asset_a):
                        leg_side = self.account.get_position(psig.asset_a).get("side", "LONG")
                        self.account.close_position(
                            psig.asset_a,
                            self._pair_fill_price(pa, leg_side, opening=False),
                            fee_pct=pair_fee_pct,
                            exit_reason=f"Dynamic Pair Exit ({psig.reason})",
                        )
                        executed += 1
                    if self.account.has_position(psig.asset_b):
                        leg_side = self.account.get_position(psig.asset_b).get("side", "LONG")
                        self.account.close_position(
                            psig.asset_b,
                            self._pair_fill_price(pb, leg_side, opening=False),
                            fee_pct=pair_fee_pct,
                            exit_reason=f"Dynamic Pair Exit ({psig.reason})",
                        )
                        executed += 1
                    if "Stop loss triggered" in psig.reason:
                        self._record_pair_stop(psig.pair_id, now)
                    decisions.append(CoordinatorDecision(
                        symbol=psig.pair_id,
                        action="CLOSE_PAIR",
                        strategy_id="stat_arb_pairs",
                        price=pa,
                        reason=psig.reason,
                        indicators={"z_score": psig.z_score, "beta": psig.hedge_ratio, "half_life": psig.half_life, "correlation": psig.correlation},
                    ))
                elif psig.action in ("LONG_A_SHORT_B", "SHORT_A_LONG_B"):
                    if self.config.get("enable_macro_regime_playbook", False):
                        cur_macro = macro_regime or self.get_macro_playbook_regime(market_data=market_data)[0]
                        cur_macro_ret = macro_ret_pct if macro_ret_pct is not None else self.last_macro_return_pct
                        if StatArbPairsStrategy.STRATEGY_ID not in self.get_playbook_allowed_strategies(cur_macro):
                            decisions.append(CoordinatorDecision(
                                symbol=psig.pair_id,
                                action="REJECTED",
                                strategy_id="stat_arb_pairs",
                                price=current_prices.get(psig.asset_a, 0.0),
                                reason=(
                                    f"Paused by Regime Playbook B "
                                    f"(Macro={cur_macro}, 20d={cur_macro_ret:+.2f}%)"
                                ),
                            ))
                            continue
                    cd_until = getattr(self, "pair_cooldown_until", {}).get(psig.pair_id)
                    if cd_until is not None and now < cd_until:
                        decisions.append(CoordinatorDecision(
                            symbol=psig.pair_id,
                            action="REJECTED",
                            strategy_id="stat_arb_pairs",
                            price=current_prices.get(psig.asset_a, 0.0),
                            reason=f"Pair {psig.pair_id} in post-stop cooldown until {cd_until.isoformat()}",
                        ))
                        continue
                    total_eq = self.account.get_total_equity(current_prices)
                    breaker_ok, breaker_reason = self.risk_engine.check_circuit_breakers(total_eq, current_time=now)
                    if not breaker_ok:
                        decisions.append(CoordinatorDecision(
                            symbol=psig.pair_id,
                            action="REJECTED",
                            strategy_id="stat_arb_pairs",
                            price=current_prices.get(psig.asset_a, 0.0),
                            reason=breaker_reason,
                        ))
                        continue
                    if not self.account.has_position(psig.asset_a) and not self.account.has_position(psig.asset_b):
                        if len(self.account.positions) + 2 <= self.risk_engine.max_open_positions:
                            pa = current_prices.get(psig.asset_a, float(market_data[psig.asset_a]["close"].iloc[-1]))
                            pb = current_prices.get(psig.asset_b, float(market_data[psig.asset_b]["close"].iloc[-1]))
                            side_a = "LONG" if psig.action == "LONG_A_SHORT_B" else "SHORT"
                            side_b = "SHORT" if psig.action == "LONG_A_SHORT_B" else "LONG"

                            raw_risk = float(self.config.get("stat_arb_risk_pct", 0.02))
                            clamped_risk = self.risk_engine.enforce_risk_bounds(raw_risk)
                            dollar_risk = total_eq * (clamped_risk * 0.5)

                            stop_pct, base_leg_notional, total_cost = self._compute_pair_leg_sizing(
                                market_data.get(psig.asset_a),
                                market_data.get(psig.asset_b),
                                total_eq,
                                dollar_risk,
                            )
                            stop_a = round(pa * (1.0 - stop_pct) if side_a == "LONG" else pa * (1.0 + stop_pct), 6)
                            stop_b = round(pb * (1.0 - stop_pct) if side_b == "LONG" else pb * (1.0 + stop_pct), 6)
                            beta_val = max(0.01, min(10.0, float(psig.hedge_ratio))) if psig.hedge_ratio and psig.hedge_ratio > 0 else 1.0
                            if pa > 0 and pb > 0 and total_cost > 0:
                                units_a = total_cost / (pa + beta_val * pb)
                                units_b = beta_val * units_a
                            else:
                                units_a = 0.0
                                units_b = 0.0

                            # Compute entry spread mean and std to lock the basket
                            comb_ab = pd.concat(
                                [market_data[psig.asset_a]["close"], market_data[psig.asset_b]["close"]],
                                axis=1,
                                join="inner",
                            ).dropna()
                            lb = self.pairs_strategy.lookback_period
                            sp_win = comb_ab.iloc[-lb:, 0] - beta_val * comb_ab.iloc[-lb:, 1]
                            entry_sp_mean = float(sp_win.mean()) if len(sp_win) > 0 else 0.0
                            entry_sp_std = float(sp_win.std()) if len(sp_win) > 1 else 1.0

                            pair_fee_pct = float(self.config.get("fee_pct", 0.0006))
                            if total_cost > 0 and units_a > 0 and units_b > 0 and self.account.cash >= (total_cost * (1.0 + 2.0 * pair_fee_pct)):
                                try:
                                    self.account.open_position(
                                        symbol=psig.asset_a,
                                        entry_price=self._pair_fill_price(pa, side_a, opening=True),
                                        units=units_a,
                                        stop_loss=stop_a,
                                        dollar_risk=dollar_risk,
                                        fee_pct=pair_fee_pct,
                                        max_concurrent_positions=self.risk_engine.max_open_positions,
                                        side=side_a,
                                        strategy_id="stat_arb_pairs",
                                    )
                                    self.account.set_position_fields(
                                        psig.asset_a,
                                        pair_id=psig.pair_id,
                                        hedge_ratio=beta_val,
                                        entry_spread_mean=entry_sp_mean,
                                        entry_spread_std=entry_sp_std,
                                    )

                                    self.account.open_position(
                                        symbol=psig.asset_b,
                                        entry_price=self._pair_fill_price(pb, side_b, opening=True),
                                        units=units_b,
                                        stop_loss=stop_b,
                                        dollar_risk=dollar_risk,
                                        fee_pct=pair_fee_pct,
                                        max_concurrent_positions=self.risk_engine.max_open_positions,
                                        side=side_b,
                                        strategy_id="stat_arb_pairs",
                                    )
                                    self.account.set_position_fields(
                                        psig.asset_b,
                                        pair_id=psig.pair_id,
                                        hedge_ratio=beta_val,
                                        entry_spread_mean=entry_sp_mean,
                                        entry_spread_std=entry_sp_std,
                                    )

                                    executed += 2
                                    signals_count += 1
                                    decisions.append(CoordinatorDecision(
                                        symbol=psig.pair_id,
                                        action=psig.action,
                                        strategy_id="stat_arb_pairs",
                                        price=pa,
                                        reason=psig.reason,
                                        indicators={"z_score": psig.z_score, "beta": psig.hedge_ratio, "half_life": psig.half_life, "correlation": psig.correlation},
                                    ))
                                except Exception:
                                    pass
                else:
                    decisions.append(CoordinatorDecision(
                        symbol=psig.pair_id,
                        action=psig.action,
                        strategy_id="stat_arb_pairs",
                        price=current_prices.get(psig.asset_a, 0.0),
                        reason=psig.reason,
                        indicators={
                            "z_score": psig.z_score,
                            "beta": psig.hedge_ratio,
                            "half_life": psig.half_life,
                            "correlation": psig.correlation,
                            "is_stationary": psig.is_stationary,
                        },
                    ))

        # Delta-Neutral Basis Funding Rate Yield Accrual Pass
        if self.is_strategy_enabled("basis_yield"):
            elapsed_hours = 8.0
            if self.last_basis_accrual_time is not None:
                elapsed_hours = (now - self.last_basis_accrual_time).total_seconds() / 3600.0

            bl_set = set(self.config.get("blacklisted_symbols", []))
            crypto_syms = [s for s in market_data.keys() if is_crypto_symbol(s) and s not in bl_set]
            best_opp: Optional[BasisOpportunity] = None
            for csym in crypto_syms:
                opp = self.basis_yield_engine.evaluate_opportunity(csym)
                if best_opp is None or opp.annualized_apr > best_opp.annualized_apr:
                    best_opp = opp

            total_eq_now = self.account.get_total_equity(current_prices)
            breaker_ok, _ = self.risk_engine.check_circuit_breakers(total_eq_now, current_time=now)
            if elapsed_hours >= 8.0 and breaker_ok:
                total_eq = self.account.get_total_equity(current_prices)
                max_basis_pct = float(self.config.get("max_basis_allocation_pct", 0.30))
                if self.config.get("enable_idle_cash_sweep", True) and total_eq > 0 and self.account.cash > total_eq * 0.40:
                    sweep_cap = float(self.config.get("idle_cash_sweep_max_pct", 0.50))
                    basis_alloc = min(self.account.cash * 0.75, total_eq * max(max_basis_pct, sweep_cap))
                else:
                    basis_alloc = min(self.account.cash * 0.80, total_eq * max_basis_pct)
                if basis_alloc >= 50.0 and best_opp is not None and best_opp.is_profitable:
                    receipt = self.basis_yield_engine.accrue_funding_yield(
                        account=self.account,
                        symbol=best_opp.symbol,
                        notional_capital=basis_alloc,
                        funding_rate_8h=best_opp.funding_rate_8h,
                        hours_elapsed=elapsed_hours,
                    )
                    self.last_basis_accrual_time = now
                    if hasattr(self.account, "state") and isinstance(self.account.state, dict):
                        self.account.state["last_basis_accrual_time"] = now.isoformat()
                        if hasattr(self.account, "_save"):
                            self.account._save()
                    if receipt.get("status") == "ACCRUED":
                        decisions.append(CoordinatorDecision(
                            symbol=best_opp.symbol,
                            action="ACCRUED_YIELD",
                            strategy_id="basis_yield",
                            price=best_opp.annualized_apr,
                            reason=f"Accrued ${receipt['accrued_payout']:.2f} cash yield ({best_opp.annualized_apr:.1f}% APR)",
                            indicators={
                                "funding_rate": best_opp.funding_rate_8h,
                                "apr": best_opp.annualized_apr,
                                "payout": receipt["accrued_payout"],
                            },
                        ))
            elif best_opp is not None:
                decisions.append(CoordinatorDecision(
                    symbol=best_opp.symbol,
                    action="YIELD_MONITOR",
                    strategy_id="basis_yield",
                    price=best_opp.annualized_apr,
                    reason=f"Top carry candidate {best_opp.symbol} ({best_opp.annualized_apr:.1f}% APR | Hurdle: {self.basis_yield_engine.hurdle_apr:.1f}% | Next window in {max(0.0, 8.0 - elapsed_hours):.1f}h)",
                    indicators={
                        "funding_rate": best_opp.funding_rate_8h,
                        "apr": best_opp.annualized_apr,
                        "hours_elapsed": elapsed_hours,
                        "hours_remaining": max(0.0, 8.0 - elapsed_hours),
                        "hurdle_apr": self.basis_yield_engine.hurdle_apr,
                        "hurdle_met": best_opp.is_profitable,
                    },
                ))

        # Skill Gate 5B: Portfolio Drift-Band Rebalancer Pass (trader-rebalance)
        if self.config.get("enable_drift_rebalancer", True) and self.account.positions:
            rebal_actions = self.drift_rebalancer.rebalance_overweight_positions(
                account=self.account,
                current_prices=current_prices,
            )
            for r_act in rebal_actions:
                executed += 1
                decisions.append(CoordinatorDecision(
                    symbol=r_act["symbol"],
                    action="REBALANCE_TRIM",
                    strategy_id="drift_rebalancer",
                    units=r_act["trimmed_units"],
                    price=r_act["exit_price"],
                    reason=(
                        f"Trimmed overweight position ({r_act['old_weight_pct']:.1f}% -> "
                        f"{r_act['new_weight_pct']:.1f}% equity, realized ${r_act['realized_pnl']:+.2f})"
                    ),
                ))

        total_equity = self.account.get_total_equity(current_prices)
        if self.risk_engine is not None:
            self.risk_engine.check_circuit_breakers(total_equity, current_time=now)

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
            macro_regime=macro_regime,
            macro_return_pct=macro_ret_pct,
        )
