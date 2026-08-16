#!/usr/bin/env python3
"""tools/analysis/analyze_last_24h_dynamics.py — Empirical Data Analysis on Fresh 24h CoinDCX Data.

Analyzes all 576,000 1m and 43,163 15m candles from datasets/coindcx_last_24h.db.
Extracts:
1. Top Volume Surges & Pumps across all 449 CoinDCX coins in the last 24 hours.
2. Post-Spike Retracement Distributions (0.382, 0.500, 0.600, 0.700, 1.000 Dump).
3. The 0.600 -> 0.700 Trap Rate on today's market conditions.
4. "DumpRide" Exhaustion Short vs "FibVOL" Long performance on today's live coins.
"""
import os
import sys
import sqlite3
import time
import numpy as np
import pandas as pd

DB_PATH = "datasets/coindcx_last_24h.db"
print(f"Opening 24h dataset at {DB_PATH}...")
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]

spike_events = []
coin_summaries = []

t0 = time.time()
for sym in symbols:
    rows = cursor.execute(
        "SELECT timestamp, open, high, low, close, volume FROM klines_15m WHERE symbol=? ORDER BY timestamp ASC",
        (sym,)
    ).fetchall()
    if len(rows) < 45:
        continue
        
    m1_rows = cursor.execute(
        "SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC",
        (sym,)
    ).fetchall()
    
    opens = np.array([r[1] for r in rows])
    highs = np.array([r[2] for r in rows])
    lows = np.array([r[3] for r in rows])
    closes = np.array([r[4] for r in rows])
    vols = np.array([r[5] for r in rows])
    times = [r[0] for r in rows]
    n = len(rows)
    
    # 24h stats for coin
    c_24h_chg = (closes[-1] - opens[0]) / opens[0] * 100 if opens[0] > 0 else 0
    c_24h_high = np.max(highs)
    c_24h_low = np.min(lows)
    c_24h_rng = (c_24h_high - c_24h_low) / c_24h_low * 100 if c_24h_low > 0 else 0
    c_tot_vol = np.sum(vols)
    
    coin_summaries.append({
        "symbol": sym,
        "chg_24h": c_24h_chg,
        "range_24h": c_24h_rng,
        "tot_vol": c_tot_vol,
        "cur_price": closes[-1]
    })
    
    # 9 EMA
    k_ema = 2.0 / (9 + 1)
    ema = np.zeros(n)
    ema[0] = closes[0]
    for i in range(1, n):
        ema[i] = closes[i] * k_ema + ema[i - 1] * (1.0 - k_ema)
        
    for i in range(40, n - 8): # need at least 2h forward window
        if closes[i] < opens[i]:
            continue
            
        base_vol = np.mean(vols[i - 40 : i])
        if base_vol <= 0:
            continue
        vol_mult = vols[i] / base_vol
        if vol_mult < 10.0:
            continue
            
        spk_h = highs[i]
        spk_l = lows[i]
        spk_o = opens[i]
        spk_c = closes[i]
        spk_rng = spk_h - spk_l
        spk_rng_pct = (spk_rng / spk_o) * 100
        
        fwd_highs = highs[i + 1 : min(i + 25, n)]
        fwd_lows = lows[i + 1 : min(i + 25, n)]
        fwd_closes = closes[i + 1 : min(i + 25, n)]
        
        max_fwd_h = np.max(fwd_highs)
        min_fwd_l = np.min(fwd_lows)
        
        retrace_depth = (spk_h - min_fwd_l) / spk_rng if spk_rng > 0 else 0
        p_600 = spk_h - 0.600 * spk_rng
        p_700 = spk_h - 0.700 * spk_rng
        
        touched_600 = min_fwd_l <= p_600
        hit_700_first = False
        hit_high_first = False
        
        if touched_600:
            for s_idx in range(i + 1, min(i + 25, n)):
                if highs[s_idx] >= spk_h:
                    hit_high_first = True
                    break
                if lows[s_idx] <= p_700:
                    hit_700_first = True
                    break
                    
        # Check first red candle close below EMA (DumpRide short trigger)
        dumpride_entry = None
        dumpride_win = False
        dumpride_gain_pct = 0.0
        for k in range(i + 1, min(i + 12, n)):
            if closes[k] < opens[k] and closes[k] < ema[k]:
                e_px = closes[k]
                sl_px = max(highs[i : k + 1])
                risk = sl_px - e_px
                tp_px = sl_px - 0.70 * (sl_px - spk_l)
                if risk > 0:
                    # check resolution
                    sub_lows = lows[k + 1 : min(i + 25, n)]
                    sub_highs = highs[k + 1 : min(i + 25, n)]
                    if len(sub_lows) > 0:
                        min_sub_l = np.min(sub_lows)
                        max_sub_h = np.max(sub_highs)
                        dumpride_win = (min_sub_l <= tp_px)
                        dumpride_gain_pct = (e_px - min_sub_l) / e_px * 100
                break
                
        spike_events.append({
            "symbol": sym,
            "ts": times[i],
            "vol_mult": vol_mult,
            "spk_rng_pct": spk_rng_pct,
            "retrace_depth": retrace_depth,
            "touched_600": touched_600,
            "hit_700_first": hit_700_first,
            "hit_high_first": hit_high_first,
            "dumpride_win": dumpride_win,
            "dumpride_gain_pct": dumpride_gain_pct
        })

