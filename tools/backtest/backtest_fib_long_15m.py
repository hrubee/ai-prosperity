#!/usr/bin/env python3
"""tools/backtest/backtest_fib_long_15m.py — Fibonacci 1.3/1.6 Mean-Reversion Long Strategy Backtest (15m Timeframe / 1m Granularity).

Strategy Rules:
1. Signal Candle: 15m Green Candle (Close > Open) with Volume >= 10.0x SMA(20) baseline.
2. Fibonacci Mapping:
   - High = H, Low = L, Range Delta = H - L
   - Entry Level (1.3 Fib): Entry = H - 1.3 * Delta = L - 0.3 * Delta (Limit Buy on Retracement)
   - Stop Loss (1.6 Fib): SL = H - 1.6 * Delta = L - 0.6 * Delta
   - Take Profit (1:4 RR): TP = Entry + 4.0 * (Entry - SL) = Entry + 1.2 * Delta
3. Intrabar Stepping: Walk forward on 1-minute bars to check for Limit Buy Fill, then monitor TP/SL.
4. Friction: 0.10% exchange fees + 0.08% slippage (0.18% total roundtrip).
5. Concurrency: Max 10 concurrent active positions (1.0% account risk per trade).
6. Outputs: Summary table + 10 sample winners + 10 sample losers saved to CSV.
"""
import os
import sys
import sqlite3
import time
import json
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
OUTPUT_CSV = "tools/backtest/fib_long_15m_trades.csv"

def resample_bars(m1_rows, tf_min=15):
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

