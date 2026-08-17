#!/usr/bin/env python3
"""tools/backtest/run_fibema_complete_sweep.py

Symbol-by-symbol streaming 1-minute granularity parameter sweep for "fibema" Long-Only strategy:
- 21 EMA calculated on candle LOWs
- Bullish impulse above 21 EMA (Low) defines Swing Low -> Swing High
- Complete sweep of Fib Entry Retracements (0.236, 0.382, 0.500, 0.618, 0.705, 0.786)
- Complete sweep of Stop Loss models (Swing Low 1.0, Fib 0.886, Below EMA, 1.0x ATR, 1.5x ATR)
- Complete sweep of RR targets (1:1.5, 1:2, 1:2.5, 1:3, 1:4)
- Evaluated on 15m, 1h, and 4h timeframes with 1-minute execution
"""
import os, sys, sqlite3, time
import numpy as np
import pandas as pd

DB_PATH = "datasets/june_2026_1m.db"
if not os.path.exists(DB_PATH):
    DB_PATH = "/root/data/june_2026_1m.db"

print(f"==========================================================================================", flush=True)
print(f"📊 FIBEMA STRATEGY COMPLETE PARAMETER SWEEP (1-MINUTE INTRABAR GRANULARITY)", flush=True)
print(f"   Database: {DB_PATH}", flush=True)
print(f"   Rules: Long Only | 21 EMA on LOW | Price > EMA(Low) | Fib Retracement Entry", flush=True)
print(f"==========================================================================================\n", flush=True)

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]

fib_entry_levels = [0.236, 0.382, 0.500, 0.618, 0.705, 0.786]
sl_types = [
    ("Swing Low (1.0)", "swing_low"),
    ("Fib 0.886", "fib_0886"),
    ("Below EMA21", "below_ema"),
    ("1.0x ATR", "atr_10"),
    ("1.5x ATR", "atr_15")
]
rr_targets = [1.5, 2.0, 2.5, 3.0, 4.0]
timeframes = [15, 60, 240]

fee_pct = 0.0010
slip_pct = 0.0010

