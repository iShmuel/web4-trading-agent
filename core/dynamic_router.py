"""30-Day Dynamic Rolling Expectancy Router (extracted from core/coordinator.py).

Evaluates all enabled & regime-permitted directional strategies over a trailing
historical window using realistic fees and slippage, selecting the highest-Sharpe
strategy with PF >= dynamic_router_min_pf and trades >= dynamic_router_min_trades
(or parking crypto in `basis_yield` and equities in `cash`).
"""

import logging
from typing import Any, Callable, Dict, Optional, Set

import pandas as pd

logger = logging.getLogger(__name__)

from core.data_loader import is_crypto_symbol
from strategies.donchian_range_fade import DonchianRangeFadeStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumStrategy
from strategies.nr7_breakout import Nr7BreakoutStrategy
from strategies.range_envelope import RangeEnvelopeStrategy
from strategies.supertrend_cta import SupertrendCtaStrategy
from strategies.trend_pullback import TrendPullbackStrategy
from strategies.volatility_squeeze import VolatilitySqueezeStrategy
from strategies.vwap_reversion import VwapReversionStrategy
from strategies.williams_r_reversal import WilliamsRReversalStrategy
from strategies.bear_chandelier_short import BearChandelierShortStrategy


def select_rolling_strategy(
    symbol: str,
    df: pd.DataFrame,
    sym_cfg: Dict[str, Any],
    config: Dict[str, Any],
    is_strategy_enabled_fn: Callable[[str], bool],
    allowed_playbook: Optional[Set[str]] = None,
) -> str:
    """Select the highest-Sharpe qualifying directional strategy over the rolling window."""
    is_crypto = is_crypto_symbol(symbol)
    min_bars = 120 if not is_crypto else 250
    if df is None or len(df) < min_bars:
        return "all"

    cfg_lb = int(config.get("dynamic_router_lookback_hours", 2160))
    lookback_bars = min(cfg_lb, len(df))
    df_win = df.iloc[-lookback_bars:].copy()

    def _dir_allowed(sid: str) -> bool:
        if not is_strategy_enabled_fn(sid):
            return False
        if allowed_playbook is not None and sid not in allowed_playbook:
            return False
        return True

    try:
        from core.backtest_engine import (
            run_backtest,
            run_basis_yield_backtest,
            run_donchian_range_fade_backtest,
            run_mean_reversion_backtest,
            run_nr7_breakout_backtest,
            run_range_envelope_backtest,
            run_supertrend_cta_backtest,
            run_trend_pullback_backtest,
            run_volatility_squeeze_backtest,
            run_vwap_reversion_backtest,
            run_williams_r_reversal_backtest,
            run_bear_chandelier_short_backtest,
        )

        router_fee = float(config.get("fee_pct", 0.0006))
        router_slip = float(config.get("slippage_pct", 0.0004))
        r_by = (
            run_basis_yield_backtest(df_win, hurdle_apr=5.0, fee_pct=router_fee, slippage_pct=router_slip)
            if is_crypto
            else None
        )

        min_pf = float(config.get("dynamic_router_min_pf", 1.15))
        min_tr = int(config.get("dynamic_router_min_trades", 2))
        cands = []

        if _dir_allowed(MomentumStrategy.STRATEGY_ID):
            r_mom = run_backtest(
                df_win,
                breakout_lookback=int(sym_cfg.get("breakout_lookback", 36)),
                ema_trend_filter=int(sym_cfg.get("ema_trend_filter", 200)),
                atr_multiplier=float(sym_cfg.get("atr_multiplier", 2.5)),
                take_profit_atr_mult=float(sym_cfg.get("take_profit_atr_mult", 2.0)),
                allow_long=bool(sym_cfg.get("allow_long", True)),
                allow_short=bool(sym_cfg.get("allow_short", False)),
                fee_pct=router_fee,
                slippage_pct=router_slip,
            )
            if r_mom.total_trades >= min_tr and r_mom.profit_factor >= min_pf:
                cands.append((r_mom.sharpe_ratio, MomentumStrategy.STRATEGY_ID))

        is_altcoin = is_crypto and symbol.upper() not in ("BTC/USDT", "ETH/USDT")
        suppress_mr = is_altcoin and bool(config.get("crypto_altcoin_momentum_only", False))

        if _dir_allowed(MeanReversionStrategy.STRATEGY_ID) and not suppress_mr:
            r_mr = run_mean_reversion_backtest(
                df_win,
                bb_period=int(sym_cfg.get("bb_period", 20)),
                bb_std=float(sym_cfg.get("bb_std", 2.1)),
                rsi_oversold=float(sym_cfg.get("rsi_oversold", 28.0)),
                rsi_overbought=float(sym_cfg.get("rsi_overbought", 72.0)),
                adx_threshold=float(sym_cfg.get("adx_threshold", 24.0)),
                allow_short=bool(sym_cfg.get("mr_allow_short", False)),
                fee_pct=router_fee,
                slippage_pct=router_slip,
            )
            if r_mr.total_trades >= min_tr and r_mr.profit_factor >= min_pf:
                cands.append((r_mr.sharpe_ratio, MeanReversionStrategy.STRATEGY_ID))

        if _dir_allowed(VolatilitySqueezeStrategy.STRATEGY_ID):
            r_sq = run_volatility_squeeze_backtest(
                df_win,
                account_risk_pct=float(sym_cfg.get("account_risk_pct", 0.02)),
                stop_atr_mult=float(sym_cfg.get("squeeze_atr_stop_mult", 2.0)),
                tp_atr_mult=float(sym_cfg.get("squeeze_tp_atr_mult", 2.5)),
                fee_pct=router_fee,
                slippage_pct=router_slip,
            )
            if r_sq.total_trades >= min_tr and r_sq.profit_factor >= min_pf:
                cands.append((r_sq.sharpe_ratio, VolatilitySqueezeStrategy.STRATEGY_ID))

        if _dir_allowed(TrendPullbackStrategy.STRATEGY_ID):
            r_pb = run_trend_pullback_backtest(
                df_win,
                account_risk_pct=float(sym_cfg.get("account_risk_pct", 0.02)),
                rsi_pullback_max=float(sym_cfg.get("pullback_rsi_max", 38.0)),
                stop_atr_mult=float(sym_cfg.get("pullback_atr_stop_mult", 2.0)),
                tp_atr_mult=float(sym_cfg.get("pullback_tp_atr_mult", 2.5)),
                fee_pct=router_fee,
                slippage_pct=router_slip,
            )
            if r_pb.total_trades >= min_tr and r_pb.profit_factor >= min_pf:
                cands.append((r_pb.sharpe_ratio, TrendPullbackStrategy.STRATEGY_ID))

        if _dir_allowed(VwapReversionStrategy.STRATEGY_ID):
            r_vw = run_vwap_reversion_backtest(
                df_win,
                account_risk_pct=float(sym_cfg.get("account_risk_pct", 0.02)),
                fee_pct=router_fee,
                slippage_pct=router_slip,
            )
            if r_vw.total_trades >= min_tr and r_vw.profit_factor >= min_pf:
                cands.append((r_vw.sharpe_ratio, VwapReversionStrategy.STRATEGY_ID))

        if _dir_allowed(Nr7BreakoutStrategy.STRATEGY_ID):
            r_nr7 = run_nr7_breakout_backtest(
                df_win,
                account_risk_pct=float(sym_cfg.get("account_risk_pct", 0.02)),
                nr7_lookback=int(sym_cfg.get("nr7_lookback", 7)),
                stop_atr_mult=float(sym_cfg.get("nr7_atr_stop_mult", 2.0)),
                fee_pct=router_fee,
                slippage_pct=router_slip,
            )
            if r_nr7.total_trades >= min_tr and r_nr7.profit_factor >= min_pf:
                cands.append((r_nr7.sharpe_ratio, Nr7BreakoutStrategy.STRATEGY_ID))

        if _dir_allowed(RangeEnvelopeStrategy.STRATEGY_ID):
            r_re = run_range_envelope_backtest(
                df_win,
                account_risk_pct=float(sym_cfg.get("account_risk_pct", 0.02)),
                range_window=int(sym_cfg.get("range_window", 36)),
                low_quantile=float(sym_cfg.get("range_low_quantile", 0.10)),
                high_quantile=float(sym_cfg.get("range_high_quantile", 0.90)),
                rsi_oversold=float(sym_cfg.get("range_rsi_oversold", 30.0)),
                rsi_overbought=float(sym_cfg.get("range_rsi_overbought", 70.0)),
                adx_max=float(sym_cfg.get("range_adx_max", 24.0)),
                stop_atr_mult=float(sym_cfg.get("range_atr_stop_mult", 2.0)),
                min_net_profit_usd=float(sym_cfg.get("range_min_net_profit_usd", 10.0)),
                allow_short=bool(sym_cfg.get("range_allow_short", True)),
                fee_pct=router_fee,
                slippage_pct=router_slip,
            )
            if r_re.total_trades >= min_tr and r_re.profit_factor >= min_pf:
                cands.append((r_re.sharpe_ratio, RangeEnvelopeStrategy.STRATEGY_ID))

        if _dir_allowed(SupertrendCtaStrategy.STRATEGY_ID):
            st_mult = (
                float(sym_cfg.get("supertrend_crypto_mult", sym_cfg.get("supertrend_mult", 3.0)))
                if is_crypto
                else float(sym_cfg.get("supertrend_equity_mult", 2.5))
            )
            r_st = run_supertrend_cta_backtest(
                df_win,
                account_risk_pct=float(sym_cfg.get("account_risk_pct", 0.02)),
                atr_period=int(sym_cfg.get("supertrend_atr_period", 10)),
                mult=st_mult,
                stop_atr_mult=float(sym_cfg.get("supertrend_stop_atr_mult", 3.0)),
                allow_long=bool(sym_cfg.get("supertrend_allow_long", True)),
                allow_short=bool(sym_cfg.get("supertrend_allow_short", True)),
                fee_pct=router_fee,
                slippage_pct=router_slip,
            )
            if r_st.total_trades >= min_tr and r_st.profit_factor >= min_pf:
                cands.append((r_st.sharpe_ratio, SupertrendCtaStrategy.STRATEGY_ID))

        if _dir_allowed(DonchianRangeFadeStrategy.STRATEGY_ID):
            r_df = run_donchian_range_fade_backtest(
                df_win,
                account_risk_pct=float(sym_cfg.get("account_risk_pct", 0.02)),
                window=int(sym_cfg.get("donchian_fade_window", 20)),
                rsi_period=int(sym_cfg.get("donchian_fade_rsi_period", 2)),
                rsi_low=float(sym_cfg.get("donchian_fade_rsi_low", 10.0)),
                rsi_high=float(sym_cfg.get("donchian_fade_rsi_high", 90.0)),
                width_lookback=int(sym_cfg.get("donchian_fade_width_lookback", 100)),
                width_quantile=float(sym_cfg.get("donchian_fade_width_quantile", 0.50)),
                stop_atr_mult=float(sym_cfg.get("donchian_fade_stop_atr_mult", 2.0)),
                tp_atr_mult=float(sym_cfg.get("donchian_fade_tp_atr_mult", 2.0)),
                allow_long=bool(sym_cfg.get("donchian_fade_allow_long", True)),
                allow_short=bool(sym_cfg.get("donchian_fade_allow_short", True)),
                fee_pct=router_fee,
                slippage_pct=router_slip,
            )
            if r_df.total_trades >= min_tr and r_df.profit_factor >= min_pf:
                cands.append((r_df.sharpe_ratio, DonchianRangeFadeStrategy.STRATEGY_ID))

        if _dir_allowed(WilliamsRReversalStrategy.STRATEGY_ID):
            r_wr = run_williams_r_reversal_backtest(
                df_win,
                account_risk_pct=float(sym_cfg.get("account_risk_pct", 0.02)),
                wr_period=int(sym_cfg.get("williams_r_period", 14)),
                adx_max=float(sym_cfg.get("williams_r_adx_max", 22.0)),
                oversold=float(sym_cfg.get("williams_r_oversold", -85.0)),
                oversold_confirm=float(sym_cfg.get("williams_r_oversold_confirm", -80.0)),
                overbought=float(sym_cfg.get("williams_r_overbought", -15.0)),
                overbought_confirm=float(sym_cfg.get("williams_r_overbought_confirm", -20.0)),
                stop_atr_mult=float(sym_cfg.get("williams_r_stop_atr_mult", 2.0)),
                tp_atr_mult=float(sym_cfg.get("williams_r_tp_atr_mult", 2.0)),
                allow_long=bool(sym_cfg.get("williams_r_allow_long", True)),
                allow_short=bool(sym_cfg.get("williams_r_allow_short", True)),
                fee_pct=router_fee,
                slippage_pct=router_slip,
            )
            if r_wr.total_trades >= min_tr and r_wr.profit_factor >= min_pf:
                cands.append((r_wr.sharpe_ratio, WilliamsRReversalStrategy.STRATEGY_ID))

        if _dir_allowed(BearChandelierShortStrategy.STRATEGY_ID):
            r_bc = run_bear_chandelier_short_backtest(
                df_win,
                account_risk_pct=float(sym_cfg.get("account_risk_pct", 0.02)),
                ema_period=int(sym_cfg.get("bear_chand_ema_period", 200)),
                z_window=int(sym_cfg.get("bear_chand_z_window", 24)),
                z_entry=float(sym_cfg.get("bear_chand_z_entry", 1.5)),
                z_exit=float(sym_cfg.get("bear_chand_z_exit", -0.5)),
                chandelier_window=int(sym_cfg.get("bear_chand_window", 20)),
                chandelier_mult=float(sym_cfg.get("bear_chand_mult", 3.0)),
                stop_atr_mult=float(sym_cfg.get("bear_chand_stop_atr_mult", 2.2)),
                tp_atr_mult=float(sym_cfg.get("bear_chand_tp_atr_mult", 2.5)),
                fee_pct=router_fee,
                slippage_pct=router_slip,
            )
            if r_bc.total_trades >= min_tr and r_bc.profit_factor >= min_pf:
                cands.append((r_bc.sharpe_ratio, BearChandelierShortStrategy.STRATEGY_ID))

        if cands:
            cands.sort(key=lambda x: x[0], reverse=True)
            return cands[0][1]
        if r_by is not None and r_by.total_trades >= 1 and r_by.profit_factor >= 1.0:
            return "basis_yield"
        return "basis_yield" if is_crypto else "cash"
    except Exception as exc:
        logger.warning("[DynamicRouter] Strategy routing failed for %s: %s (falling back to 'all')", symbol, exc)
        return "all"
