#!/usr/bin/env python3
"""tools/backtest/backtest_4months_all_timeframes.py — 4-Month Multi-Timeframe Realistic DumpRide Backtest.

Dataset: April, May, June, July 2026 (58.1 Million 1-Minute Bars across 330+ coins)
Timeframes: 15m, 30m, 1h, 2h, 4h
Realism:
- Intrabar 1-minute tick stepping
- 0.10% Round-trip Exchange Taker Fees (0.05% entry + 0.05% exit)
- 0.08% Round-trip Market Order Slippage (0.04% entry + 0.04% exit)
- Total Friction = 0.18% on Position Notional
- 1.0x ATR(14) Stop Loss | 1:2 RR Take Profit (2.0x ATR)
- Max 10 Concurrent Positions | 1.0% Account Risk per Trade
"""
import os
import sys
import sqlite3
import time
import numpy as np
import pandas as pd
from datetime import datetime, timezone

DATASETS = [
    ("April 2026", "datasets/april_2026_1m.db"),
    ("May 2026",   "datasets/may_2026_1m.db"),
    ("June 2026",  "datasets/june_2026_1m.db"),
    ("July 2026",  "datasets/july_2026_1m.db"),
]

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

def load_month_data(db_path):
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]
    
    raw_m1 = {}
    for sym in symbols:
        rows = cursor.execute(
            "SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC",
            (sym,)
        ).fetchall()
        if len(rows) >= 500:
            raw_m1[sym] = rows
    conn.close()
    return raw_m1

def evaluate_trades_for_month(raw_m1, tf_min):
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
                
                # ATR(14)
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
                    
                friction_in_r = FRICTION_PCT / sl_pct
                
                sub_idx = np.searchsorted(m1_ts_list, entry_ts)
                if sub_idx >= len(m1_rows):
                    i += 1
                    continue
                    
                exit_px = None
                exit_ts = None
                r_gross = None
                outcome = None
                
                # Walk 1-minute bars
                max_walk = 2880 if tf_min <= 30 else 10080
                for m_idx in range(sub_idx, min(len(m1_rows), sub_idx + max_walk)):
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
                    last_i = min(len(m1_rows)-1, sub_idx + max_walk - 1)
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
                
    return all_trades

def apply_portfolio_concurrency(all_trades, max_concurrent=10):
    all_trades.sort(key=lambda x: x["entry_ts"])
    executed = []
    active = {}
    for t in all_trades:
        e_ts = t["entry_ts"]
        for a_sym in list(active.keys()):
            if active[a_sym] <= e_ts:
                del active[a_sym]
        if t["sym"] in active:
            continue
        if len(active) >= max_concurrent:
            continue
        executed.append(t)
        active[t["sym"]] = t["exit_ts"]
    return executed

