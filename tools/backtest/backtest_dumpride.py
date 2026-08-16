#!/usr/bin/env python3
"""tools/backtest/backtest_dumpride.py — Complete Backtest Engine for the "DumpRide" Strategy.

"DumpRide" Strategy:
1. Setup: Detects massive institutional volume pump (>=10x SMA40 volume).
2. Peak Tracking: Records pump peak high and pump origin low.
3. Rollover Entry: Enters SHORT on the first red confirmation candle below 9 EMA.
4. Stop Loss: Set at pump peak high (structural top).
5. Target / Exit: Evaluates multiple exit models (70% Retrace, 100% Retrace to Origin, 1:2 RR, 1:3 RR, Activated ATR Trail).

Backtests on both 15m and 4h timeframes using 1-minute intrabar execution across datasets/june_2026_1m.db.
"""
import os
import sys
import sqlite3
import time
import numpy as np
import pandas as pd

DB_PATH = "datasets/june_2026_1m.db"
if not os.path.exists(DB_PATH):
    DB_PATH = "/root/data/june_2026_1m.db"

print(f"Loading 1-minute dataset from {DB_PATH}...")
t0 = time.time()
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]

raw_candles = {}
total_1m_bars = 0
for sym in symbols:
    rows = cursor.execute(
        "SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC",
        (sym,)
    ).fetchall()
    if len(rows) >= 500:
        raw_candles[sym] = rows
        total_1m_bars += len(rows)
conn.close()

print(f"Loaded {len(raw_candles)} active symbols ({total_1m_bars:,} 1m bars) in {time.time()-t0:.2f}s.\n")

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

