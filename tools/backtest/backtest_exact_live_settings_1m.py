#!/usr/bin/env python3
"""tools/backtest/backtest_exact_live_settings_1m.py — Exact 1-Minute Granularity Backtest of Current Live Settings.

Exact Strategy Parameters:
• Timeframe: 4H (240m) resampled from 1-minute ticks
• Volume Spike: >= 10.0x SMA(20) 4H volume baseline
• Candle Direction: Green (Close > Open, Min Pump = 0.0%)
• Entry: Short on the very first 1-minute bar following 4H candle close
• Stop Loss: Entry + 1.0x ATR(14)
• Take Profit: Entry - 2.0x ATR(14) (1:2 RR)
• Max Concurrent Positions: 10
• Risk Allocation: 1.0% account risk per trade
• Dataset: datasets/june_2026_1m.db (15.3M 1-minute bars across 357 perpetual pairs)
"""
import os
import sys
import sqlite3
import time
import numpy as np
import pandas as pd
from datetime import datetime, timezone

DB_PATH = "datasets/june_2026_1m.db"

def resample_to_4h(m1_rows):
    tf_ms = 240 * 60 * 1000 # 4 hours in ms
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

def run_exact_backtest():
    if not os.path.exists(DB_PATH):
        print(f"Dataset not found: {DB_PATH}")
        return

    print("="*105)
    print("🚀 EXACT 1-MINUTE GRANULARITY BACKTEST — CURRENT LIVE PRODUCTION SETTINGS")
    print("   • Strategy: 4H DumpRide Exhaustion Short")
    print("   • Volume Trigger: >= 10.0x SMA(20) Baseline")
    print("   • Minimum Pump: 0.0% (Any Bullish Green Candle: Close > Open)")
    print("   • Stop Loss: 1.0x ATR(14) | Take Profit: 1:2 RR (2.0x ATR)")
    print("   • Max Concurrent Positions: 10 | Risk: 1.0% per trade")
    print("="*105)
    
    t0 = time.time()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]
    
    raw_m1 = {}
    total_bars = 0
    for sym in symbols:
        rows = cursor.execute(
            "SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC",
            (sym,)
        ).fetchall()
        if len(rows) >= 500:
            raw_m1[sym] = rows
            total_bars += len(rows)
    conn.close()
    print(f"Loaded {len(raw_m1)} symbols with {total_bars:,} total 1-minute bars in {time.time()-t0:.2f}s.\n")

    # Step 1: Detect all 4H signals chronologically across all symbols
    all_signals = []
    
    for sym, m1_rows in raw_m1.items():
        tf_bars = resample_to_4h(m1_rows)
        if len(tf_bars) < 25:
            continue
            
        opens = np.array([b['open'] for b in tf_bars])
        highs = np.array([b['high'] for b in tf_bars])
        lows = np.array([b['low'] for b in tf_bars])
        closes = np.array([b['close'] for b in tf_bars])
        volumes = np.array([b['volume'] for b in tf_bars])
        timestamps = np.array([b['ts'] for b in tf_bars])
        
        for i in range(20, len(tf_bars)):
            base_vol = np.mean(volumes[i-20:i])
            if base_vol <= 0: continue
            
            vol_mult = volumes[i] / base_vol
            is_green = closes[i] > opens[i]
            
            # Exact live criteria
            if vol_mult >= 10.0 and is_green:
                entry_ts = timestamps[i] + 240 * 60 * 1000 # 4H close
                entry_px = closes[i]
                
                # ATR(14)
                trs = []
                for k in range(max(1, i-13), i+1):
                    tr = max(highs[k] - lows[k], abs(highs[k] - closes[k-1]), abs(lows[k] - closes[k-1]))
                    trs.append(tr)
                atr14 = float(np.mean(trs)) if trs else entry_px * 0.03
                
                sl_px = entry_px + (1.0 * atr14)
                tp_px = entry_px - (2.0 * atr14)
                
                pump_pct = ((closes[i] - opens[i]) / opens[i]) * 100.0
                
                all_signals.append({
                    "sym": sym,
                    "candle_ts": timestamps[i],
                    "entry_ts": entry_ts,
                    "entry_px": entry_px,
                    "sl_px": sl_px,
                    "tp_px": tp_px,
                    "atr14": atr14,
                    "vol_mult": vol_mult,
                    "pump_pct": pump_pct,
                    "risk_dist": sl_px - entry_px
                })
                
    # Sort all signals globally by time
    all_signals.sort(key=lambda x: x["entry_ts"])
    print(f"Total 4H Volume Spike Signals Detected: {len(all_signals)}")

    # Step 2: Global Portfolio Concurrency & 1-Minute Tick Stepping Simulation
    active_positions = {} # sym -> pos_dict
    executed_trades = []
    skipped_due_to_concurrency = 0
    
    # Pre-index 1-minute data timestamps for fast slicing
    m1_ts_map = {}
    for sym, m1_rows in raw_m1.items():
        m1_ts_map[sym] = np.array([r[0] for r in m1_rows])

    for sig in all_signals:
        sym = sig["sym"]
        entry_ts = sig["entry_ts"]
        
        # Check concurrency at entry_ts: close out any finished active positions
        for a_sym in list(active_positions.keys()):
            pos = active_positions[a_sym]
            if pos["exit_ts"] is not None and pos["exit_ts"] <= entry_ts:
                del active_positions[a_sym]
                
        # If coin is already in position, skip duplicate signal
        if sym in active_positions:
            continue
            
        # Max concurrent position cap (10)
        if len(active_positions) >= 10:
            skipped_due_to_concurrency += 1
            continue
            
        # Simulate on 1-Minute Bars
        m1_rows = raw_m1[sym]
        m1_ts_list = m1_ts_map[sym]
        start_idx = np.searchsorted(m1_ts_list, entry_ts)
        
        if start_idx >= len(m1_rows):
            continue
            
        entry_px = sig["entry_px"]
        sl_px = sig["sl_px"]
        tp_px = sig["tp_px"]
        risk_dist = sig["risk_dist"]
        
        exit_px = None
        exit_ts = None
        outcome = None
        r_earned = None
        
        # Walk up to 7 days (10080 1m bars)
        for m_idx in range(start_idx, min(len(m1_rows), start_idx + 10080)):
            m_ts, m_o, m_h, m_l, m_c, m_v = m1_rows[m_idx]
            
            # Check Stop Loss (Short stops out when High >= SL)
            if m_h >= sl_px:
                exit_px = sl_px
                exit_ts = m_ts
                outcome = "STOP LOSS"
                r_earned = -1.0
                break
                
            # Check Take Profit (Short TPs when Low <= TP)
            if m_l <= tp_px:
                exit_px = tp_px
                exit_ts = m_ts
                outcome = "TAKE PROFIT"
                r_earned = +2.0
                break
                
        if exit_px is None:
            # Reconcile at end of available data
            last_c = m1_rows[min(len(m1_rows)-1, start_idx + 10079)][4]
            exit_px = last_c
            exit_ts = m1_rows[min(len(m1_rows)-1, start_idx + 10079)][0]
            r_earned = (entry_px - last_c) / risk_dist
            outcome = "MARKET / TIMEOUT"
            
        dur_hrs = (exit_ts - entry_ts) / (1000 * 3600.0)
        
        trade_rec = {
            "sym": sym,
            "entry_ts": entry_ts,
            "exit_ts": exit_ts,
            "entry_px": entry_px,
            "exit_px": exit_px,
            "sl_px": sl_px,
            "tp_px": tp_px,
            "vol_mult": sig["vol_mult"],
            "pump_pct": sig["pump_pct"],
            "outcome": outcome,
            "r": r_earned,
            "is_win": r_earned > 0,
            "duration_hrs": dur_hrs
        }
        
        executed_trades.append(trade_rec)
        active_positions[sym] = {"exit_ts": exit_ts}

    # Step 3: Compute Comprehensive Performance Metrics
    n_trades = len(executed_trades)
    wins = [t for t in executed_trades if t["is_win"]]
    losses = [t for t in executed_trades if not t["is_win"]]
    tp_hits = [t for t in executed_trades if t["outcome"] == "TAKE PROFIT"]
    sl_hits = [t for t in executed_trades if t["outcome"] == "STOP LOSS"]
    
    win_rate = (len(wins) / n_trades) * 100.0
    gross_profit_r = sum(t["r"] for t in wins)
    gross_loss_r = abs(sum(t["r"] for t in losses)) if losses else 0.001
    profit_factor = gross_profit_r / gross_loss_r
    
    net_r = sum(t["r"] for t in executed_trades)
    exp_r = net_r / n_trades
    avg_duration = np.mean([t["duration_hrs"] for t in executed_trades])
    
    # Drawdown Analysis
    r_series = [t["r"] for t in executed_trades]
    cum_r = np.cumsum(r_series)
    peak_r = np.maximum.accumulate(cum_r)
    dd_r = peak_r - cum_r
    max_dd_r = np.max(dd_r) if len(dd_r) > 0 else 0.0
    
    # Consecutive streaks
    max_consec_wins = 0
    max_consec_losses = 0
    cur_w = 0
    cur_l = 0
    for t in executed_trades:
        if t["is_win"]:
            cur_w += 1
            cur_l = 0
            if cur_w > max_consec_wins: max_consec_wins = cur_w
        else:
            cur_l += 1
            cur_w = 0
            if cur_l > max_consec_losses: max_consec_losses = cur_l

    print("\n" + "="*85)
    print("📊 EXECUTIVE PERFORMANCE SUMMARY (1-MINUTE INTRABAR PRECISION)")
    print("="*85)
    print(f"• Total Signals Generated:          {len(all_signals)}")
    print(f"• Signals Executed (10 Max Cap):    {n_trades}")
    print(f"• Skipped (Portfolio Full):         {skipped_due_to_concurrency}")
    print(f"• Take Profit Hit (1:2 RR, +2.0R):  {len(tp_hits)} ({len(tp_hits)/n_trades*100:.1f}%)")
    print(f"• Stop Loss Hit (1.0x ATR, -1.0R):  {len(sl_hits)} ({len(sl_hits)/n_trades*100:.1f}%)")
    print(f"• Overall Win Rate:                 {win_rate:.2f}%")
    print(f"• Profit Factor (PF):               {profit_factor:.2f}")
    print(f"• Gross Profit:                     +{gross_profit_r:.2f}R")
    print(f"• Gross Loss:                       -{gross_loss_r:.2f}R")
    print(f"• Net Return:                       {net_r:+.2f}R ({net_r:+.2f}% Account Growth at 1% Risk)")
    print(f"• Expectancy per Trade:             {exp_r:+.3f}R / trade")
    print(f"• Max Drawdown:                     {max_dd_r:.2f}R ({max_dd_r:.2f}% Account DD)")
    print(f"• Return-to-Drawdown Ratio:         {net_r / max_dd_r:.2f}x")
    print(f"• Average Trade Duration:           {avg_duration:.1f} hours ({avg_duration/4:.1f} 4H bars)")
    print(f"• Max Consecutive Wins:             {max_consec_wins}")
    print(f"• Max Consecutive Losses:           {max_consec_losses}")
    print("="*85)

if __name__ == "__main__":
    run_exact_backtest()
