#!/usr/bin/env python3
import os
import sys
import sqlite3
import time
import numpy as np

DB_PATH = "datasets/june_2026_1m.db"

def run_fast_exact_backtest():
    print("="*90)
    print("🚀 EXACT 1-MINUTE INTRABAR BACKTEST — LIVE DUMPRIDE 4H SETTINGS")
    print("   • Volume Spike Trigger: >= 10.0x SMA(20)")
    print("   • Direction: Green Candle (Close > Open, Min Pump = 0.0%)")
    print("   • Stop Loss: 1.0x ATR(14) | Take Profit: 1:2 RR (2.0x ATR)")
    print("   • Concurrency Cap: 10 Max Positions | 1% Account Risk per Trade")
    print("="*90)
    
    t0 = time.time()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]
    
    all_trades = []
    
    for sym in symbols:
        rows = cursor.execute(
            "SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC",
            (sym,)
        ).fetchall()
        if len(rows) < 1000:
            continue
            
        m1_arr = np.array(rows)
        ts_1m = m1_arr[:, 0]
        h_1m = m1_arr[:, 2]
        l_1m = m1_arr[:, 3]
        c_1m = m1_arr[:, 4]
        
        # Resample to 4H
        tf_ms = 240 * 60 * 1000
        b_ts = (ts_1m // tf_ms) * tf_ms
        unique_b_ts, split_idx = np.unique(b_ts, return_index=True)
        
        tf_bars = []
        for i in range(len(split_idx)):
            start_i = split_idx[i]
            end_i = split_idx[i+1] if i+1 < len(split_idx) else len(m1_arr)
            slice_1m = m1_arr[start_i:end_i]
            tf_bars.append({
                "ts": unique_b_ts[i],
                "open": slice_1m[0, 1],
                "high": np.max(slice_1m[:, 2]),
                "low": np.min(slice_1m[:, 3]),
                "close": slice_1m[-1, 4],
                "volume": np.sum(slice_1m[:, 5]),
                "m1_start_idx": start_i,
                "m1_end_idx": end_i
            })
            
        if len(tf_bars) < 25:
            continue
            
        closes_4h = np.array([b["close"] for b in tf_bars])
        opens_4h = np.array([b["open"] for b in tf_bars])
        highs_4h = np.array([b["high"] for b in tf_bars])
        lows_4h = np.array([b["low"] for b in tf_bars])
        vols_4h = np.array([b["volume"] for b in tf_bars])
        ts_4h = np.array([b["ts"] for b in tf_bars])
        
        for i in range(20, len(tf_bars)):
            base_v = np.mean(vols_4h[i-20:i])
            if base_v <= 0: continue
            
            vol_mult = vols_4h[i] / base_v
            is_green = closes_4h[i] > opens_4h[i]
            
            if vol_mult >= 10.0 and is_green:
                entry_px = closes_4h[i]
                entry_ts = ts_4h[i] + tf_ms
                
                # ATR(14)
                trs = []
                for k in range(max(1, i-13), i+1):
                    tr = max(highs_4h[k] - lows_4h[k], abs(highs_4h[k] - closes_4h[k-1]), abs(lows_4h[k] - closes_4h[k-1]))
                    trs.append(tr)
                atr14 = float(np.mean(trs)) if trs else entry_px * 0.03
                
                sl_px = entry_px + (1.0 * atr14)
                tp_px = entry_px - (2.0 * atr14)
                risk_dist = sl_px - entry_px
                
                # Search 1-minute slice starting from entry_ts
                m1_start = np.searchsorted(ts_1m, entry_ts)
                if m1_start >= len(ts_1m): continue
                
                # Walk 1-minute bars
                exit_px = None
                exit_ts = None
                r_earned = None
                outcome = None
                
                for m_i in range(m1_start, min(len(ts_1m), m1_start + 10080)):
                    m_h = h_1m[m_i]
                    m_l = l_1m[m_i]
                    
                    if m_h >= sl_px:
                        exit_px = sl_px
                        exit_ts = ts_1m[m_i]
                        r_earned = -1.0
                        outcome = "STOP LOSS"
                        break
                    elif m_l <= tp_px:
                        exit_px = tp_px
                        exit_ts = ts_1m[m_i]
                        r_earned = +2.0
                        outcome = "TAKE PROFIT"
                        break
                        
                if exit_px is None:
                    last_i = min(len(ts_1m)-1, m1_start + 10079)
                    exit_px = c_1m[last_i]
                    exit_ts = ts_1m[last_i]
                    r_earned = (entry_px - exit_px) / risk_dist
                    outcome = "TIMEOUT"
                    
                dur_hrs = (exit_ts - entry_ts) / (1000 * 3600.0)
                all_trades.append({
                    "sym": sym,
                    "entry_ts": entry_ts,
                    "exit_ts": exit_ts,
                    "entry_px": entry_px,
                    "exit_px": exit_px,
                    "r": r_earned,
                    "is_win": r_earned > 0,
                    "outcome": outcome,
                    "duration_hrs": dur_hrs
                })
                
    conn.close()
    
    # Sort globally by entry time
    all_trades.sort(key=lambda x: x["entry_ts"])
    
    # Concurrency simulation (Max 10)
    executed = []
    active = {}
    skipped = 0
    
    for t in all_trades:
        sym = t["sym"]
        e_ts = t["entry_ts"]
        
        # Clean finished active positions
        for a_sym in list(active.keys()):
            if active[a_sym] <= e_ts:
                del active[a_sym]
                
        if sym in active:
            continue
            
        if len(active) >= 10:
            skipped += 1
            continue
            
        executed.append(t)
        active[sym] = t["exit_ts"]
        
    n_trades = len(executed)
    wins = [t for t in executed if t["is_win"]]
    losses = [t for t in executed if not t["is_win"]]
    tp_hits = [t for t in executed if t["outcome"] == "TAKE PROFIT"]
    sl_hits = [t for t in executed if t["outcome"] == "STOP LOSS"]
    
    win_rate = (len(wins) / n_trades) * 100.0
    gross_p = sum(t["r"] for t in wins)
    gross_l = abs(sum(t["r"] for t in losses)) if losses else 0.001
    pf = gross_p / gross_l
    net_r = sum(t["r"] for t in executed)
    exp_r = net_r / n_trades
    avg_dur = np.mean([t["duration_hrs"] for t in executed])
    
    cum_r = np.cumsum([t["r"] for t in executed])
    peak_r = np.maximum.accumulate(cum_r)
    dd_r = peak_r - cum_r
    max_dd = np.max(dd_r) if len(dd_r) > 0 else 0.0
    
    print(f"Loaded and simulated in {time.time()-t0:.2f}s.\n")
    print("="*85)
    print("📊 EXACT 1-MINUTE INTRABAR PERFORMANCE RESULTS")
    print("="*85)
    print(f"• Total Raw Signals:               {len(all_trades)}")
    print(f"• Executed Trades (10 Max Cap):    {n_trades}")
    print(f"• Skipped by Concurrency:          {skipped}")
    print(f"• Take Profit Hit (+2.0R):         {len(tp_hits)} ({len(tp_hits)/n_trades*100:.1f}%)")
    print(f"• Stop Loss Hit (-1.0R):           {len(sl_hits)} ({len(sl_hits)/n_trades*100:.1f}%)")
    print(f"• Overall Win Rate:                {win_rate:.2f}%")
    print(f"• Profit Factor (PF):              {pf:.2f}")
    print(f"• Gross Profit:                    +{gross_p:.2f}R")
    print(f"• Gross Loss:                      -{gross_l:.2f}R")
    print(f"• Net Return:                      {net_r:+.2f}R ({net_r:+.2f}% Account Growth at 1% Risk)")
    print(f"• Expectancy per Trade:            {exp_r:+.3f}R / trade")
    print(f"• Max Drawdown:                    {max_dd:.2f}R ({max_dd:.2f}% Account Drawdown)")
    print(f"• Return / Max Drawdown Ratio:     {net_r / max_dd:.2f}x")
    print(f"• Average Trade Duration:          {avg_dur:.1f} hours ({avg_dur/4:.1f} 4H bars)")
    print("="*85)

if __name__ == "__main__":
    run_fast_exact_backtest()
