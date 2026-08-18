#!/usr/bin/env python3
"""tools/backtest/backtest_rr_comparison_1m.py — Exact 1-Minute Granularity RR Comparison (1:2 vs 1:3 vs 1:4 vs 1:5).

Evaluates 4H DumpRide (10.0x Volume Spike + Green Candle) with 1.0x ATR Stop Loss:
- 1:2 RR (Take Profit = 2.0x ATR)
- 1:3 RR (Take Profit = 3.0x ATR)
- 1:4 RR (Take Profit = 4.0x ATR)
- 1:5 RR (Take Profit = 5.0x ATR)
- 1:3 RR with Trailing (Activate at 1.5R, Trail 1.0R)
- 1:4 RR with Trailing (Activate at 2.0R, Trail 1.0R)
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

def run_backtest():
    if not os.path.exists(DB_PATH):
        print(f"Database not found: {DB_PATH}")
        return

    print(f"Loading 1-Minute Historical Dataset from {DB_PATH}...")
    t0 = time.time()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]
    
    raw_m1 = {}
    total_1m_bars = 0
    for sym in symbols:
        rows = cursor.execute(
            "SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC",
            (sym,)
        ).fetchall()
        if len(rows) >= 500: # Need at least ~8 hours of data
            raw_m1[sym] = rows
            total_1m_bars += len(rows)
    conn.close()
    print(f"Loaded {len(raw_m1)} symbols with {total_1m_bars:,} total 1-minute bars in {time.time()-t0:.2f}s.\n")

    # Target RR ratios to test
    rr_targets = [
        ("Fixed 1:2 RR", 2.0, False, 0, 0),
        ("Fixed 1:3 RR", 3.0, False, 0, 0),
        ("Fixed 1:4 RR", 4.0, False, 0, 0),
        ("Fixed 1:5 RR", 5.0, False, 0, 0),
        ("1:3 RR + Trail (Act 1.5R, Trail 1.0R)", 3.0, True, 1.5, 1.0),
        ("1:4 RR + Trail (Act 2.0R, Trail 1.0R)", 4.0, True, 2.0, 1.0),
        ("Uncapped Runner + Trail (Act 1.5R, Trail 1.0R)", 10.0, True, 1.5, 1.0),
    ]

    all_results = []

    for name, rr_mult, is_trail, trail_act, trail_dist in rr_targets:
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
            
            # 1-minute lookup map
            m1_dict = {row[0]: row for row in m1_rows}
            m1_ts_list = np.array([row[0] for row in m1_rows])
            
            i = 20
            while i < len(tf_bars):
                # 20-period baseline volume
                base_vol = np.mean(volumes[i-20:i])
                if base_vol <= 0:
                    i += 1
                    continue
                    
                vol_mult = volumes[i] / base_vol
                is_green = closes[i] > opens[i]
                
                if vol_mult >= 10.0 and is_green:
                    # Volume Spike Detected on 4H Close!
                    entry_ts = timestamps[i] + 240 * 60 * 1000 # Close of 4H bar
                    entry_px = closes[i]
                    
                    # Calculate ATR(14) on 4H bars
                    trs = []
                    for k in range(max(1, i-13), i+1):
                        tr = max(highs[k] - lows[k], abs(highs[k] - closes[k-1]), abs(lows[k] - closes[k-1]))
                        trs.append(tr)
                    atr = float(np.mean(trs)) if trs else entry_px * 0.03
                    
                    risk_dist = 1.0 * atr # 1.0x ATR Stop Loss
                    sl_px = entry_px + risk_dist
                    tp_px = entry_px - (rr_mult * risk_dist)
                    
                    # Simulate on 1-Minute Granularity
                    sub_idx = np.searchsorted(m1_ts_list, entry_ts)
                    if sub_idx >= len(m1_rows):
                        i += 1
                        continue
                        
                    curr_sl = sl_px
                    max_favorable_r = 0.0
                    trailing_active = False
                    trade_result = None
                    entry_time = entry_ts
                    exit_time = entry_ts
                    
                    # Track for up to 7 days (10080 minutes)
                    for m_idx in range(sub_idx, min(len(m1_rows), sub_idx + 10080)):
                        m_ts, m_o, m_h, m_l, m_c, m_v = m1_rows[m_idx]
                        
                        # Max favorable excursion (dump in our favor)
                        fav_dist = entry_px - m_l
                        fav_r = fav_dist / risk_dist
                        if fav_r > max_favorable_r:
                            max_favorable_r = fav_r
                            
                        # Trailing Stop Logic
                        if is_trail:
                            if max_favorable_r >= trail_act:
                                trailing_active = True
                                new_sl = entry_px - ((max_favorable_r - trail_dist) * risk_dist)
                                if new_sl < curr_sl:
                                    curr_sl = new_sl
                                    
                        # Check Stop Loss hit (Short position stops out when High >= SL)
                        if m_h >= curr_sl:
                            exit_px = curr_sl
                            exit_time = m_ts
                            r_earned = (entry_px - exit_px) / risk_dist
                            trade_result = {
                                "sym": sym,
                                "entry_px": entry_px,
                                "exit_px": exit_px,
                                "r": r_earned,
                                "is_win": r_earned > 0,
                                "duration_min": (exit_time - entry_time) / 60000.0,
                                "exit_reason": "Trailing SL" if trailing_active and r_earned > 0 else "Stop Loss"
                            }
                            break
                            
                        # Check Take Profit hit (Short position TPs when Low <= TP)
                        if m_l <= tp_px:
                            exit_px = tp_px
                            exit_time = m_ts
                            trade_result = {
                                "sym": sym,
                                "entry_px": entry_px,
                                "exit_px": exit_px,
                                "r": rr_mult,
                                "is_win": True,
                                "duration_min": (exit_time - entry_time) / 60000.0,
                                "exit_reason": "Take Profit Target"
                            }
                            break
                            
                    if trade_result is None:
                        # Time limit exit at market
                        last_c = m1_rows[min(len(m1_rows)-1, sub_idx + 10079)][4]
                        r_earned = (entry_px - last_c) / risk_dist
                        trade_result = {
                            "sym": sym,
                            "entry_px": entry_px,
                            "exit_px": last_c,
                            "r": r_earned,
                            "is_win": r_earned > 0,
                            "duration_min": 10080,
                            "exit_reason": "Timeout / Market"
                        }
                        
                    trades.append(trade_result)
                    
                    # Advance to avoid overlapping duplicate signals on consecutive bars
                    bars_to_skip = int(trade_result["duration_min"] // 240) + 1
                    i += max(1, bars_to_skip)
                    continue
                    
                i += 1
                
        # Calculate statistics
        if not trades:
            continue
            
        n_trades = len(trades)
        wins = [t for t in trades if t["is_win"]]
        losses = [t for t in trades if not t["is_win"]]
        win_rate = (len(wins) / n_trades) * 100.0
        
        gross_profit_r = sum(t["r"] for t in wins)
        gross_loss_r = abs(sum(t["r"] for t in losses)) if losses else 0.0001
        pf = gross_profit_r / gross_loss_r if gross_loss_r > 0 else 999.0
        
        net_r = sum(t["r"] for t in trades)
        expectancy_r = net_r / n_trades
        avg_dur_hrs = np.mean([t["duration_min"] for t in trades]) / 60.0
        
        # Calculate Max Drawdown in R
        cum_r = np.cumsum([t["r"] for t in trades])
        peak_r = np.maximum.accumulate(cum_r)
        dd_r = peak_r - cum_r
        max_dd_r = np.max(dd_r) if len(dd_r) > 0 else 0.0
        
        all_results.append({
            "name": name,
            "trades": n_trades,
            "win_rate": win_rate,
            "pf": pf,
            "net_r": net_r,
            "expectancy_r": expectancy_r,
            "max_dd_r": max_dd_r,
            "avg_dur_hrs": avg_dur_hrs
        })

    print("="*105)
    print(f"{'STRATEGY / RR CONFIGURATION':<45} | {'TRADES':<6} | {'WIN %':<7} | {'PF':<6} | {'NET RETURN':<11} | {'EXP (R)':<8} | {'MAX DD':<7} | {'AVG DURATION'}")
    print("="*105)
    for r in all_results:
        print(f"{r['name']:<45} | {r['trades']:>6} | {r['win_rate']:>6.1f}% | {r['pf']:>6.2f} | {r['net_r']:>+10.2f}R | {r['expectancy_r']:>+7.2f}R | {r['max_dd_r']:>6.2f}R | {r['avg_dur_hrs']:>6.1f} hrs")
    print("="*105)

if __name__ == "__main__":
    run_backtest()
