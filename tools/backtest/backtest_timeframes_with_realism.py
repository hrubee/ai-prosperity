#!/usr/bin/env python3
"""tools/backtest/backtest_timeframes_with_realism.py — Multi-Timeframe DumpRide Backtest with Full Market Realism.

Evaluates 30m, 1h, 2h, and 4h timeframes under realistic live market execution:
• Intrabar Execution: 1-Minute tick-stepping on 15.3M candles
• Realistic Exchange Fee: 0.10% round-trip (0.05% entry taker + 0.05% exit taker)
• Realistic Execution Slippage: 0.08% round-trip (0.04% entry + 0.04% exit)
• Total Round-trip Friction: 0.18% of Position Notional
• Stop Loss: 1.0x ATR(14) | Take Profit: 1:2 RR (2.0x ATR(14))
• Volume Surge Trigger: >= 10.0x SMA(20) on that timeframe (Green candle)
• Portfolio Concurrency Cap: Max 10 Concurrent Trades | 1.0% Account Risk per Trade
"""
import os
import sys
import sqlite3
import time
import numpy as np
import pandas as pd

DB_PATH = "datasets/june_2026_1m.db"
FRICTION_PCT = 0.0018  # 0.10% fee + 0.08% slippage = 0.18% roundtrip

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

def simulate_timeframe(tf_name, tf_min, raw_m1):
    tf_ms = tf_min * 60 * 1000
    all_trades = []
    
    for sym, m1_rows in raw_m1.items():
        bars_tf = resample_bars(m1_rows, tf_min)
        if len(bars_tf) < 25:
            continue
            
        m1_ts_list = np.array([r[0] for r in m1_rows])
        opens = np.array([b['open'] for b in bars_tf])
        highs = np.array([b['high'] for b in bars_tf])
        lows = np.array([b['low'] for b in bars_tf])
        closes = np.array([b['close'] for b in bars_tf])
        volumes = np.array([b['volume'] for b in bars_tf])
        timestamps = np.array([b['ts'] for b in bars_tf])
        
        i = 20
        while i < len(bars_tf):
            base_vol = np.mean(volumes[i-20:i])
            if base_vol <= 0:
                i += 1
                continue
                
            vol_mult = volumes[i] / base_vol
            is_green = closes[i] > opens[i]
            
            if vol_mult >= 10.0 and is_green:
                entry_ts = timestamps[i] + tf_ms
                entry_px = closes[i]
                
                # ATR(14) on this timeframe
                trs = []
                for k in range(max(1, i-13), i+1):
                    tr = max(highs[k] - lows[k], abs(highs[k] - closes[k-1]), abs(lows[k] - closes[k-1]))
                    trs.append(tr)
                atr14 = float(np.mean(trs)) if trs else entry_px * 0.02
                
                risk_dist = 1.0 * atr14
                sl_px = entry_px + risk_dist
                tp_px = entry_px - (2.0 * risk_dist)
                sl_pct = atr14 / entry_px
                
                if sl_pct <= 0:
                    i += 1
                    continue
                    
                # Friction in R = Friction_pct / sl_pct (since position size = 1% / sl_pct)
                friction_in_r = FRICTION_PCT / sl_pct
                
                sub_idx = np.searchsorted(m1_ts_list, entry_ts)
                if sub_idx >= len(m1_rows):
                    i += 1
                    continue
                    
                exit_px = None
                exit_ts = None
                r_gross = None
                outcome = None
                
                # Walk 1-minute bars up to 7 days
                for m_idx in range(sub_idx, min(len(m1_rows), sub_idx + 10080)):
                    m_ts, m_o, m_h, m_l, m_c, m_v = m1_rows[m_idx]
                    
                    if m_h >= sl_px:
                        exit_px = sl_px
                        exit_ts = m_ts
                        r_gross = -1.0
                        outcome = "STOP LOSS"
                        break
                    elif m_l <= tp_px:
                        exit_px = tp_px
                        exit_ts = m_ts
                        r_gross = +2.0
                        outcome = "TAKE PROFIT"
                        break
                        
                if exit_px is None:
                    last_i = min(len(m1_rows)-1, sub_idx + 10079)
                    exit_px = m1_rows[last_i][4]
                    exit_ts = m1_rows[last_i][0]
                    r_gross = (entry_px - exit_px) / risk_dist
                    outcome = "TIMEOUT"
                    
                r_net = r_gross - friction_in_r
                dur_hrs = (exit_ts - entry_ts) / (1000 * 3600.0)
                
                all_trades.append({
                    "sym": sym,
                    "entry_ts": entry_ts,
                    "exit_ts": exit_ts,
                    "sl_pct": sl_pct * 100.0,
                    "r_gross": r_gross,
                    "r_net": r_net,
                    "friction_r": friction_in_r,
                    "is_win": r_net > 0,
                    "outcome": outcome,
                    "duration_hrs": dur_hrs
                })
                
                i += 1
            else:
                i += 1
                
    # Sort globally by entry time
    all_trades.sort(key=lambda x: x["entry_ts"])
    
    # Portfolio Concurrency (Max 10)
    executed = []
    active = {}
    for t in all_trades:
        e_ts = t["entry_ts"]
        for a_sym in list(active.keys()):
            if active[a_sym] <= e_ts:
                del active[a_sym]
        if t["sym"] in active:
            continue
        if len(active) >= 10:
            continue
        executed.append(t)
        active[t["sym"]] = t["exit_ts"]
        
    n = len(executed)
    if n == 0:
        return None
        
    wins_net = [t for t in executed if t["is_win"]]
    losses_net = [t for t in executed if not t["is_win"]]
    
    win_rate = (len(wins_net) / n) * 100.0
    gross_p = sum(t["r_gross"] for t in executed if t["r_gross"] > 0)
    gross_l = abs(sum(t["r_gross"] for t in executed if t["r_gross"] < 0)) if any(t["r_gross"] < 0 for t in executed) else 0.001
    pf_gross = gross_p / gross_l
    
    net_p = sum(t["r_net"] for t in wins_net)
    net_l = abs(sum(t["r_net"] for t in losses_net)) if losses_net else 0.001
    pf_net = net_p / net_l
    
    gross_r = sum(t["r_gross"] for t in executed)
    total_friction_r = sum(t["friction_r"] for t in executed)
    net_r = sum(t["r_net"] for t in executed)
    
    cum_r = np.cumsum([t["r_net"] for t in executed])
    peak_r = np.maximum.accumulate(cum_r)
    max_dd = np.max(peak_r - cum_r) if len(cum_r) > 0 else 0.0
    
    avg_sl = np.mean([t["sl_pct"] for t in executed])
    med_sl = np.median([t["sl_pct"] for t in executed])
    avg_dur = np.mean([t["duration_hrs"] for t in executed])
    friction_pct_gross = (total_friction_r / gross_r * 100.0) if gross_r > 0 else 999.0
    
    return {
        "tf": tf_name,
        "trades": n,
        "avg_sl": f"{avg_sl:.2f}% (Med: {med_sl:.2f}%)",
        "win_rate": f"{win_rate:.1f}%",
        "gross_r": f"{gross_r:+.1f}R",
        "friction_r": f"{total_friction_r:.1f}R",
        "net_r": f"{net_r:+.2f}R",
        "pf_net": f"{pf_net:.2f}",
        "max_dd": f"{max_dd:.2f}R",
        "duration": f"{avg_dur:.1f}h",
        "drag_pct": f"{friction_pct_gross:.1f}%"
    }

