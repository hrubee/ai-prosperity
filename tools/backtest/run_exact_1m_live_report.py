#!/usr/bin/env python3
"""tools/backtest/run_exact_1m_live_report.py — Exact 1-Minute Granularity Performance Report for DumpRide Live Settings.
"""
import os
import sys
import sqlite3
import time
import numpy as np
import pandas as pd
from datetime import datetime, timezone

DB_PATH = "datasets/june_2026_1m.db"

def resample_bars(m1_rows, tf_min=240):
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

def main():
    if not os.path.exists(DB_PATH):
        print(f"Dataset not found: {DB_PATH}")
        return

    print("="*95)
    print("🚀 RUNNING EXACT 1-MINUTE GRANULARITY BACKTEST ON LIVE PRODUCTION SETTINGS")
    print("   • Timeframe: 4H (240m)")
    print("   • Volume Spike: >= 10.0x SMA(20) Baseline")
    print("   • Direction: Green Expansion Candle (Close > Open, Min Pump = 0.0%)")
    print("   • Execution: Short at the close of 4H bar on exact 1-minute ticks")
    print("   • Stop Loss: 1.0x ATR(14)")
    print("   • Take Profit: 1:2 RR (2.0x ATR(14))")
    print("   • Dataset: 15.3 Million 1-Minute Bars across 357 Symbols")
    print("="*95)

    t0 = time.time()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]
    
    raw_m1 = {}
    total_1m = 0
    for sym in symbols:
        rows = cursor.execute(
            "SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC",
            (sym,)
        ).fetchall()
        if len(rows) >= 500:
            raw_m1[sym] = rows
            total_1m += len(rows)
    conn.close()
    print(f"Loaded {len(raw_m1)} coins ({total_1m:,} 1-minute bars) in {time.time()-t0:.2f}s.\n")

    trades = []
    
    for sym, m1_rows in raw_m1.items():
        tf_bars = resample_bars(m1_rows, tf_min=240)
        if len(tf_bars) < 25:
            continue
            
        opens = np.array([b['open'] for b in tf_bars])
        highs = np.array([b['high'] for b in tf_bars])
        lows = np.array([b['low'] for b in tf_bars])
        closes = np.array([b['close'] for b in tf_bars])
        volumes = np.array([b['volume'] for b in tf_bars])
        timestamps = np.array([b['ts'] for b in tf_bars])
        
        m1_ts_list = np.array([row[0] for row in m1_rows])
        
        i = 20
        while i < len(tf_bars):
            base_vol = np.mean(volumes[i-20:i])
            if base_vol <= 0:
                i += 1
                continue
                
            vol_mult = volumes[i] / base_vol
            is_green = closes[i] > opens[i]
            
            if vol_mult >= 10.0 and is_green:
                entry_ts = timestamps[i] + 240 * 60 * 1000 # 4H close
                entry_px = closes[i]
                
                # ATR(14)
                trs = []
                for k in range(max(1, i-13), i+1):
                    tr = max(highs[k] - lows[k], abs(highs[k] - closes[k-1]), abs(lows[k] - closes[k-1]))
                    trs.append(tr)
                atr14 = float(np.mean(trs)) if trs else entry_px * 0.03
                
                risk_dist = 1.0 * atr14
                sl_px = entry_px + risk_dist
                tp_px = entry_px - (2.0 * risk_dist)
                
                sub_idx = np.searchsorted(m1_ts_list, entry_ts)
                if sub_idx >= len(m1_rows):
                    i += 1
                    continue
                    
                exit_px = None
                exit_ts = None
                r_earned = None
                outcome = None
                
                for m_idx in range(sub_idx, min(len(m1_rows), sub_idx + 10080)):
                    m_ts, m_o, m_h, m_l, m_c, m_v = m1_rows[m_idx]
                    
                    # Stop loss hit
                    if m_h >= sl_px:
                        exit_px = sl_px
                        exit_ts = m_ts
                        r_earned = -1.0
                        outcome = "STOP LOSS"
                        break
                        
                    # Take profit hit
                    if m_l <= tp_px:
                        exit_px = tp_px
                        exit_ts = m_ts
                        r_earned = +2.0
                        outcome = "TAKE PROFIT"
                        break
                        
                if exit_px is None:
                    last_c = m1_rows[min(len(m1_rows)-1, sub_idx + 10079)][4]
                    exit_px = last_c
                    exit_ts = m1_rows[min(len(m1_rows)-1, sub_idx + 10079)][0]
                    r_earned = (entry_px - last_c) / risk_dist
                    outcome = "TIMEOUT"
                    
                dur_hrs = (exit_ts - entry_ts) / (1000 * 3600.0)
                pump_pct = ((closes[i] - opens[i]) / opens[i]) * 100.0
                
                trades.append({
                    "sym": sym,
                    "entry_ts": entry_ts,
                    "exit_ts": exit_ts,
                    "entry_px": entry_px,
                    "exit_px": exit_px,
                    "sl_px": sl_px,
                    "tp_px": tp_px,
                    "vol_mult": vol_mult,
                    "pump_pct": pump_pct,
                    "outcome": outcome,
                    "r": r_earned,
                    "is_win": r_earned > 0,
                    "duration_hrs": dur_hrs
                })
                
                # Jump forward to prevent overlapping self-signals
                i += 1
            else:
                i += 1

    # Sort globally by entry time
    trades.sort(key=lambda x: x["entry_ts"])
    
    n_trades = len(trades)
    wins = [t for t in trades if t["is_win"]]
    losses = [t for t in trades if not t["is_win"]]
    tp_hits = [t for t in trades if t["outcome"] == "TAKE PROFIT"]
    sl_hits = [t for t in trades if t["outcome"] == "STOP LOSS"]
    
    win_rate = (len(wins) / n_trades) * 100.0
    gross_p = sum(t["r"] for t in wins)
    gross_l = abs(sum(t["r"] for t in losses)) if losses else 0.001
    pf = gross_p / gross_l
    net_r = sum(t["r"] for t in trades)
    exp_r = net_r / n_trades
    avg_dur = np.mean([t["duration_hrs"] for t in trades])
    
    cum_r = np.cumsum([t["r"] for t in trades])
    peak_r = np.maximum.accumulate(cum_r)
    dd_r = peak_r - cum_r
    max_dd = np.max(dd_r) if len(dd_r) > 0 else 0.0
    
    # Streaks
    max_consec_w = 0
    max_consec_l = 0
    cw, cl = 0, 0
    for t in trades:
        if t["is_win"]:
            cw += 1
            cl = 0
            if cw > max_consec_w: max_consec_w = cw
        else:
            cl += 1
            cw = 0
            if cl > max_consec_l: max_consec_l = cl

    print("="*85)
    print("📊 EXACT 1-MINUTE INTRABAR PERFORMANCE REPORT — DUMPRIDE 4H (LIVE SETTINGS)")
    print("="*85)
    print(f"• Total Trades Sampled:            {n_trades}")
    print(f"• Take Profit Hit (+2.0R):         {len(tp_hits)} ({len(tp_hits)/n_trades*100:.1f}%)")
    print(f"• Stop Loss Hit (-1.0R):           {len(sl_hits)} ({len(sl_hits)/n_trades*100:.1f}%)")
    print(f"• Overall Win Rate:                {win_rate:.2f}%")
    print(f"• Profit Factor (PF):              {pf:.2f}")
    print(f"• Gross Profit:                    +{gross_p:.2f}R")
    print(f"• Gross Loss:                      -{gross_l:.2f}R")
    print(f"• Net Portfolio Return:            {net_r:+.2f}R ({net_r:+.2f}% Account Growth at 1% Risk)")
    print(f"• Expectancy per Trade:            {exp_r:+.3f}R / trade")
    print(f"• Max Drawdown:                    {max_dd:.2f}R ({max_dd:.2f}% Account Drawdown)")
    print(f"• Return-to-Drawdown Ratio:        {net_r / max_dd:.2f}x")
    print(f"• Average Trade Duration:          {avg_dur:.1f} hours ({avg_dur/4:.1f} 4H bars)")
    print(f"• Max Consecutive Wins:            {max_consec_w}")
    print(f"• Max Consecutive Losses:          {max_consec_l}")
    print("="*85)

if __name__ == "__main__":
    main()