def run_dumpride_simulation(tf_name, tf_min, exit_model="70_retrace", spike_vol_mult=10.0, pump_min_pct=3.0):
    all_trades = []
    
    for sym, m1_rows in raw_candles.items():
        tf_bars = resample_bars(m1_rows, tf_min)
        n = len(tf_bars)
        if n < 45:
            continue
            
        opens = np.array([b['open'] for b in tf_bars])
        highs = np.array([b['high'] for b in tf_bars])
        lows = np.array([b['low'] for b in tf_bars])
        closes = np.array([b['close'] for b in tf_bars])
        vols = np.array([b['volume'] for b in tf_bars])
        times = [b['ts'] for b in tf_bars]
        
        # Precompute 9 EMA
        k_ema = 2.0 / (9 + 1)
        ema = np.zeros(n)
        ema[0] = closes[0]
        for i in range(1, n):
            ema[i] = closes[i] * k_ema + ema[i - 1] * (1.0 - k_ema)
            
        # 14 ATR
        tr = np.zeros(n)
        tr[0] = highs[0] - lows[0]
        for i in range(1, n):
            tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        atr14 = pd.Series(tr).rolling(14).mean().fillna(highs[0] - lows[0]).values
        
        m1_times = [r[0] for r in m1_rows]
        watching = None
        
        for i in range(40, n):
            cur_t = times[i]
            cur_o, cur_h, cur_l, cur_c, cur_v = opens[i], highs[i], lows[i], closes[i], vols[i]
            is_green = cur_c >= cur_o
            is_red = cur_c < cur_o
            
            baseline_vol = np.mean(vols[i - 40 : i])
            vol_mult = cur_v / baseline_vol if baseline_vol > 0 else 0
            
            if watching is not None:
                # Update highest peak reached
                if cur_h > watching['pump_peak_px']:
                    watching['pump_peak_px'] = cur_h
                    
                # Rollover Entry Confirmation: Red close below 9 EMA
                if is_red and cur_c < ema[i]:
                    entry_px = cur_c
                    sl_px = watching['pump_peak_px']
                    risk = sl_px - entry_px
                    cur_atr = atr14[i]
                    
                    # Ensure risk distance is realistic (>= 0.5% to avoid exchange micro-spread traps)
                    if (risk / entry_px) >= 0.005:
                        pump_start = watching['pump_start_px']
                        
                        # Determine TP Target based on Exit Model
                        if exit_model == "70_retrace":
                            tp_px = sl_px - 0.70 * (sl_px - pump_start)
                            if tp_px >= entry_px * 0.998:
                                tp_px = entry_px - 2.0 * risk
                        elif exit_model == "100_retrace": # Dump to Origin
                            tp_px = pump_start
                            if tp_px >= entry_px * 0.998:
                                tp_px = entry_px - 2.0 * risk
                        elif exit_model == "fixed_1_2":
                            tp_px = entry_px - 2.0 * risk
                        elif exit_model == "fixed_1_3":
                            tp_px = entry_px - 3.0 * risk
                        elif exit_model == "atr_trail":
                            tp_px = 0.0 # pure trailing SL
                            
                        # 1-minute intrabar execution
                        trigger_close_t = cur_t + tf_min * 60 * 1000
                        m1_start_idx = -1
                        for idx in range(len(m1_times)):
                            if m1_times[idx] >= trigger_close_t:
                                m1_start_idx = idx
                                break
                                
                        if m1_start_idx != -1:
                            exit_r = None
                            exit_t = None
                            exit_reason = None
                            exit_px = None
                            trough_px = entry_px
                            cur_sl = sl_px
                            
                            for m1_idx in range(m1_start_idx, len(m1_rows)):
                                m1_t, m1_o, m1_h, m1_l, m1_c, m1_v = m1_rows[m1_idx]
                                
                                # ATR Trailing update for shorts
                                if exit_model == "atr_trail":
                                    if m1_l < trough_px:
                                        trough_px = m1_l
                                        profit_dist = entry_px - trough_px
                                        if profit_dist >= 1.5 * cur_atr:
                                            new_sl = trough_px + 1.0 * cur_atr
                                            if new_sl < cur_sl:
                                                cur_sl = new_sl
                                                
                                hit_sl = (m1_h >= cur_sl)
                                hit_tp = (m1_l <= tp_px) if tp_px > 0 else False
                                
                                if hit_sl and hit_tp:
                                    exit_r = -1.0 if cur_sl == sl_px else ((entry_px - cur_sl) / risk)
                                    exit_px = cur_sl
                                    exit_t = m1_t
                                    exit_reason = "SL"
                                    break
                                elif hit_sl:
                                    exit_r = -1.0 if cur_sl == sl_px else ((entry_px - cur_sl) / risk)
                                    exit_px = cur_sl
                                    exit_t = m1_t
                                    exit_reason = "SL"
                                    break
                                elif hit_tp:
                                    exit_r = (entry_px - tp_px) / risk
                                    exit_px = tp_px
                                    exit_t = m1_t
                                    exit_reason = "TP"
                                    break
                                    
                            if exit_r is None and m1_start_idx < len(m1_rows):
                                last_c = m1_rows[-1][4]
                                exit_r = (entry_px - last_c) / risk
                                exit_px = last_c
                                exit_t = m1_rows[-1][0]
                                exit_reason = "EOM"
                                
                            if exit_r is not None:
                                all_trades.append({
                                    "symbol": sym,
                                    "tf": tf_name,
                                    "model": exit_model,
                                    "entry_t": trigger_close_t,
                                    "exit_t": exit_t,
                                    "entry_px": entry_px,
                                    "sl_px": sl_px,
                                    "tp_px": tp_px,
                                    "exit_px": exit_px,
                                    "risk": risk,
                                    "r_multiple": exit_r,
                                    "is_win": exit_r > 0,
                                    "exit_reason": exit_reason
                                })
                        watching = None
                        continue
                        
                # If price drops below pump start before entry, setup invalid
                if cur_c < watching['pump_start_px']:
                    watching = None
                    continue
                    
            # Check for new volume spike pump
            if is_green and vol_mult >= spike_vol_mult:
                pump_pct = (cur_c - cur_o) / cur_o * 100 if cur_o > 0 else 0
                if pump_pct >= pump_min_pct:
                    watching = {
                        "symbol": sym,
                        "spike_t": cur_t,
                        "spike_avg_vol": baseline_vol,
                        "pump_start_px": cur_l,
                        "pump_peak_px": cur_h
                    }
                    
    return all_trades

