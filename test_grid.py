import sys
from pathlib import Path
PROJECT_ROOT = Path("<repo_root>")
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import numpy as np
from core.data_loader import fetch_ohlcv

df = fetch_ohlcv("BTC/USDT", "15m", 60)

# Simulate a 10-level Grid on BTC
# Lower bound: $78,000, Upper bound: $86,000
lower_bound = 78000.0
upper_bound = 86000.0
grid_levels = 15
grid_step = (upper_bound - lower_bound) / grid_levels

print(f"=== BTC 60-DAY GRID TRADING SIMULATION ===")
print(f"Range: ${lower_bound:,.0f} to ${upper_bound:,.0f} | Levels: {grid_levels} | Step: ${grid_step:.2f} ({(grid_step/lower_bound)*100:.2f}%)")

cash = 5000.0
initial_capital = 10000.0
# Allocate 50% cash, 50% BTC initially at midpoint
mid_price = (lower_bound + upper_bound) / 2.0
btc_holdings = 5000.0 / mid_price
grid_trades = 0
grid_pnl = 0.0

# Track buy orders at each level
levels = [lower_bound + i * grid_step for i in range(grid_levels + 1)]
filled_buys = []

for idx, row in df.iterrows():
    low, high, close = row["low"], row["high"], row["close"]
    
    # Check for grid cross
    for lvl in levels:
        # If price dips through a level, buy a grid slice
        if low <= lvl <= high:
            slice_val = 400.0
            slice_units = slice_val / lvl
            fee = slice_val * 0.0004 # 0.04% maker fee
            
            # Check if we can take profit on previous lower level
            if filled_buys and (lvl - filled_buys[0]["price"]) >= grid_step * 0.9:
                bought = filled_buys.pop(0)
                profit = (lvl - bought["price"]) * bought["units"] - (fee * 2)
                grid_pnl += profit
                cash += (bought["units"] * lvl) - fee
                grid_trades += 1
            elif cash >= slice_val:
                cash -= (slice_val + fee)
                filled_buys.append({"price": lvl, "units": slice_units})

# Value remaining inventory
end_price = df.iloc[-1]["close"]
inventory_val = sum(b["units"] * end_price for b in filled_buys)
final_val = cash + inventory_val

print(f"Completed Grid Arbitrage Trades: {grid_trades}")
print(f"Realized Grid Profit:            ${grid_pnl:+,.2f}")
print(f"Final Total Portfolio Value:     ${final_val:,.2f} ({((final_val - initial_capital)/initial_capital)*100:+.2f}%)")
