import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import research.run_shifted_oos_validation as oos
from reports.targeted_profitability_optimization.run_optimization_research import apply_overrides, evaluate_scenario

base_cfg = json.loads(open('config/active_config.json').read())
covr = {
    'adx_chop_threshold': 24.0,
    'adx_trend_threshold': 28.0,
    'bb_std': 1.9,
    'stat_arb_entry_z': 2.5,
    'stat_arb_min_edge_cost_ratio': 2.5,
    'pairs_universe': [
        ['GLD', 'SLV'],
        ['NVDA', 'AMD'],
        ['NVDA', 'SPY'],
        ['BTC/USDT', 'ETH/USDT'],
        ['SOL/USDT', 'AVAX/USDT'],
        ['XLE', 'TLT']
    ]
}
cfg = apply_overrides(base_cfg, covr)
frames = oos.load_180d_candidate_frames(base_cfg)
win1, win2, _, _, _ = oos.split_selection_and_oos_windows(frames, base_cfg)
universe, _ = oos.select_universe_from_window1(win1, base_cfg)
costs = {'fee_pct': 0.0006, 'slippage_pct': 0.0004}

r_with = evaluate_scenario(cfg, frames, universe, ['momentum', 'mean_reversion', 'stat_arb_pairs', 'basis_yield'], costs, window='W2')
r_without = evaluate_scenario(cfg, frames, universe, ['momentum', 'mean_reversion', 'basis_yield'], costs, window='W2')

print('WITH StatArb:')
print('  Return: {}% | Sharpe: {} | MaxDD: {}% | Trades: {} | PnL: ${}'.format(
    r_with['net_return_pct'], r_with['sharpe_ratio'], r_with['max_drawdown_pct'], r_with['directional_trades'], r_with['net_pnl']
))

print('WITHOUT StatArb:')
print('  Return: {}% | Sharpe: {} | MaxDD: {}% | Trades: {} | PnL: ${}'.format(
    r_without['net_return_pct'], r_without['sharpe_ratio'], r_without['max_drawdown_pct'], r_without['directional_trades'], r_without['net_pnl']
))