print("=" * 86)
print("🚀 \"DUMPRIDE\" EXHAUSTION SHORT STRATEGY: 15M VS 4H COMPREHENSIVE BACKTEST")
print("=" * 86)

models = [
    ("70_retrace", "70% Retracement Target"),
    ("100_retrace", "100% Dump to Origin Target"),
    ("fixed_1_2", "Fixed 1:2 Risk-Reward"),
    ("fixed_1_3", "Fixed 1:3 Risk-Reward"),
    ("atr_trail", "Activated ATR Trailing Stop")
]

results = []

for tf_name, tf_min, pump_min in [("15m", 15, 3.0), ("4h", 240, 5.0)]:
    for model_id, model_name in models:
        t_start = time.time()
        trades = run_dumpride_simulation(tf_name, tf_min, exit_model=model_id, pump_min_pct=pump_min)
        dur = time.time() - t_start
        
        n_trades = len(trades)
        if n_trades == 0:
            continue
            
        wins = [t for t in trades if t['is_win']]
        losses = [t for t in trades if not t['is_win']]
        wr = len(wins) / n_trades * 100.0
        tot_r = sum(t['r_multiple'] for t in trades)
        gross_win = sum(t['r_multiple'] for t in wins)
        gross_loss = abs(sum(t['r_multiple'] for t in losses))
        pf = (gross_win / gross_loss) if gross_loss > 0 else 999.0
        avg_win = (gross_win / len(wins)) if wins else 0.0
        avg_loss = (gross_loss / len(losses)) if losses else 0.0
        expectancy = tot_r / n_trades
        
        # Chronological $100 account simulation
        trades.sort(key=lambda x: x['entry_t'])
        bal = 100.0
        peak = 100.0
        max_dd = 0.0
        for t in trades:
            r_usd = bal * 0.01
            pnl = t['r_multiple'] * r_usd
            bal += pnl
            if bal > peak: peak = bal
            dd = peak - bal
            if dd > max_dd: max_dd = dd
        dd_pct = (max_dd / peak) * 100 if peak > 0 else 0
        roi = ((bal - 100.0) / 100.0) * 100.0
        
        res = {
            "tf": tf_name,
            "model_id": model_id,
            "model_name": model_name,
            "trades": n_trades,
            "wr": wr,
            "tot_r": tot_r,
            "pf": pf,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "expectancy": expectancy,
            "final_bal": bal,
            "roi": roi,
            "max_dd_pct": dd_pct,
            "dur": dur
        }
        results.append(res)
        
        print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"📌 TIMEFRAME: {tf_name.upper()} | EXIT: {model_name} ({dur:.1f}s)")
        print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"  • Total Trades Executed : {n_trades}")
        print(f"  • Win Rate              : {wr:.1f}% ({len(wins)} Wins / {len(losses)} Losses)")
        print(f"  • Total Return (R)      : {tot_r:+.2f} R")
        print(f"  • Profit Factor         : {pf:.2f}")
        print(f"  • Avg Win / Avg Loss    : +{avg_win:.2f} R / -{avg_loss:.2f} R (Expectancy: {expectancy:+.2f} R/trade)")
        print(f"  • $100 Account (1% Risk): ${bal:.2f} ({roi:+.2f}% ROI) | Max DD: {dd_pct:.1f}%\n")

print("=" * 86)
print("🏆 \"DUMPRIDE\" MASTER COMPARISON TABLE (15M VS 4H)")
print("=" * 86)
print(f"{'TF':<5} | {'Exit Model':<28} | {'Trades':<7} | {'Win Rate':<9} | {'PF':<6} | {'Total R':<10} | {'$100 ROI':<10} | {'Max DD %':<8}")
print("-" * 86)
for r in sorted(results, key=lambda x: x['tot_r'], reverse=True):
    print(f"{r['tf']:<5} | {r['model_name']:<28} | {r['trades']:<7} | {r['wr']:>7.1f}% | {r['pf']:>6.2f} | {r['tot_r']:>+8.2f} R | {r['roi']:>+8.2f}% | {r['max_dd_pct']:>7.1f}%")
print("=" * 86)