def main():
    print("="*115)
    print("🚀 REALISTIC MULTI-TIMEFRAME DUMPRIDE BACKTEST (30m vs 1h vs 2h vs 4h)")
    print("   • Friction Modeled: 0.10% Roundtrip Exchange Fee + 0.08% Real-World Slippage (0.18% Total)")
    print("   • Granularity: 1-Minute Intrabar Walk on 15.3M Bars (357 Coins / June 2026)")
    print("   • Parameters: >= 10.0x Volume Spike | Green Candle | 1.0x ATR SL | 1:2 RR TP | Max 10 Positions")
    print("="*115)

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

    timeframes = [
        ("30-Minute (30m)", 30),
        ("1-Hour (1h)",     60),
        ("2-Hour (2h)",     120),
        ("4-Hour (4h)",     240),
    ]

    results = []
    for tf_name, tf_min in timeframes:
        t_start = time.time()
        print(f"Simulating {tf_name} with intrabar 1m execution & realism...")
        res = simulate_timeframe(tf_name, tf_min, raw_m1)
        if res:
            results.append(res)
        print(f"Completed {tf_name} in {time.time()-t_start:.2f}s.\n")

    print("="*115)
    print("📊 REALISTIC PERFORMANCE COMPARISON TABLE (AFTER 0.18% FEES + SLIPPAGE)")
    print("="*115)
    print(f"{'TIMEFRAME':<18} | {'TRADES':<7} | {'AVG SL (MEDIAN)':<18} | {'WIN RATE':<9} | {'GROSS PNL':<10} | {'FRICTION':<9} | {'NET RETURN':<12} | {'NET PF':<7} | {'MAX DD':<8} | {'AVG DURATION'} | {'FEE DRAG'}")
    print("-" * 115)
    for r in results:
        print(f"{r['tf']:<18} | {r['trades']:<7} | {r['avg_sl']:<18} | {r['win_rate']:<9} | {r['gross_r']:<10} | {r['friction_r']:<9} | {r['net_r']:<12} | {r['pf_net']:<7} | {r['max_dd']:<8} | {r['duration']:<12} | {r['drag_pct']}")
    print("="*115)

if __name__ == "__main__":
    main()
