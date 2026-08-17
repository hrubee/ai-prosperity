#!/usr/bin/env python3
"""tools/backtest/fast_backtest_fibema.py

Fast, institutional 1-minute granularity parameter sweep for "fibema" Long-Only strategy:
- 21 EMA calculated on candle LOWs
- Bullish impulse above 21 EMA (Low) defines Swing Low -> Swing High
- Complete sweep of Fib Entry Retracements (0.382, 0.500, 0.618, 0.705, 0.786)
- Complete sweep of Stop Loss models (Swing Low 1.0, Fib 0.886, Below EMA, 1.0x ATR, 1.5x ATR)
- Complete sweep of RR targets (1:1.5, 1:2, 1:2.5, 1:3, 1:4)
- Evaluated on 15m, 1h, and 4h timeframes with 1-minute tick-level execution
"""
import sqlite3
import numpy as np
import pandas as pd

DB_PATH = "datasets/june_2026_1m.db"
print(f"Loading 1-minute dataset from {DB_PATH}...", flush=True)

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]

raw_m1 = {}
for sym in symbols:
    rows = cursor.execute(
        "SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC",
        (sym,)
    ).fetchall()
    if len(rows) >= 100:
        raw_m1[sym] = rows
conn.close()
print(f"Loaded {len(raw_m1)} coins.", flush=True)

