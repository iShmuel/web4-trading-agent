import sys
from pathlib import Path
PROJECT_ROOT = Path("<repo_root>")
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import numpy as np
from core.data_loader import fetch_ohlcv

df = fetch_ohlcv(symbol="BTC/USDT", timeframe="15m", days=60)
data = df.copy()

# Bollinger Bands (20 periods, 2 std dev)
data["sma20"] = data["close"].rolling(20).mean()
data["std20"] = data["close"].rolling(20).std()
data["upper_bb"] = data["sma20"] + (data["std20"] * 2.0)
data["lower_bb"] = data["sma20"] - (data["std20"] * 2.0)

tr = pd.concat([data["high"]-data["low"], (data["high"]-data["close"].shift()).abs(), (data["low"]-data["close"].shift()).abs()], axis=1).max(axis=1)
data["atr"] = tr.ewm(alpha=1/14, adjust=False).mean()

print("\n========================================================")
print("     BOLLINGER BAND MICRO-SCALPING (15m BTC/USDT)       ")
print("========================================================")

for stop_atr in [1.5, 2.0, 2.5]:
    for tp_pct in [0.006, 0.008, 0.010, 0.012]: # 0.6% to 1.2% profit target
        equity = 10000.0
        in_pos = False
        entry_p = 0.0
        stop_p = 0.0
        tp_p = 0.0
        trades = []
        
        for idx, row in data.iterrows():
            c, h, l, a, lower, sma = row["close"], row["high"], row["low"], row["atr"], row["lower_bb"], row["sma20"]
            if pd.isna(a) or pd.isna(lower): continue
            
            if not in_pos:
                # Buy when price pierces below lower Bollinger Band (oversold micro-dip)
                if c < lower:
                    in_pos = True
                    entry_p = c * 1.0004
                    units = (equity * 0.02) / (a * stop_atr)
                    units = min(units, (equity * 0.95) / entry_p)
                    stop_p = entry_p - (a * stop_atr)
                    tp_p = entry_p * (1.0 + tp_pct)
            else:
                if l <= stop_p:
                    pnl = (stop_p - entry_p) * units - (units * stop_p * 0.001)
                    equity += pnl
                    trades.append(pnl)
                    in_pos = False
                elif h >= tp_p:
                    pnl = (tp_p - entry_p) * units - (units * tp_p * 0.001)
                    equity += pnl
                    trades.append(pnl)
                    in_pos = False
                    
        wins = [t for t in trades if t > 0]
        wr = len(wins)/len(trades)*100 if trades else 0
        ret = (equity - 10000)/10000 * 100
        if ret > 0:
            print(f"Stop: {stop_atr:.1f}x ATR | TP: {tp_pct*100:.1f}% | Ret: {ret:+6.2f}% | Final: ${equity:9.2f} | Trades: {len(trades):3d} | WR: {wr:5.1f}%")
