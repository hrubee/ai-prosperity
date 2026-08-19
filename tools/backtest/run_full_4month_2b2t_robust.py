import os
import sys
import sqlite3
import time
import numpy as np
import pandas as pd
from concurrent.futures import ProcessPoolExecutor

print("=========================================================================================", flush=True)
print("🧪 MULTI-MONTH ROBUST 2B2T & DUMPRIDE STUDY (58.1 MILLION 1-MIN BARS / 330+ PAIRS)", flush=True)
print("=========================================================================================", flush=True)

DATASETS = [
    ("April 2026", "datasets/april_2026_1m.db"),
    ("May 2026",   "datasets/may_2026_1m.db"),
    ("June 2026",  "datasets/june_2026_1m.db"),
    ("July 2026",  "datasets/july_2026_1m.db"),
]

FRICTION_R = 0.05 # Round-trip taker fee + slippage deduction in R units

def process_symbol_candles(args):
    sym, df_raw = args
    if len(df_raw) < 1000:
        return [], []
        
    df_raw = df_raw.sort_values("timestamp")
    m1_ts = df_raw["timestamp"].values
    m1_opens = df_raw["open"].values
    m1_highs = df_raw["high"].values
    m1_lows = df_raw["low"].values
    m1_closes = df_raw["close"].values
    m1_vols = df_raw["volume"].values
    n_1m = len(df_raw)
    
    # ── 1. DUMPRIDE 1H SHORT ──────────────────────────────────────────
    tf_ms_1h = 3600 * 1000
    b_1h_ts = (m1_ts // tf_ms_1h) * tf_ms_1h
    
    # Resample to 1H
    df_1h = df_raw.groupby(b_1h_ts).agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).reset_index().rename(columns={'index': 'ts'})
    
    n_1h = len(df_1h)
    dr_trades = []
    
    if n_1h >= 25:
        h1_opens = df_1h["open"].values
        h1_highs = df_1h["high"].values
        h1_lows = df_1h["low"].values
        h1_closes = df_1h["close"].values
        h1_vols = df_1h["volume"].values
        h1_ts = df_1h["ts"].values
        
        tr = np.zeros(n_1h)
        for k in range(1, n_1h):
            tr[k] = max(h1_highs[k]-h1_lows[k], abs(h1_highs[k]-h1_closes[k-1]), abs(h1_lows[k]-h1_closes[k-1]))
        atr = np.zeros(n_1h)
        for k in range(13, n_1h): atr[k] = np.mean(tr[k-13:k+1])
        
        i = 20
        while i < n_1h - 24:
            if h1_closes[i] <= h1_opens[i]: i += 1; continue
            pump_pct = (h1_closes[i] - h1_opens[i]) / h1_opens[i]
            if pump_pct < 0.0 or pump_pct > 0.025: i += 1; continue
            
            c_range = h1_highs[i] - h1_lows[i]
            if c_range <= 0: i += 1; continue
            upper_wick = h1_highs[i] - max(h1_opens[i], h1_closes[i])
            if (upper_wick / c_range) < 0.20: i += 1; continue
            
            base_v = np.mean(h1_vols[i-20:i])
            if base_v <= 0 or (h1_vols[i] / base_v) < 3.5: i += 1; continue
            
            entry_ts = h1_ts[i] + tf_ms_1h
            sub_idx = np.searchsorted(m1_ts, entry_ts)
            if sub_idx >= n_1m: i += 1; continue
            
            entry_px = h1_closes[i]
            risk_dist = 1.0 * atr[i]
            if risk_dist <= 0: i += 1; continue
            
            sl_px = entry_px + risk_dist
            tp_px = entry_px - (2.0 * risk_dist)
            
            outcome = None
            m1_held = 0
            for m_i in range(sub_idx, min(n_1m, sub_idx + 2880)):
                m1_held += 1
                if m1_highs[m_i] >= sl_px: outcome = "LOSS"; break
                elif m1_lows[m_i] <= tp_px: outcome = "WIN"; break
                
            if outcome is None:
                pnl_r = ((entry_px - m1_closes[min(n_1m-1, sub_idx + 2879)]) / risk_dist) - FRICTION_R
                outcome = "WIN" if pnl_r > 0 else "LOSS"
            else:
                pnl_r = (2.0 - FRICTION_R) if outcome == "WIN" else (-1.0 - FRICTION_R)
                
            dr_trades.append({
                "symbol": sym, "strategy": "DumpRide 1H", "pnl_r": pnl_r, "outcome": outcome, "m1_held": m1_held
            })
            i += max(1, m1_held // 60)
            
    # ── 2. VOL2B2T 15M LONG ───────────────────────────────────────────
    tf_ms_15m = 900 * 1000
    b_15m_ts = (m1_ts // tf_ms_15m) * tf_ms_15m
    
    df_15m = df_raw.groupby(b_15m_ts).agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).reset_index().rename(columns={'index': 'ts'})
    
    n_15m = len(df_15m)
    v2_trades = []
    
    if n_15m >= 25:
        m15_opens = df_15m["open"].values
        m15_highs = df_15m["high"].values
        m15_lows = df_15m["low"].values
        m15_closes = df_15m["close"].values
        m15_vols = df_15m["volume"].values
        m15_ts = df_15m["ts"].values
        
        tr = np.zeros(n_15m)
        for k in range(1, n_15m):
            tr[k] = max(m15_highs[k]-m15_lows[k], abs(m15_highs[k]-m15_closes[k-1]), abs(m15_lows[k]-m15_closes[k-1]))
        atr = np.zeros(n_15m)
        for k in range(13, n_15m): atr[k] = np.mean(tr[k-13:k+1])
        
        i = 20
        while i < n_15m - 24:
            if m15_closes[i] >= m15_opens[i]: i += 1; continue
            dump_pct = (m15_closes[i] - m15_opens[i]) / m15_opens[i]
            if dump_pct > -0.015: i += 1; continue
            
            base_v = np.mean(m15_vols[i-20:i])
            if base_v <= 0 or (m15_vols[i] / base_v) < 3.0: i += 1; continue
            
            spike_low = m15_lows[i]
            
            reclaim_idx = -1
            for f in range(i + 1, min(n_15m - 10, i + 9)):
                if m15_lows[f] < spike_low and m15_closes[f] > spike_low:
                    f_range = m15_highs[f] - m15_lows[f]
                    if f_range > 0:
                        lower_wick = min(m15_opens[f], m15_closes[f]) - m15_lows[f]
                        if (lower_wick / f_range) >= 0.20:
                            reclaim_idx = f
                            break
                            
            if reclaim_idx == -1: i += 1; continue
            
            entry_ts = m15_ts[reclaim_idx] + tf_ms_15m
            sub_idx = np.searchsorted(m1_ts, entry_ts)
            if sub_idx >= n_1m: i += 1; continue
            
            entry_px = m15_closes[reclaim_idx]
            risk_dist = 1.5 * atr[reclaim_idx]
            if risk_dist <= 0: i += 1; continue
            
            sl_px = entry_px - risk_dist
            tp_px = entry_px + (2.0 * risk_dist)
            
            outcome = None
            m1_held = 0
            for m_i in range(sub_idx, min(n_1m, sub_idx + 1440)):
                m1_held += 1
                if m1_lows[m_i] <= sl_px: outcome = "LOSS"; break
                elif m1_highs[m_i] >= tp_px: outcome = "WIN"; break
                
            if outcome is None:
                pnl_r = ((m1_closes[min(n_1m-1, sub_idx + 1439)] - entry_px) / risk_dist) - FRICTION_R
                outcome = "WIN" if pnl_r > 0 else "LOSS"
            else:
                pnl_r = (2.0 - FRICTION_R) if outcome == "WIN" else (-1.0 - FRICTION_R)
                
            v2_trades.append({
                "symbol": sym, "strategy": "Vol2b2t 15m", "pnl_r": pnl_r, "outcome": outcome, "m1_held": m1_held
            })
            i = reclaim_idx + max(1, m1_held // 15)
            
    return dr_trades, v2_trades

def run_month_multiprocess(month_name, db_path):
    t0_m = time.time()
    if not os.path.exists(db_path):
        return [], []
        
    print(f"📖 Loading {month_name} ({db_path}) into memory...", flush=True)
    conn = sqlite3.connect(db_path)
    df_all = pd.read_sql_query("SELECT symbol, timestamp, open, high, low, close, volume FROM klines_1m", conn)
    conn.close()
    
    print(f"⚡ Loaded {len(df_all):,} 1m candles in {time.time()-t0_m:.1f}s! Grouping by symbol...", flush=True)
    grouped = list(df_all.groupby("symbol"))
    del df_all
    
    print(f"🔬 Processing {len(grouped)} symbols across 8 CPU cores...", flush=True)
    dr_all, v2_all = [], []
    with ProcessPoolExecutor(max_workers=8) as pool:
        results = pool.map(process_symbol_candles, grouped)
        for dr, v2 in results:
            for t in dr: t["month"] = month_name
            for t in v2: t["month"] = month_name
            dr_all.extend(dr)
            v2_all.extend(v2)
            
    print(f"✅ {month_name} finished in {time.time()-t0_m:.1f}s! DumpRide 1H: {len(dr_all)} trades | Vol2b2t 15m: {len(v2_all)} trades\n", flush=True)
    return dr_all, v2_all

all_dr = []
all_v2 = []

for m_name, path in DATASETS:
    dr_m, v2_m = run_month_multiprocess(m_name, path)
    all_dr.extend(dr_m)
    all_v2.extend(v2_m)

def print_final_report(name, trades):
    if not trades:
        print(f"[{name}] No trades generated.")
        return
        
    total = len(trades)
    wins = len([t for t in trades if t["outcome"] == "WIN"])
    losses = total - wins
    wr = (wins / total) * 100.0
    net_r = sum(t["pnl_r"] for t in trades)
    gross_win = sum(t["pnl_r"] for t in trades if t["pnl_r"] > 0)
    gross_loss = abs(sum(t["pnl_r"] for t in trades if t["pnl_r"] < 0))
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    
    df = pd.DataFrame(trades)
    
    print(f"=========================================================================================", flush=True)
    print(f"🏆 {name} — 4-MONTH LIFETIME METRICS (APRIL - JULY 2026)", flush=True)
    print(f"=========================================================================================", flush=True)
    print(f"  • Total Trades Sampled : {total:,} Trades", flush=True)
    print(f"  • Overall Win Rate     : {wr:.2f}% ({wins:,} Wins / {losses:,} Losses)", flush=True)
    print(f"  • Net Return (R)       : {'+' if net_r > 0 else ''}{net_r:,.2f} R (After 0.18% Friction)", flush=True)
    print(f"  • Profit Factor        : {pf:.2f}", flush=True)
    print(f"  • Avg Hold Time        : {np.mean([t['m1_held'] for t in trades]):.0f} minutes", flush=True)
    print(f"-----------------------------------------------------------------------------------------", flush=True)
    print(f"{'Month':<12} | {'Trades':<8} | {'Win Rate':<10} | {'Net Return (R)':<16} | {'Profit Factor'}", flush=True)
    print(f"-----------------------------------------------------------------------------------------", flush=True)
    
    for m in ["April 2026", "May 2026", "June 2026", "July 2026"]:
        sub = df[df["month"] == m]
        if len(sub) == 0: continue
        m_total = len(sub)
        m_wins = len(sub[sub["outcome"] == "WIN"])
        m_wr = (m_wins / m_total) * 100.0
        m_net = sub["pnl_r"].sum()
        m_gw = sub[sub["pnl_r"] > 0]["pnl_r"].sum()
        m_gl = abs(sub[sub["pnl_r"] < 0]["pnl_r"].sum())
        m_pf = (m_gw / m_gl) if m_gl > 0 else float("inf")
        print(f"{m:<12} | {m_total:<8d} | {m_wr:>6.1f}%   | {m_net:>+12.2f} R    | {m_pf:>6.2f}", flush=True)
    print(f"=========================================================================================\n", flush=True)

print_final_report("1. DUMPRIDE 1H INSTITUTIONAL ABSORPTION SHORT", all_dr)
print_final_report("2. VOL2B2T 15M CAPITULATION RECLAIM LONG", all_v2)