def resample_bars(m1_rows, tf_min):
    tf_ms = tf_min * 60 * 1000
    tf_bars = []
    cur_bar = None
    cur_1m_slice = []
    for row in m1_rows:
        ts, o, h, l, c, v = row
        b_ts = (ts // tf_ms) * tf_ms
        if cur_bar is None or cur_bar['ts'] != b_ts:
            if cur_bar is not None:
                cur_bar['m1_bars'] = cur_1m_slice
                tf_bars.append(cur_bar)
            cur_bar = {'ts': b_ts, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': v}
            cur_1m_slice = [row]
        else:
            if h > cur_bar['high']: cur_bar['high'] = h
            if l < cur_bar['low']: cur_bar['low'] = l
            cur_bar['close'] = c
            cur_bar['volume'] += v
            cur_1m_slice.append(row)
    if cur_bar is not None:
        cur_bar['m1_bars'] = cur_1m_slice
        tf_bars.append(cur_bar)
    return tf_bars

fee_pct = 0.0010
slip_pct = 0.0010

fib_entry_levels = [0.382, 0.500, 0.618, 0.705, 0.786]
sl_types = [
    ("Swing Low (1.0)", "swing_low"),
    ("Fib 0.886", "fib_0886"),
    ("Below EMA21", "below_ema"),
    ("1.0x ATR", "atr_10"),
    ("1.5x ATR", "atr_15")
]
rr_targets = [1.5, 2.0, 2.5, 3.0, 4.0]
timeframes = [15, 60, 240]

all_results = []

for tf in timeframes:
    tf_name = f"{tf}m" if tf < 60 else f"{tf//60}h"
    print(f"\n================================================================================")
    print(f"📊 EVALUATING TIMEFRAME: {tf_name.upper()} WITH 1-MINUTE INTRABAR PRECISION")
    print(f"================================================================================", flush=True)
    
    setups = []
    for sym, m1_rows in raw_m1.items():
        bars = resample_bars(m1_rows, tf)
        if len(bars) < 30: continue
        
        lows = pd.Series([b['low'] for b in bars])
        highs = pd.Series([b['high'] for b in bars])
        closes = pd.Series([b['close'] for b in bars])
        opens = pd.Series([b['open'] for b in bars])
        times = [b['ts'] for b in bars]
        
        # 21 EMA on LOW
        ema21 = lows.ewm(span=21, adjust=False).mean().values
        
        # ATR 14
        tr = np.maximum(highs - lows, np.maximum(abs(highs - closes.shift(1)), abs(lows - closes.shift(1)))).fillna(highs - lows)
        atr14 = tr.rolling(14).mean().fillna(highs - lows).values
        
        m1_times = np.array([r[0] for r in m1_rows])
        m1_highs = np.array([r[2] for r in m1_rows])
        m1_lows = np.array([r[3] for r in m1_rows])
        m1_closes = np.array([r[4] for r in m1_rows])
        
        n = len(bars)
        last_setup_bar = -99
        for i in range(21, n - 2):
            if i - last_setup_bar < 4: continue # Prevent overlapping swing duplicate entries
            
            # Condition 1: Price is above 21 EMA on Low
            if closes.iloc[i] <= ema21[i] or lows.iloc[i] <= ema21[i]:
                continue
                
            # Condition 2: Local Swing High (higher than previous 3 bars)
            if highs.iloc[i] < np.max(highs.iloc[max(0, i-3) : i]):
                continue
                
            # Find Swing Low: lowest low of previous 8 bars (where impulse began)
            sw_low_idx = max(0, i - 8) + np.argmin(lows.iloc[max(0, i-8) : i+1].values)
            sw_low = lows.iloc[sw_low_idx]
            sw_high = highs.iloc[i]
            
            impulse_size = sw_high - sw_low
            # Impulse must be significant (>= 1.2x ATR)
            if impulse_size < (1.2 * atr14[i]) or (impulse_size / sw_low) < 0.010:
                continue
                
            next_ts = times[i + 1]
            start_idx = np.searchsorted(m1_times, next_ts)
            if start_idx >= len(m1_times): continue
            
            last_setup_bar = i
            setups.append({
                "sym": sym,
                "sw_low": sw_low,
                "sw_high": sw_high,
                "ema": ema21[i],
                "atr": atr14[i],
                "start_idx": start_idx,
                "m1_highs": m1_highs,
                "m1_lows": m1_lows,
                "m1_closes": m1_closes,
                "tf_len": tf
            })
            
    print(f"   Found {len(setups)} valid impulse swings. Sweeping 125 Fib/SL/RR combinations...", flush=True)
    
    for fib_entry in fib_entry_levels:
        for sl_name, sl_mode in sl_types:
            for rr in rr_targets:
                trades = []
                for s in setups:
                    fib_range = s["sw_high"] - s["sw_low"]
                    entry_px = s["sw_high"] - (fib_entry * fib_range)
                    
                    if sl_mode == "swing_low":
                        sl_px = s["sw_low"] * 0.998
                    elif sl_mode == "fib_0886":
                        sl_px = s["sw_high"] - (0.886 * fib_range)
                    elif sl_mode == "below_ema":
                        sl_px = s["ema"] * 0.997
                    elif sl_mode == "atr_10":
                        sl_px = entry_px - (1.0 * s["atr"])
                    elif sl_mode == "atr_15":
                        sl_px = entry_px - (1.5 * s["atr"])
                    else:
                        sl_px = s["sw_low"]
                        
                    risk_dist = entry_px - sl_px
                    if risk_dist <= 0 or (risk_dist / entry_px) < 0.005:
                        continue
                        
                    tp_px = entry_px + (rr * risk_dist)
                    
                    m1_l = s["m1_lows"]
                    m1_h = s["m1_highs"]
                    m1_c = s["m1_closes"]
                    start_i = s["start_idx"]
                    max_scan = min(len(m1_l), start_i + (16 * s["tf_len"]))
                    
                    # 1m Limit Order Fill Check
                    fill_idx = -1
                    for idx in range(start_i, max_scan):
                        if m1_l[idx] <= entry_px:
                            fill_idx = idx
                            break
                    if fill_idx == -1: continue
                    
                    # 1m Intrabar Trade Exit Resolution
                    exit_r = None
                    max_hold = min(len(m1_l), fill_idx + (32 * s["tf_len"]))
                    for idx in range(fill_idx, max_hold):
                        if m1_l[idx] <= sl_px:
                            real_sl = sl_px * (1.0 - slip_pct)
                            exit_r = -((entry_px - real_sl) / risk_dist)
                            break
                        elif m1_h[idx] >= tp_px:
                            real_tp = tp_px * (1.0 - slip_pct)
                            exit_r = (real_tp - entry_px) / risk_dist
                            break
                    if exit_r is None and fill_idx < len(m1_l):
                        exit_r = (m1_c[min(len(m1_c)-1, max_hold)] - entry_px) / risk_dist
                        
                    if exit_r is not None:
                        fee_r = (2.0 * fee_pct) / (risk_dist / entry_px)
                        trades.append(exit_r - fee_r)
                        
                n = len(trades)
                if n < 10: continue
                wins = [t for t in trades if t > 0]
                losses = [t for t in trades if t <= 0]
                wr = len(wins) / n * 100
                tot_r = sum(trades)
                gw = sum(wins)
                gl = abs(sum(losses)) if losses else 0.001
                pf = gw / gl
                
                all_results.append({
                    "tf": tf_name,
                    "fib_entry": fib_entry,
                    "sl_type": sl_name,
                    "rr": rr,
                    "trades": n,
                    "win_rate": wr,
                    "profit_factor": pf,
                    "total_r": tot_r
                })

all_results.sort(key=lambda x: x["total_r"], reverse=True)

print("\n" + "=" * 115)
print(f"🏆 TOP 30 FIBEMA STRATEGY CONFIGURATIONS (1-MINUTE INTRABAR GRANULARITY + FEES + SLIPPAGE)")
print("=" * 115)
print(f"{'Timeframe':<10} | {'Fib Entry':<10} | {'Stop Loss Level':<18} | {'RR Target':<10} | {'Trades':<8} | {'Win Rate':<10} | {'Profit Factor':<14} | {'Total Return (R)'}")
print("-" * 115)

for r in all_results[:30]:
    print(f"{r['tf'].upper():<10} | {r['fib_entry']:<10.3f} | {r['sl_type']:<18} | 1:{r['rr']:<8.1f} | {r['trades']:<8} | {r['win_rate']:>6.1f}%    | {r['profit_factor']:<14.2f} | {r['total_r']:>+12.2f} R")

print("=" * 115)