def main():
    print("="*120)
    print("🚀 4-MONTH COMPREHENSIVE MULTI-TIMEFRAME DUMPRIDE BACKTEST (58.1M 1-MINUTE BARS)")
    print("   • Span: April 2026, May 2026, June 2026, July 2026 (4 Full Months)")
    print("   • Timeframes Tested: 15m, 30m, 1h, 2h, 4h")
    print("   • Market Realism: 0.10% Taker Fees + 0.08% Slippage (0.18% Total Friction)")
    print("   • Intrabar Granularity: 1-Minute Tick Stepping")
    print("   • Max Concurrent Positions: 10 | Risk per Trade: 1.0%")
    print("="*120, flush=True)

    timeframes = [
        ("15-Minute (15m)", 15),
        ("30-Minute (30m)", 30),
        ("1-Hour (1h)",     60),
        ("2-Hour (2h)",     120),
        ("4-Hour (4h)",     240),
    ]

    # Store results per timeframe
    tf_overall_results = []
    tf_monthly_breakdown = {}

    for tf_name, tf_min in timeframes:
        print(f"\n▶ PROCESSING TIMEFRAME: {tf_name}...", flush=True)
        t_tf0 = time.time()
        
        all_month_trades = []
        tf_monthly_breakdown[tf_name] = []
        
        for m_label, db_file in DATASETS:
            t_m0 = time.time()
            raw_m1 = load_month_data(db_file)
            if not raw_m1:
                print(f"   ⚠️ {m_label} data not found at {db_file}")
                continue
                
            m_trades = evaluate_trades_for_month(raw_m1, tf_min)
            # Apply concurrency for this month
            m_exec = apply_portfolio_concurrency(m_trades, max_concurrent=10)
            
            n_m = len(m_exec)
            net_r_m = sum(t["r_net"] for t in m_exec) if n_m > 0 else 0.0
            gross_r_m = sum(t["r_gross"] for t in m_exec) if n_m > 0 else 0.0
            fric_r_m = sum(t["friction_r"] for t in m_exec) if n_m > 0 else 0.0
            wr_m = (len([t for t in m_exec if t["is_win"]]) / n_m * 100.0) if n_m > 0 else 0.0
            
            tf_monthly_breakdown[tf_name].append({
                "month": m_label,
                "trades": n_m,
                "win_rate": f"{wr_m:.1f}%",
                "gross_r": f"{gross_r_m:+.1f}R",
                "friction_r": f"{fric_r_m:.1f}R",
                "net_r": f"{net_r_m:+.2f}R"
            })
            
            all_month_trades.extend(m_trades)
            print(f"   ✓ {m_label:<10} -> {n_m:>4} trades | WR: {wr_m:>4.1f}% | Gross: {gross_r_m:>+6.1f}R | Net: {net_r_m:>+6.2f}R ({time.time()-t_m0:.1f}s)", flush=True)
            
        # Global 4-Month Concurrency Execution
        cum_executed = apply_portfolio_concurrency(all_month_trades, max_concurrent=10)
        n_tot = len(cum_executed)
        
        if n_tot == 0:
            continue
            
        wins = [t for t in cum_executed if t["is_win"]]
        losses = [t for t in cum_executed if not t["is_win"]]
        
        win_rate = (len(wins) / n_tot) * 100.0
        gross_p = sum(t["r_gross"] for t in cum_executed if t["r_gross"] > 0)
        gross_l = abs(sum(t["r_gross"] for t in cum_executed if t["r_gross"] < 0)) if any(t["r_gross"] < 0 for t in cum_executed) else 0.001
        pf_gross = gross_p / gross_l
        
        net_p = sum(t["r_net"] for t in wins)
        net_l = abs(sum(t["r_net"] for t in losses)) if losses else 0.001
        pf_net = net_p / net_l
        
        gross_r = sum(t["r_gross"] for t in cum_executed)
        total_friction_r = sum(t["friction_r"] for t in cum_executed)
        net_r = sum(t["r_net"] for t in cum_executed)
        
        cum_r_series = np.cumsum([t["r_net"] for t in cum_executed])
        peak_r = np.maximum.accumulate(cum_r_series)
        max_dd = np.max(peak_r - cum_r_series) if len(cum_r_series) > 0 else 0.0
        
        avg_sl = np.mean([t["sl_pct"] for t in cum_executed])
        med_sl = np.median([t["sl_pct"] for t in cum_executed])
        avg_dur = np.mean([t["duration_hrs"] for t in cum_executed])
        exp_r = net_r / n_tot
        drag_pct = (total_friction_r / gross_r * 100.0) if gross_r > 0 else 999.0
        
        tf_overall_results.append({
            "tf": tf_name,
            "trades": n_tot,
            "avg_sl": f"{avg_sl:.2f}% (Med: {med_sl:.2f}%)",
            "win_rate": f"{win_rate:.1f}%",
            "gross_r": f"{gross_r:+.1f}R",
            "friction_r": f"{total_friction_r:.1f}R",
            "net_r": f"{net_r:+.2f}R",
            "pf_net": f"{pf_net:.2f}",
            "max_dd": f"{max_dd:.2f}R",
            "exp_r": f"{exp_r:+.3f}R",
            "duration": f"{avg_dur:.1f}h",
            "drag_pct": f"{drag_pct:.1f}%",
            "monthly_avg": f"{net_r / 4.0:+.1f}R / mo"
        })
        print(f"   ★ {tf_name} 4-MONTH TOTAL: {n_tot} trades | Net Return: {net_r:+.2f}R | Net PF: {pf_net:.2f} | Max DD: {max_dd:.2f}R (Finished in {time.time()-t_tf0:.1f}s)\n", flush=True)

    print("\n" + "="*125)
    print("📊 4-MONTH CUMULATIVE MULTI-TIMEFRAME PERFORMANCE TABLE (WITH 0.18% FEES + SLIPPAGE)")
    print("="*125)
    print(f"{'TIMEFRAME':<18} | {'TRADES':<7} | {'MEDIAN SL':<12} | {'WIN RATE':<9} | {'GROSS PNL':<10} | {'FRICTION':<9} | {'NET RETURN':<12} | {'NET PF':<7} | {'MAX DD':<8} | {'AVG DURATION'} | {'MONTHLY AVG'}")
    print("-" * 125)
    for r in tf_overall_results:
        print(f"{r['tf']:<18} | {r['trades']:<7} | {r['avg_sl'].split(' ')[-1]:<12} | {r['win_rate']:<9} | {r['gross_r']:<10} | {r['friction_r']:<9} | {r['net_r']:<12} | {r['pf_net']:<7} | {r['max_dd']:<8} | {r['duration']:<12} | {r['monthly_avg']}")
    print("="*125)

if __name__ == "__main__":
    main()
