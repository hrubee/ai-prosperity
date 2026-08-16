#!/usr/bin/env python3
"""tools/analysis/analyze_spike_dynamics.py — Empirical Data Analysis on 15m Volume Spikes.

Conducts pure statistical, distribution, and path analysis on all 10x+ volume spikes
across 15.3M 1m candles in datasets/june_2026_1m.db.
No backtest simulation rules — pure empirical probability densities and metrics.
"""
import os
import sys
import sqlite3
import time
import numpy as np
import pandas as pd

DB_PATH = "datasets/june_2026_1m.db"
print(f"Loading data from {DB_PATH}...")
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]

spike_events = []

t0 = time.time()
for sym in symbols:
    rows = cursor.execute(
        "SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC",
        (sym,)
    ).fetchall()
    if len(rows) < 1000:
        continue
        
    # Aggregate to 15m
    tf_ms = 15 * 60 * 1000
    bars = []
    cur_bar = None
    cur_1m = []
    for r in rows:
        ts, o, h, l, c, v = r
        b_ts = (ts // tf_ms) * tf_ms
        if cur_bar is None or cur_bar['ts'] != b_ts:
            if cur_bar is not None:
                cur_bar['m1'] = cur_1m
                bars.append(cur_bar)
            cur_bar = {'ts': b_ts, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': v}
            cur_1m = [r]
        else:
            if h > cur_bar['high']: cur_bar['high'] = h
            if l < cur_bar['low']: cur_bar['low'] = l
            cur_bar['close'] = c
            cur_bar['volume'] += v
            cur_1m.append(r)
    if cur_bar is not None:
        cur_bar['m1'] = cur_1m
        bars.append(cur_bar)
        
    n = len(bars)
    if n < 60:
        continue
        
    opens = np.array([b['open'] for b in bars])
    highs = np.array([b['high'] for b in bars])
    lows = np.array([b['low'] for b in bars])
    closes = np.array([b['close'] for b in bars])
    vols = np.array([b['volume'] for b in bars])
    times = [b['ts'] for b in bars]
    
    # 14 ATR
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr14 = pd.Series(tr).rolling(14).mean().values
    
    # Scan for 10x volume spikes on green candle
    for i in range(40, n - 48): # leave 48 bars (12h) forward window
        if closes[i] < opens[i]:
            continue # only green spikes
            
        base_vol = np.mean(vols[i - 40 : i])
        if base_vol <= 0:
            continue
        vol_mult = vols[i] / base_vol
        if vol_mult < 10.0:
            continue
            
        # Spike properties
        spk_h = highs[i]
        spk_l = lows[i]
        spk_o = opens[i]
        spk_c = closes[i]
        spk_rng = spk_h - spk_l
        spk_rng_pct = (spk_rng / spk_o) * 100
        body_pct = (spk_c - spk_o) / spk_rng * 100 if spk_rng > 0 else 0
        upper_wick_pct = (spk_h - spk_c) / spk_rng * 100 if spk_rng > 0 else 0
        cur_atr = atr14[i] if not np.isnan(atr14[i]) else spk_rng
        atr_ratio = spk_rng / cur_atr if cur_atr > 0 else 1.0
        
        # 2nd candle confirmation check
        prev_is_green = (closes[i - 1] >= opens[i - 1]) if i >= 1 else False
        next_is_green = (closes[i + 1] >= opens[i + 1]) if i + 1 < n else False
        
        # Forward path analysis over next 16 bars (4h) and 48 bars (12h)
        fwd_highs_4h = highs[i + 1 : i + 17]
        fwd_lows_4h = lows[i + 1 : i + 17]
        fwd_closes_4h = closes[i + 1 : i + 17]
        
        fwd_highs_12h = highs[i + 1 : i + 49]
        fwd_lows_12h = lows[i + 1 : i + 49]
        
        max_h_4h = np.max(fwd_highs_4h)
        min_l_4h = np.min(fwd_lows_4h)
        max_h_12h = np.max(fwd_highs_12h)
        min_l_12h = np.min(fwd_lows_12h)
        
        # Retracement depth relative to spike range (0.0 = Spike High, 1.0 = Spike Low, >1.0 = Break below Spike Low)
        retrace_depth_4h = (spk_h - min_l_4h) / spk_rng if spk_rng > 0 else 0
        retrace_depth_12h = (spk_h - min_l_12h) / spk_rng if spk_rng > 0 else 0
        
        # Maximum upside expansion beyond spike high
        max_upside_pct_4h = (max_h_4h - spk_h) / spk_h * 100
        max_upside_pct_12h = (max_h_12h - spk_h) / spk_h * 100
        
        # Maximum downside drawdown below spike low
        max_downside_pct_4h = (spk_l - min_l_4h) / spk_l * 100 if min_l_4h < spk_l else 0.0
        
        # MFE / MAE from close
        mae_pct_4h = (spk_c - min_l_4h) / spk_c * 100
        mfe_pct_4h = (max_h_4h - spk_c) / spk_c * 100
        
        # Check if price breaks above Spike High before breaking below Spike Low
        # Intrabar sequential analysis
        first_break = None # "HIGH_FIRST" or "LOW_FIRST"
        for bar_idx in range(i + 1, min(i + 49, n)):
            b_h = highs[bar_idx]
            b_l = lows[bar_idx]
            if b_h > spk_h and b_l < spk_l:
                first_break = "BOTH"
                break
            elif b_h > spk_h:
                first_break = "HIGH_FIRST"
                break
            elif b_l < spk_l:
                first_break = "LOW_FIRST"
                break
                
        if first_break is None:
            first_break = "NEITHER"
            
        # Check retrace levels touched and whether they subsequently made a new high
        # Retrace levels: 0.382, 0.500, 0.618, 0.700, 0.786, 1.000
        p_382 = spk_h - 0.382 * spk_rng
        p_500 = spk_h - 0.500 * spk_rng
        p_600 = spk_h - 0.600 * spk_rng
        p_700 = spk_h - 0.700 * spk_rng
        p_786 = spk_h - 0.786 * spk_rng
        
        # For level 0.600 specifically:
        # If it touches 0.600, what is the subsequent min low vs subsequent max high?
        touched_600 = min_l_12h <= p_600
        hit_700_after_600 = False
        made_new_high_after_600 = False
        
        if touched_600:
            # find index when 0.60 was first touched
            touch_idx = -1
            for k_idx in range(i + 1, min(i + 49, n)):
                if lows[k_idx] <= p_600:
                    touch_idx = k_idx
                    break
            if touch_idx != -1:
                post_lows = lows[touch_idx : min(i + 49, n)]
                post_highs = highs[touch_idx : min(i + 49, n)]
                # Sequential resolution
                for s_idx in range(touch_idx, min(i + 49, n)):
                    if highs[s_idx] >= spk_h:
                        made_new_high_after_600 = True
                        break
                    if lows[s_idx] <= p_700:
                        hit_700_after_600 = True
                        break
                        
        spike_events.append({
            "symbol": sym,
            "ts": times[i],
            "vol_mult": vol_mult,
            "spk_rng_pct": spk_rng_pct,
            "body_pct": body_pct,
            "upper_wick_pct": upper_wick_pct,
            "atr_ratio": atr_ratio,
            "prev_is_green": prev_is_green,
            "next_is_green": next_is_green,
            "two_green_confirmed": prev_is_green,
            "retrace_depth_4h": retrace_depth_4h,
            "retrace_depth_12h": retrace_depth_12h,
            "max_upside_pct_4h": max_upside_pct_4h,
            "max_upside_pct_12h": max_upside_pct_12h,
            "max_downside_pct_4h": max_downside_pct_4h,
            "mae_pct_4h": mae_pct_4h,
            "mfe_pct_4h": mfe_pct_4h,
            "first_break": first_break,
            "touched_600": touched_600,
            "hit_700_after_600": hit_700_after_600,
            "made_new_high_after_600": made_new_high_after_600
        })

conn.close()
df = pd.DataFrame(spike_events)
print(f"Identified {len(df)} total 10x+ volume spike events in {time.time()-t0:.2f}s.\n")

print("=" * 85)
print("📊 EMPIRICAL DATA ANALYSIS: 15M VOLUME SPIKES & RETRACEMENT DYNAMICS")
print("=" * 85)

print("\n1️⃣ SPIKE CANDLE SIZE & VOLATILITY CHARACTERISTICS:")
print(f"  • Total 10x+ Volume Spikes Analyzed : {len(df):,}")
print(f"  • Median Spike Candle Range         : {df['spk_rng_pct'].median():.2f}% (Mean: {df['spk_rng_pct'].mean():.2f}%)")
print(f"  • 25th - 75th Percentile Range      : {df['spk_rng_pct'].quantile(0.25):.2f}% to {df['spk_rng_pct'].quantile(0.75):.2f}%")
print(f"  • Median Risk Box at Fib 0.6-0.7    : {df['spk_rng_pct'].median() * 0.10:.3f}% (0.10 × Range)")
print(f"  • % of Spikes with <0.40% Risk Box  : {(df['spk_rng_pct'] * 0.10 < 0.40).mean() * 100:.1f}%")

print("\n2️⃣ RETRACEMENT DEPTH PROBABILITY DISTRIBUTION (Where Does Price Retrace After a Spike?):")
print(f"  • Retraces past 0.382 Fib (Depth >= 0.382) : {(df['retrace_depth_4h'] >= 0.382).mean() * 100:.1f}% of spikes")
print(f"  • Retraces past 0.500 Fib (Depth >= 0.500) : {(df['retrace_depth_4h'] >= 0.500).mean() * 100:.1f}% of spikes")
print(f"  • Retraces past 0.600 Fib (Depth >= 0.600) : {(df['retrace_depth_4h'] >= 0.600).mean() * 100:.1f}% of spikes")
print(f"  • Retraces past 0.700 Fib (Depth >= 0.700) : {(df['retrace_depth_4h'] >= 0.700).mean() * 100:.1f}% of spikes")
print(f"  • Retraces past 1.000 Fib (Dumps below Low): {(df['retrace_depth_4h'] >= 1.000).mean() * 100:.1f}% of spikes")
print(f"  • Retraces past 1.500 Fib (Deep breakdown) : {(df['retrace_depth_4h'] >= 1.500).mean() * 100:.1f}% of spikes")

print("\n3️⃣ THE FIB 0.600 -> 0.700 CONDITIONAL PROBABILITY TRAP:")
df_600 = df[df['touched_600']]
p_fail_to_700 = df_600['hit_700_after_600'].mean() * 100
p_bounce_to_high = df_600['made_new_high_after_600'].mean() * 100
print(f"  • Spikes that pulled back to 0.600 Fib    : {len(df_600)} ({len(df_600)/len(df)*100:.1f}% of all spikes)")
print(f"  • Of those touching 0.600, % that drop to 0.700 SL FIRST : {p_fail_to_700:.1f}%")
print(f"  • Of those touching 0.600, % that bounce to Spike High FIRST : {p_bounce_to_high:.1f}%")
print(f"  • Probability Ratio (Failure / Success at 0.60->0.70)     : {p_fail_to_700 / max(p_bounce_to_high, 0.1):.2f}x higher probability of hitting SL")

print("\n4️⃣ BREAKOUT DIRECTIONALITY (High Break vs Low Break First):")
counts = df['first_break'].value_counts(normalize=True) * 100
print(f"  • Breaks Spike High First (Continuation) : {counts.get('HIGH_FIRST', 0):.1f}%")
print(f"  • Breaks Spike Low First (Complete Dump) : {counts.get('LOW_FIRST', 0):.1f}%")
print(f"  • Simultaneous / Choppy                  : {counts.get('BOTH', 0):.1f}%")
print(f"  • Stays inside Spike Range (Consolidation): {counts.get('NEITHER', 0):.1f}%")

print("\n5️⃣ ADVERSE EXCURSION (MAE) VS FAVORABLE EXCURSION (MFE) FROM CLOSE:")
print(f"  • Median Max Drop from Close (MAE) : -{df['mae_pct_4h'].median():.2f}%")
print(f"  • Median Max Gain from Close (MFE) : +{df['mfe_pct_4h'].median():.2f}%")
print(f"  • % of Spikes with MAE > 2.0%      : {(df['mae_pct_4h'] > 2.0).mean() * 100:.1f}%")
print(f"  • % of Spikes with MAE > 5.0%      : {(df['mae_pct_4h'] > 5.0).mean() * 100:.1f}%")

print("\n6️⃣ SUB-POPULATION FILTER ANALYSIS (Does 2 Consecutive Green Candles Help?):")
df_2g = df[df['two_green_confirmed']]
print(f"  • Total 2-Green Spikes                : {len(df_2g):,}")
print(f"  • 2-Green: Breaks High First Rate     : {(df_2g['first_break'] == 'HIGH_FIRST').mean() * 100:.1f}% (vs {(df['first_break'] == 'HIGH_FIRST').mean() * 100:.1f}% all)")
print(f"  • 2-Green: Retraces past 1.00 (Dump)  : {(df_2g['retrace_depth_4h'] >= 1.000).mean() * 100:.1f}% (vs {(df['retrace_depth_4h'] >= 1.000).mean() * 100:.1f}% all)")

print("\n" + "=" * 85)