def resample_bars_np(arr, tf_min):
    tf_ms = tf_min * 60 * 1000
    b_ts = (arr[:, 0] // tf_ms) * tf_ms
    unique_ts, split_indices = np.unique(b_ts, return_index=True)
    slices = np.split(arr, split_indices[1:])
    
    n_bars = len(slices)
    bars_ts = np.zeros(n_bars, dtype=np.int64)
    bars_o = np.zeros(n_bars)
    bars_h = np.zeros(n_bars)
    bars_l = np.zeros(n_bars)
    bars_c = np.zeros(n_bars)
    bars_v = np.zeros(n_bars)
    
    for idx, sl in enumerate(slices):
        bars_ts[idx] = int(sl[0, 0] // tf_ms * tf_ms)
        bars_o[idx] = sl[0, 1]
        bars_h[idx] = np.max(sl[:, 2])
        bars_l[idx] = np.min(sl[:, 3])
        bars_c[idx] = sl[-1, 4]
        bars_v[idx] = np.sum(sl[:, 5])
        
    return {
        "ts": bars_ts, "open": bars_o, "high": bars_h, "low": bars_l, "close": bars_c, "vol": bars_v,
        "m1_arr": arr
    }

def calc_ema_np(series, span=21):
    alpha = 2.0 / (span + 1.0)
    out = np.zeros_like(series)
    out[0] = series[0]
    for i in range(1, len(series)):
        out[i] = alpha * series[i] + (1.0 - alpha) * out[i - 1]
    return out

def calc_atr_np(h, l, c, period=14):
    n = len(h)
    tr = np.zeros(n)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr = np.zeros(n)
    atr[0] = tr[0]
    for i in range(1, n):
        if i < period:
            atr[i] = np.mean(tr[: i + 1])
        else:
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr

# Pre-extract setups symbol-by-symbol for each timeframe
all_results = []

for tf in timeframes:
    t_start = time.time()
    tf_name = f"{tf}m" if tf < 60 else f"{tf//60}h"
    print(f"Processing Timeframe: {tf_name.upper()}...", flush=True)
    
    setups = []
    for i, sym in enumerate(symbols):
        rows = cursor.execute(
            "SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC",
            (sym,)
        ).fetchall()
        if len(rows) < 100: continue
        
        m1_arr = np.array(rows, dtype=np.float64)
        data = resample_bars_np(m1_arr, tf)
        n = len(data["ts"])
        if n < 30: continue
        
        lows = data["low"]
        highs = data["high"]
        closes = data["close"]
        times = data["ts"]
        
        ema21 = calc_ema_np(lows, 21)
        atr14 = calc_atr_np(highs, lows, closes, 14)
        
        m1_times = m1_arr[:, 0]
        m1_highs = m1_arr[:, 2]
        m1_lows = m1_arr[:, 3]
        m1_closes = m1_arr[:, 4]
        
        last_setup_bar = -99
        for b_i in range(21, n - 2):
            if b_i - last_setup_bar < 4: continue
            
            if closes[b_i] <= ema21[b_i] or lows[b_i] <= ema21[b_i]:
                continue
                
            if highs[b_i] < np.max(highs[max(0, b_i-3) : b_i]):
                continue
                
            sw_low = np.min(lows[max(0, b_i-8) : b_i+1])
            sw_high = highs[b_i]
            
            impulse_size = sw_high - sw_low
            if impulse_size < (1.2 * atr14[b_i]) or (impulse_size / sw_low) < 0.010:
                continue
                
            next_ts = times[b_i + 1]
            start_idx = np.searchsorted(m1_times, next_ts)
            if start_idx >= len(m1_times): continue
            
            last_setup_bar = b_i
            setups.append({
                "sw_low": sw_low,
                "sw_high": sw_high,
                "ema": ema21[b_i],
                "atr": atr14[b_i],
                "start_idx": start_idx,
                "m1_highs": m1_highs,
                "m1_lows": m1_lows,
                "m1_closes": m1_closes,
                "tf_len": tf
            })
            
    print(f"  -> Extracted {len(setups)} valid impulse setups in {time.time()-t_start:.1f}s. Sweeping parameter grid...", flush=True)
    
    # Sweep grid
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
                    if risk_dist <= 0 or (risk_dist / entry_px) < 0.004:
                        continue
                        
                    tp_px = entry_px + (rr * risk_dist)
                    
                    m1_l = s["m1_lows"]
                    m1_h = s["m1_highs"]
                    m1_c = s["m1_closes"]
                    start_i = s["start_idx"]
                    max_scan = min(len(m1_l), start_i + (16 * s["tf_len"]))
                    
                    # Check limit fill
                    sl_slice = m1_l[start_i:max_scan]
                    fill_mask = np.where(sl_slice <= entry_px)[0]
                    if len(fill_mask) == 0:
                        continue
                    fill_idx = start_i + fill_mask[0]
                    
                    # Exit resolution
                    max_hold = min(len(m1_l), fill_idx + (32 * s["tf_len"]))
                    hold_l = m1_l[fill_idx:max_hold]
                    hold_h = m1_h[fill_idx:max_hold]
                    
                    sl_hits = np.where(hold_l <= sl_px)[0]
                    tp_hits = np.where(hold_h >= tp_px)[0]
                    
                    first_sl = sl_hits[0] if len(sl_hits) > 0 else 999999
                    first_tp = tp_hits[0] if len(tp_hits) > 0 else 999999
                    
                    if first_sl < first_tp:
                        real_sl = sl_px * (1.0 - slip_pct)
                        exit_r = -((entry_px - real_sl) / risk_dist)
                    elif first_tp < first_sl:
                        real_tp = tp_px * (1.0 - slip_pct)
                        exit_r = (real_tp - entry_px) / risk_dist
                    else:
                        exit_r = (m1_c[min(len(m1_c)-1, max_hold)] - entry_px) / risk_dist
                        
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

conn.close()

all_results.sort(key=lambda x: x["total_r"], reverse=True)

print("\n" + "=" * 115)
print(f"🏆 TOP 30 FIBEMA STRATEGY CONFIGURATIONS (1-MINUTE INTRABAR PRECISION + FEES + SLIPPAGE)")
print("=" * 115)
print(f"{'Timeframe':<10} | {'Fib Entry':<10} | {'Stop Loss Level':<18} | {'RR Target':<10} | {'Trades':<8} | {'Win Rate':<10} | {'Profit Factor':<14} | {'Total Return (R)'}")
print("-" * 115)

for r in all_results[:30]:
    print(f"{r['tf'].upper():<10} | {r['fib_entry']:<10.3f} | {r['sl_type']:<18} | 1:{r['rr']:<8.1f} | {r['trades']:<8} | {r['win_rate']:>6.1f}%    | {r['profit_factor']:<14.2f} | {r['total_r']:>+12.2f} R")

print("=" * 115)