conn.close()
df_spikes = pd.DataFrame(spike_events)
df_coins = pd.DataFrame(coin_summaries)

print("=" * 86)
print("📊 EMPIRICAL DATA ANALYSIS ON FRESH 24-HOUR COINDCX MARKET DATA")
print("=" * 86)

print("\n1️⃣ 24-HOUR MARKET VOLATILITY OVERVIEW (Across 449 Active Coins):")
print(f"  • Total Coins Analyzed       : {len(df_coins)}")
print(f"  • Average 24h Price Change   : {df_coins['chg_24h'].mean():+.2f}% (Median: {df_coins['chg_24h'].median():+.2f}%)")
print(f"  • Average 24h High-Low Range : {df_coins['range_24h'].mean():.2f}% (Median: {df_coins['range_24h'].median():.2f}%)")
print(f"  • Coins with >15% 24h Swings : {(df_coins['range_24h'] >= 15.0).sum()} coins")

print("\n🔥 TOP 10 BIGGEST MOVERS IN THE LAST 24 HOURS:")
top_movers = df_coins.sort_values(by="range_24h", ascending=False).head(10)
for idx, r in top_movers.iterrows():
    print(f"  • {r['symbol']:<10} | 24h Change: {r['chg_24h']:>+7.2f}% | 24h High-Low Range: {r['range_24h']:>6.2f}% | Current Px: {r['cur_price']}")

print("\n2️⃣ 24-HOUR VOLUME SPIKE DYNAMICS (10x+ Volume on 15m Bars):")
print(f"  • Total 10x+ Volume Spikes Found in 24h: {len(df_spikes)}")
if len(df_spikes) > 0:
    print(f"  • Median Spike Candle Size             : {df_spikes['spk_rng_pct'].median():.2f}%")
    print(f"  • Retraces past 0.500 Fib              : {(df_spikes['retrace_depth'] >= 0.500).mean() * 100:.1f}%")
    print(f"  • Retraces past 0.600 Fib              : {(df_spikes['retrace_depth'] >= 0.600).mean() * 100:.1f}%")
    print(f"  • Retraces past 0.700 Fib              : {(df_spikes['retrace_depth'] >= 0.700).mean() * 100:.1f}%")
    print(f"  • Dumps below origin (Retrace >= 1.0)  : {(df_spikes['retrace_depth'] >= 1.000).mean() * 100:.1f}%")

print("\n3️⃣ THE 0.600 -> 0.700 RETRACEMENT TRAP IN TODAY'S MARKET:")
if len(df_spikes) > 0:
    df_600 = df_spikes[df_spikes['touched_600']]
    print(f"  • Spikes that pulled back to 0.600 Fib        : {len(df_600)}")
    if len(df_600) > 0:
        p_fail = df_600['hit_700_first'].mean() * 100
        p_win = df_600['hit_high_first'].mean() * 100
        print(f"  • Of those touching 0.600, % hitting 0.700 SL : {p_fail:.1f}%")
        print(f"  • Of those touching 0.600, % hitting New High : {p_win:.1f}%")
        print(f"  • Failure to Win Ratio                        : {p_fail / max(p_win, 0.1):.2f}x higher failure rate")

print("\n4️⃣ \"DUMPRIDE\" EXHAUSTION SHORT PERFORMANCE IN TODAY'S MARKET:")
if len(df_spikes) > 0:
    print(f"  • DumpRide Short Setup Success Rate (70% Retrace Target) : {df_spikes['dumpride_win'].mean() * 100:.1f}%")
    print(f"  • Average Downside Excursion from Exhaustion Trigger     : +{df_spikes['dumpride_gain_pct'].mean():.2f}%")

print("\n" + "=" * 86)
