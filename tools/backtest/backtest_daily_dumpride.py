#!/usr/bin/env python3
"""tools/backtest/backtest_daily_dumpride.py — 4-Month Daily (1d) Timeframe DumpRide Backtest.

Dataset: April, May, June, July 2026 (58.1 Million 1-Minute Bars across 330+ coins)
Timeframe: 1d (Daily Candles = 1440 minutes = 86,400,000 ms)
Signal Conditions:
- Daily Green Candle (Close > Open)
- Volume Spike >= Multiplier (Testing 3.0x, 5.0x, 7.5x, 10.0x)
- Stop Loss: 1.0x ATR(14)
- Take Profit: 1:2 RR (2.0x ATR) and 1:3 RR
- Realism: 0.10% Taker Fees + 0.08% Slippage (0.18% Friction)
- Intrabar Stepping: 1-Minute Granularity
- Max 10 Concurrent Positions | 1.0% Risk per Trade
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

def resample_bars(m1_rows, tf_min=1440):
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

def load_all_dataset_rows(db_path):
    if not os.path.exists(db_path): return {}
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

def evaluate_daily_signals(raw_m1_all_months, vol_threshold=5.0, rr_target=2.0):
    tf_min = 1440
    tf_ms = tf_min * 60 * 1000
    trades = []
    
    for sym, m1_rows in raw_m1_all_months.items():
        bars_1d = resample_bars(m1_rows, tf_min=1440)
        if len(bars_1d) < 15:
            continue
            
        m1_ts_list = np.array([r[0] for r in m1_rows])
        opens = np.array([b['open'] for b in bars_1d])
        highs = np.array([b['high'] for b in bars_1d])
        lows = np.array([b['low'] for b in bars_1d])
        closes = np.array([b['close'] for b in bars_1d])
        volumes = np.array([b['volume'] for b in bars_1d])
        timestamps = np.array([b['ts'] for b in bars_1d])
        
        i = 10
        while i < len(bars_1d):
            base_vol = np.mean(volumes[max(0, i-10):i])
            if base_vol <= 0:
                i += 1
                continue
                
            vol_mult = volumes[i] / base_vol
            is_green = closes[i] > opens[i]
            
            if vol_mult >= vol_threshold and is_green:
                entry_ts = timestamps[i] + tf_ms
                entry_px = closes[i]
                
                # ATR(14) or shorter
                trs = []
                for k in range(max(1, i-13), i+1):
                    tr = max(highs[k] - lows[k], abs(highs[k] - closes[k-1]), abs(lows[k] - closes[k-1]))
                    trs.append(tr)
                atr14 = float(np.mean(trs)) if trs else entry_px * 0.05
                
                risk_dist = 1.0 * atr14
                sl_px = entry_px + risk_dist
                tp_px = entry_px - (rr_target * risk_dist)
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
                
                # Walk forward up to 30 days (43,200 1m bars)
                for m_idx in range(sub_idx, min(len(m1_rows), sub_idx + 43200)):
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
                        r_gross = +rr_target
                        outcome = "TAKE PROFIT"
                        break
                        
                if exit_px is None:
                    last_i = min(len(m1_rows)-1, sub_idx + 43199)
                    exit_px = m1_rows[last_i][4]
                    exit_ts = m1_rows[last_i][0]
                    r_gross = (entry_px - exit_px) / risk_dist
                    outcome = "TIMEOUT"
                    
                r_net = r_gross - friction_in_r
                dur_days = (exit_ts - entry_ts) / (1000 * 3600.0 * 24.0)
                
                entry_dt = datetime.fromtimestamp(entry_ts/1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                exit_dt = datetime.fromtimestamp(exit_ts/1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                
                trades.append({
                    "symbol": sym,
                    "entry_time": entry_dt,
                    "exit_time": exit_dt,
                    "entry_ts": entry_ts,
                    "exit_ts": exit_ts,
                    "vol_mult": float(vol_mult),
                    "entry_px": float(entry_px),
                    "sl_px": float(sl_px),
                    "tp_px": float(tp_px),
                    "exit_px": float(exit_px),
                    "sl_pct": float(sl_pct * 100.0),
                    "r_gross": float(r_gross),
                    "r_net": float(r_net),
                    "friction_r": float(friction_in_r),
                    "is_win": bool(r_net > 0),
                    "outcome": outcome,
                    "duration_days": float(dur_days)
                })
                
                i += 1
            else:
                i += 1
                
    return trades

def apply_portfolio_concurrency(trades, max_concurrent=10):
    trades.sort(key=lambda x: x["entry_ts"])
    executed = []
    active = {}
    for t in trades:
        e_ts = t["entry_ts"]
        for a_s in list(active.keys()):
            if active[a_s] <= e_ts: del active[a_s]
        if t["symbol"] in active: continue
        if len(active) >= max_concurrent: continue
        executed.append(t)
        active[t["symbol"]] = t["exit_ts"]
    return executed

def main():
    print("="*120)
    print("🚀 4-MONTH DAILY (1D) TIMEFRAME DUMPRIDE REALISTIC BACKTEST (58.1M 1-MINUTE BARS)")
    print("   • Span: April 1, 2026 – July 31, 2026 (4 Continuous Months across 330+ coins)")
    print("   • Timeframe: 1d (Daily Candles = 1440 Minutes)")
    print("   • Realism: 0.10% Exchange Taker Fees + 0.08% Slippage (0.18% Friction)")
    print("   • Intrabar Execution: 1-Minute Tick Stepping")
    print("   • Portfolio Cap: Max 10 Concurrent Positions | 1.0% Risk per Trade")
    print("="*120, flush=True)

    # 1. Merge continuous 1m data across all 4 months per symbol
    print("\n▶ Stitching continuous 4-month 1-minute datasets...", flush=True)
    full_m1_universe = {}
    
    for m_label, db_path in DATASETS:
        t0 = time.time()
        m_data = load_all_dataset_rows(db_path)
        for sym, rows in m_data.items():
            if sym not in full_m1_universe:
                full_m1_universe[sym] = []
            full_m1_universe[sym].extend(rows)
        print(f"   ✓ Loaded {m_label} ({len(m_data)} pairs) in {time.time()-t0:.1f}s", flush=True)
        
    # Sort all stitched series by timestamp
    for sym in full_m1_universe:
        full_m1_universe[sym].sort(key=lambda x: x[0])
        
    print(f"\n✅ Universe Stitched: {len(full_m1_universe)} perpetual symbols across 4 months.\n")

    # Evaluate different Volume Thresholds and RR Ratios on Daily
    scenarios = [
        ("Daily 10.0x Spike (1:2 RR)", 10.0, 2.0),
        ("Daily 7.5x Spike (1:2 RR)",   7.5, 2.0),
        ("Daily 5.0x Spike (1:2 RR)",   5.0, 2.0),
        ("Daily 3.0x Spike (1:2 RR)",   3.0, 2.0),
        ("Daily 5.0x Spike (1:3 RR)",   5.0, 3.0),
    ]

    summary_rows = []

    for name, vol_thresh, rr in scenarios:
        t0 = time.time()
        raw_trades = evaluate_daily_signals(full_m1_universe, vol_threshold=vol_thresh, rr_target=rr)
        executed = apply_portfolio_concurrency(raw_trades, max_concurrent=10)
        
        n_tot = len(executed)
        if n_tot == 0:
            summary_rows.append({
                "scenario": name, "trades": 0, "win_rate": "0.0%", "gross_r": "0.0R",
                "friction_r": "0.0R", "net_r": "0.0R", "pf_net": "0.00", "max_dd": "0.0R", "med_sl": "0.0%", "dur": "0.0d"
            })
            continue
            
        wins = [t for t in executed if t["is_win"]]
        losses = [t for t in executed if not t["is_win"]]
        win_rate = (len(wins) / n_tot) * 100.0
        
        gross_p = sum(t["r_gross"] for t in executed if t["r_gross"] > 0)
        gross_l = abs(sum(t["r_gross"] for t in executed if t["r_gross"] < 0)) if any(t["r_gross"] < 0 for t in executed) else 0.001
        pf_gross = gross_p / gross_l
        
        net_p = sum(t["r_net"] for t in wins)
        net_l = abs(sum(t["r_net"] for t in losses)) if losses else 0.001
        pf_net = net_p / net_l
        
        gross_r = sum(t["r_gross"] for t in executed)
        total_friction_r = sum(t["friction_r"] for t in executed)
        net_r = sum(t["r_net"] for t in executed)
        
        cum_r = np.cumsum([t["r_net"] for t in executed])
        peak_r = np.maximum.accumulate(cum_r)
        max_dd = np.max(peak_r - cum_r) if len(cum_r) > 0 else 0.0
        
        avg_sl = np.mean([t["sl_pct"] for t in executed])
        med_sl = np.median([t["sl_pct"] for t in executed])
        avg_dur = np.mean([t["duration_days"] for t in executed])
        exp_r = net_r / n_tot
        
        summary_rows.append({
            "scenario": name,
            "trades": n_tot,
            "win_rate": f"{win_rate:.1f}%",
            "gross_r": f"{gross_r:+.1f}R",
            "friction_r": f"{total_friction_r:.1f}R",
            "net_r": f"{net_r:+.2f}R",
            "pf_net": f"{pf_net:.2f}",
            "max_dd": f"{max_dd:.2f}R",
            "med_sl": f"{med_sl:.2f}%",
            "dur": f"{avg_dur:.1f} days",
            "monthly_avg": f"{net_r/4.0:+.2f}R / mo"
        })
        print(f"   ★ {name:<28} -> {n_tot:>3} trades | WR: {win_rate:>4.1f}% | Gross: {gross_r:>+6.1f}R | Net: {net_r:>+6.2f}R | PF: {pf_net:>4.2f} ({time.time()-t0:.1f}s)", flush=True)

    print("\n" + "="*130)
    print("📊 4-MONTH DAILY (1D) TIMEFRAME PERFORMANCE MATRIX (WITH 0.18% FEES + SLIPPAGE)")
    print("="*130)
    print(f"{'STRATEGY / SCENARIO':<30} | {'TRADES':<7} | {'MEDIAN SL':<10} | {'WIN RATE':<9} | {'GROSS PNL':<10} | {'FRICTION':<9} | {'NET RETURN':<12} | {'NET PF':<7} | {'MAX DD':<8} | {'AVG DURATION'} | {'MONTHLY AVG'}")
    print("-" * 130)
    for r in summary_rows:
        print(f"{r['scenario']:<30} | {r['trades']:<7} | {r['med_sl']:<10} | {r['win_rate']:<9} | {r['gross_r']:<10} | {r['friction_r']:<9} | {r['net_r']:<12} | {r['pf_net']:<7} | {r['max_dd']:<8} | {r['dur']:<12} | {r['monthly_avg']}")
    print("="*130)

if __name__ == "__main__":
    main()
