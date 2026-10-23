import sys
from pathlib import Path
PROJECT_ROOT = Path("<repo_root>")
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import numpy as np
from core.data_loader import fetch_ohlcv

for timeframe in ["1h", "15m"]:
    print(f"\n========================================================")
    print(f"      TESTING TREND-PULLBACK STRATEGY ({timeframe})")
    print(f"========================================================")
    df = fetch_ohlcv(symbol="BTC/USDT", timeframe=timeframe, days=60)
    
    # Compute indicators
    data = df.copy()
    data["ema50"] = data["close"].ewm(span=50, adjust=False).mean()
    data["ema20"] = data["close"].ewm(span=20, adjust=False).mean()
    
    delta = data["close"].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss
    data["rsi"] = 100 - (100 / (1 + rs))
    
    tr = pd.concat([data["high"]-data["low"], (data["high"]-data["close"].shift()).abs(), (data["low"]-data["close"].shift()).abs()], axis=1).max(axis=1)
    data["atr"] = tr.ewm(alpha=1/14, adjust=False).mean()
    
    # Test different RSI dip thresholds: 30, 35, 40
    for rsi_thresh in [30, 35, 40]:
        equity = 10000.0
        in_pos = False
        entry_p = 0.0
        stop_p = 0.0
        tp_p = 0.0
        trades = []
        
        for idx, row in data.iterrows():
            c, h, l, a, rsi, ema50 = row["close"], row["high"], row["low"], row["atr"], row["rsi"], row["ema50"]
            if pd.isna(a) or pd.isna(rsi) or pd.isna(ema50): continue
            
            if not in_pos:
                # Buy ONLY if in macro uptrend (close > ema50) AND pullback occurs (rsi < rsi_thresh)
                if c > ema50 and rsi < rsi_thresh:
                    in_pos = True
                    entry_p = c * 1.0004
                    units = (equity * 0.02) / (a * 2.0)
                    units = min(units, (equity * 0.95) / entry_p)
                    stop_p = entry_p - (a * 2.0)
                    tp_p = entry_p + (a * 2.5) # +2.5 ATR target
            else:
                if l <= stop_p:
                    pnl = (stop_p - entry_p) * units - (units * stop_p * 0.001)
                    equity += pnl
                    trades.append(pnl)
                    in_pos = False
                elif h >= tp_p or rsi > 65:
                    exec_exit = min(tp_p, h) if h >= tp_p else c
                    pnl = (exec_exit - entry_p) * units - (units * exec_exit * 0.001)
                    equity += pnl
                    trades.append(pnl)
                    in_pos = False
                    
        wins = [t for t in trades if t > 0]
        wr = len(wins)/len(trades)*100 if trades else 0
        ret = (equity - 10000)/10000 * 100
        print(f"RSI Dip < {rsi_thresh:2d} | Return: {ret:+6.2f}% | Final: ${equity:9.2f} | Trades: {len(trades):3d} | Win Rate: {wr:5.1f}%")