def evaluate_fib_longs_in_dataset(raw_m1, month_label):
    tf_min = 15
    tf_ms = tf_min * 60 * 1000
    trades = []
    
    for sym, m1_rows in raw_m1.items():
        bars_15m = resample_bars(m1_rows, tf_min=15)
        if len(bars_15m) < 25:
            continue
            
        m1_ts_list = np.array([r[0] for r in m1_rows])
        opens = np.array([b['open'] for b in bars_15m])
        highs = np.array([b['high'] for b in bars_15m])
        lows = np.array([b['low'] for b in bars_15m])
        closes = np.array([b['close'] for b in bars_15m])
        volumes = np.array([b['volume'] for b in bars_15m])
        timestamps = np.array([b['ts'] for b in bars_15m])
        
        i = 20
        while i < len(bars_15m):
            base_vol = np.mean(volumes[i-20:i])
            if base_vol <= 0:
                i += 1
                continue
                
            vol_mult = volumes[i] / base_vol
            is_green = closes[i] > opens[i]
            
            # 15m Green Candle with >= 10.0x Volume Spike
            if vol_mult >= 10.0 and is_green:
                spike_ts = timestamps[i]
                candle_close_ts = spike_ts + tf_ms
                
                H = highs[i]
                L = lows[i]
                delta = H - L
                if delta <= 0:
                    i += 1
                    continue
                    
                # Fibonacci Levels
                entry_px = L - (0.3 * delta) # 1.3 Fib Level
                sl_px = L - (0.6 * delta)    # 1.6 Fib Level
                risk_dist = entry_px - sl_px # 0.3 * delta
                tp_px = entry_px + (4.0 * risk_dist) # 1:4 RR Target = entry + 1.2 * delta
                
                sl_pct = (risk_dist / entry_px)
                if sl_pct <= 0:
                    i += 1
                    continue
                    
                friction_in_r = FRICTION_PCT / sl_pct
                
                # Walk 1-minute bars forward from 15m candle close
                start_m1_idx = np.searchsorted(m1_ts_list, candle_close_ts)
                if start_m1_idx >= len(m1_rows):
                    i += 1
                    continue
                    
                # Phase 1: Wait for Limit Buy Order Fill (Order valid for up to 24h = 1440 1m bars)
                filled = False
                fill_idx = None
                fill_ts = None
                
                for w_i in range(start_m1_idx, min(len(m1_rows), start_m1_idx + 1440)):
                    m_ts, m_o, m_h, m_l, m_c, m_v = m1_rows[w_i]
                    if m_l <= entry_px:
                        filled = True
                        fill_idx = w_i
                        fill_ts = m_ts
                        break
                        
                if not filled:
                    # Order expired unfilled
                    i += 1
                    continue
                    
                # Phase 2: Position is Filled -> Monitor TP and SL on subsequent 1-minute bars
                exit_px = None
                exit_ts = None
                r_gross = None
                outcome = None
                
                # Track for up to 48 hours (2880 mins) post-fill
                for p_i in range(fill_idx, min(len(m1_rows), fill_idx + 2880)):
                    m_ts, m_o, m_h, m_l, m_c, m_v = m1_rows[p_i]
                    
                    # For a LONG position:
                    # SL hit when Low <= SL
                    if m_l <= sl_px:
                        exit_px = sl_px
                        exit_ts = m_ts
                        r_gross = -1.0
                        outcome = "STOP LOSS"
                        break
                        
                    # TP hit when High >= TP
                    if m_h >= tp_px:
                        exit_px = tp_px
                        exit_ts = m_ts
                        r_gross = +4.0
                        outcome = "TAKE PROFIT (1:4)"
                        break
                        
                if exit_px is None:
                    last_idx = min(len(m1_rows) - 1, fill_idx + 2879)
                    exit_px = m1_rows[last_idx][4]
                    exit_ts = m1_rows[last_idx][0]
                    r_gross = (exit_px - entry_px) / risk_dist
                    outcome = "TIMEOUT"
                    
                r_net = r_gross - friction_in_r
                dur_hrs = (exit_ts - fill_ts) / (1000 * 3600.0)
                wait_fill_hrs = (fill_ts - candle_close_ts) / (1000 * 3600.0)
                
                spike_dt_str = datetime.fromtimestamp(spike_ts/1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                entry_dt_str = datetime.fromtimestamp(fill_ts/1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                exit_dt_str = datetime.fromtimestamp(exit_ts/1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                
                trades.append({
                    "month": month_label,
                    "symbol": sym,
                    "spike_time": spike_dt_str,
                    "entry_time": entry_dt_str,
                    "exit_time": exit_dt_str,
                    "spike_ts": spike_ts,
                    "entry_ts": fill_ts,
                    "exit_ts": exit_ts,
                    "vol_mult": float(vol_mult),
                    "candle_high": float(H),
                    "candle_low": float(L),
                    "entry_px": float(entry_px),
                    "sl_px": float(sl_px),
                    "tp_px": float(tp_px),
                    "exit_px": float(exit_px),
                    "sl_pct": float(sl_pct * 100.0),
                    "tp_pct": float(((tp_px - entry_px) / entry_px) * 100.0),
                    "r_gross": float(r_gross),
                    "r_net": float(r_net),
                    "friction_r": float(friction_in_r),
                    "is_win": bool(r_net > 0),
                    "outcome": outcome,
                    "duration_hrs": float(dur_hrs),
                    "wait_fill_hrs": float(wait_fill_hrs)
                })
                
                i += 1
            else:
                i += 1
                
    return trades

def run_fib_strategy_backtest():
    print("="*120)
    print("🚀 FIBONACCI 1.3 / 1.6 MEAN-REVERSION LONG STRATEGY BACKTEST (15M TIMEFRAME / 1M GRANULARITY)")
    print("   • Signal: 15m Green Expansion Candle (Close > Open) with Volume >= 10.0x Baseline")
    print("   • Entry: Limit Buy at 1.3 Fib Extension below Spike Candle (Low - 0.3 * Range)")
    print("   • Stop Loss: 1.6 Fib Extension below Spike Candle (Low - 0.6 * Range)")
    print("   • Take Profit: 1:4 Risk-to-Reward Ratio (Entry + 1.2 * Range)")
    print("   • Intrabar Execution: 1-Minute Tick Stepping on 58.1 Million Candles (April–July 2026)")
    print("   • Market Realism: 0.10% Taker Fees + 0.08% Slippage (0.18% Total Friction)")
    print("   • Portfolio Management: Max 10 Concurrent Trades | 1.0% Account Risk per Trade")
    print("="*120, flush=True)

    all_raw_trades = []
    monthly_stats = []

    for month_label, db_path in DATASETS:
        t0 = time.time()
        print(f"\n▶ Loading & Evaluating {month_label} ({db_path})...", flush=True)
        raw_m1 = load_all_dataset_rows(db_path)
        if not raw_m1:
            print(f"   ⚠️ Could not load {db_path}")
            continue
            
        m_trades = evaluate_fib_longs_in_dataset(raw_m1, month_label)
        all_raw_trades.extend(m_trades)
        
        # Concurrency check for this month
        m_trades.sort(key=lambda x: x["entry_ts"])
        exec_m = []
        act_m = {}
        for t in m_trades:
            e_ts = t["entry_ts"]
            for a_s in list(act_m.keys()):
                if act_m[a_s] <= e_ts: del act_m[a_s]
            if t["symbol"] in act_m: continue
            if len(act_m) >= 10: continue
            exec_m.append(t)
            act_m[t["symbol"]] = t["exit_ts"]
            
        n_m = len(exec_m)
        wins_m = [t for t in exec_m if t["is_win"]]
        wr_m = (len(wins_m) / n_m * 100.0) if n_m > 0 else 0.0
        gross_r_m = sum(t["r_gross"] for t in exec_m) if n_m > 0 else 0.0
        net_r_m = sum(t["r_net"] for t in exec_m) if n_m > 0 else 0.0
        fric_r_m = sum(t["friction_r"] for t in exec_m) if n_m > 0 else 0.0
        
        monthly_stats.append({
            "month": month_label,
            "trades": n_m,
            "win_rate": f"{wr_m:.1f}%",
            "gross_r": f"{gross_r_m:+.1f}R",
            "friction_r": f"{fric_r_m:.1f}R",
            "net_r": f"{net_r_m:+.2f}R"
        })
        print(f"   ✓ {month_label} Evaluated: {len(m_trades)} filled setups -> {n_m} executed (WR: {wr_m:.1f}%, Net: {net_r_m:+.2f}R) in {time.time()-t0:.1f}s", flush=True)

    # Global 4-Month Concurrency Execution
    all_raw_trades.sort(key=lambda x: x["entry_ts"])
    executed_trades = []
    active_positions = {}
    
    for t in all_raw_trades:
        e_ts = t["entry_ts"]
        for a_sym in list(active_positions.keys()):
            if active_positions[a_sym] <= e_ts:
                del active_positions[a_sym]
        if t["symbol"] in active_positions:
            continue
        if len(active_positions) >= 10:
            continue
        executed_trades.append(t)
        active_positions[t["symbol"]] = t["exit_ts"]
        
    n_total = len(executed_trades)
    if n_total == 0:
        print("No trades executed.")
        return

    # Save all executed trades to CSV
    df_trades = pd.DataFrame(executed_trades)
    df_trades.to_csv(OUTPUT_CSV, index=False)
    print(f"\n💾 Saved all {n_total} executed trade records to `{OUTPUT_CSV}`")

    # Metrics Calculation
    wins = [t for t in executed_trades if t["is_win"]]
    losses = [t for t in executed_trades if not t["is_win"]]
    tp_hits = [t for t in executed_trades if t["outcome"] == "TAKE PROFIT (1:4)"]
    sl_hits = [t for t in executed_trades if t["outcome"] == "STOP LOSS"]
    
    win_rate = (len(wins) / n_total) * 100.0
    gross_p = sum(t["r_gross"] for t in executed_trades if t["r_gross"] > 0)
    gross_l = abs(sum(t["r_gross"] for t in executed_trades if t["r_gross"] < 0)) if any(t["r_gross"] < 0 for t in executed_trades) else 0.001
    pf_gross = gross_p / gross_l
    
    net_p = sum(t["r_net"] for t in wins)
    net_l = abs(sum(t["r_net"] for t in losses)) if losses else 0.001
    pf_net = net_p / net_l
    
    gross_r = sum(t["r_gross"] for t in executed_trades)
    total_friction_r = sum(t["friction_r"] for t in executed_trades)
    net_r = sum(t["r_net"] for t in executed_trades)
    
    cum_r = np.cumsum([t["r_net"] for t in executed_trades])
    peak_r = np.maximum.accumulate(cum_r)
    max_dd = np.max(peak_r - cum_r) if len(cum_r) > 0 else 0.0
    
    avg_sl = np.mean([t["sl_pct"] for t in executed_trades])
    med_sl = np.median([t["sl_pct"] for t in executed_trades])
    avg_dur = np.mean([t["duration_hrs"] for t in executed_trades])
    avg_wait = np.mean([t["wait_fill_hrs"] for t in executed_trades])
    exp_r = net_r / n_total

    print("\n" + "="*100)
    print("📊 4-MONTH EXECUTIVE PERFORMANCE SUMMARY — FIBONACCI 1.3/1.6 MEAN-REVERSION LONG")
    print("="*100)
    print(f"• Total Executed Trades (10 Max Cap):  {n_total:,}")
    print(f"• Take Profit Hits (1:4 RR, +4.0R):     {len(tp_hits):,} ({len(tp_hits)/n_total*100:.1f}%)")
    print(f"• Stop Loss Hits (1.6 Fib, -1.0R):     {len(sl_hits):,} ({len(sl_hits)/n_total*100:.1f}%)")
    print(f"• Win Rate:                            {win_rate:.2f}%")
    print(f"• Gross Profit Factor:                 {pf_gross:.2f}")
    print(f"• NET Profit Factor (After Friction):  {pf_net:.2f}")
    print(f"• Gross Realized Return:               {gross_r:+.2f}R")
    print(f"• Total Friction (Fees + Slippage):    -{total_friction_r:.2f}R")
    print(f"• NET REALIZED RETURN (4 Months):      {net_r:+.2f}R ({net_r/4.0:+.2f}R / month avg)")
    print(f"• Max Drawdown:                        {max_dd:.2f}R")
    print(f"• Expectancy per Trade:                {exp_r:+.3f}R / trade")
    print(f"• Median Stop Loss Distance:           {med_sl:.2f}% (Mean: {avg_sl:.2f}%)")
    print(f"• Average Order Fill Wait Time:        {avg_wait:.1f} hours")
    print(f"• Average Trade Hold Duration:         {avg_dur:.1f} hours")
    print("="*100)

    # 10 Sample Winning Trades
    print("\n" + "="*120)
    print("🏆 10 SAMPLE WINNING TRADES (1:4 RR HIT):")
    print("="*120)
    sample_wins = sorted(wins, key=lambda x: x["r_net"], reverse=True)[:10]
    print(f"{'SYMBOL':<10} | {'ENTRY TIME (UTC)':<17} | {'ENTRY':<10} | {'SL (1.6 FIB)':<12} | {'TP (1:4)':<10} | {'EXIT PRICE':<10} | {'NET R':<8} | {'DURATION'}")
    print("-" * 120)
    for w in sample_wins:
        print(f"#{w['symbol']:<9} | {w['entry_time']:<17} | {w['entry_px']:>10.4f} | {w['sl_px']:>12.4f} | {w['tp_px']:>10.4f} | {w['exit_px']:>10.4f} | {w['r_net']:>+6.2f}R | {w['duration_hrs']:>5.1f} hrs")

    # 10 Sample Losing Trades
    print("\n" + "="*120)
    print("🛑 10 SAMPLE LOSING TRADES (STOP LOSS HIT):")
    print("="*120)
    sample_losses = sorted(losses, key=lambda x: x["r_net"])[:10]
    print(f"{'SYMBOL':<10} | {'ENTRY TIME (UTC)':<17} | {'ENTRY':<10} | {'SL (1.6 FIB)':<12} | {'TP (1:4)':<10} | {'EXIT PRICE':<10} | {'NET R':<8} | {'DURATION'}")
    print("-" * 120)
    for l in sample_losses:
        print(f"#{l['symbol']:<9} | {l['entry_time']:<17} | {l['entry_px']:>10.4f} | {l['sl_px']:>12.4f} | {l['tp_px']:>10.4f} | {l['exit_px']:>10.4f} | {l['r_net']:>+6.2f}R | {l['duration_hrs']:>5.1f} hrs")
    print("="*120)

if __name__ == "__main__":
    run_fib_strategy_backtest()
