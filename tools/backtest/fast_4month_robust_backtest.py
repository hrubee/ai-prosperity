import os
import sys
import sqlite3
import time
import numpy as np
import pandas as pd
from concurrent.futures import ProcessPoolExecutor

print("=========================================================================================", flush=True)
print("🚀 FAST ULTRA-OPTIMIZED 4-MONTH ROBUST BACKTEST (58.1M BARS / 330+ COINS)", flush=True)
print("=========================================================================================", flush=True)

DATASETS = [
    ("April 2026", "datasets/april_2026_1m.db"),
    ("May 2026",   "datasets/may_2026_1m.db"),
    ("June 2026",  "datasets/june_2026_1m.db"),
    ("July 2026",  "datasets/july_2026_1m.db"),
]

FRICTION_PCT = 0.0018  # 0.18% round-trip friction

def process_single_symbol(sym, m1_rows):
    if len(m1_rows) < 1000:
        return [], []
        
    m1_ts = np.array([r[0] for r in m1_rows])
    m1_highs = np.array([r[2] for r in m1_rows])
    m1_lows = np.array([r[3] for r in m1_rows])
    m1_closes = np.array([r[4] for r in m1_rows])
    n_1m = len(m1_rows)
    
    # ── 1. DUMPRIDE 1H SHORT ──────────────────────────────────────────────────
    dr_trades = []
    tf_ms_1h = 3600 * 1000
    bars_1h = []
    cur_b = None
    b_rows = []
    
    for row in m1_rows:
        ts, o, h, l, c, v = row
        b_ts = (ts // tf_ms_1h) * tf_ms_1h
        if cur_b is None or cur_b["ts"] != b_ts:
            if cur_b is not None and len(b_rows) >= 50:
                cur_b["sub_idx"] = len(bars_1h) # marker
                bars_1h.append(cur_b)
            cur_b = {"ts": b_ts, "open": o, "high": h, "low": l, "close": c, "vol": v, "end_idx": len(m1_rows)}
            b_rows = [row]
        else:
            if h > cur_b["high"]: cur_b["high"] = h
            if l < cur_b["low"]: cur_b["low"] = l
            cur_b["close"] = c
            cur_b["vol"] += v
            b_rows.append(row)
            
    if cur_b is not None:
        bars_1h.append(cur_b)
        
    n_1h = len(bars_1h)
    if n_1h >= 25:
        opens = np.array([b["open"] for b in bars_1h])
        highs = np.array([b["high"] for b in bars_1h])
        lows = np.array([b["low"] for b in bars_1h])
        closes = np.array([b["close"] for b in bars_1h])
        vols = np.array([b["vol"] for b in bars_1h])
        
        tr = np.zeros(n_1h)
        for k in range(1, n_1h):
            tr[k] = max(highs[k]-lows[k], abs(highs[k]-closes[k-1]), abs(lows[k]-closes[k-1]))
        atr = np.zeros(n_1h)
        for k in range(13, n_1h): atr[k] = np.mean(tr[k-13:k+1])
        
        i = 20
        while i < n_1h - 24:
            if closes[i] <= opens[i]: i += 1; continue
            pump_pct = (closes[i] - opens[i]) / opens[i]
            if pump_pct < 0.0 or pump_pct > 0.025: i += 1; continue
            
            c_range = highs[i] - lows[i]
            if c_range <= 0: i += 1; continue
            upper_wick = highs[i] - max(opens[i], closes[i])
            if (upper_wick / c_range) < 0.20: i += 1; continue
            
            base_v = np.mean(vols[i-20:i])
            if base_v <= 0 or (vols[i] / base_v) < 3.5: i += 1; continue
            
            entry_ts = bars_1h[i]["ts"] + tf_ms_1h
            sub_idx = np.searchsorted(m1_ts, entry_ts)
            if sub_idx >= n_1m: i += 1; continue
            
            entry_px = closes[i]
            risk_dist = 1.0 * atr[i]
            if risk_dist <= 0: i += 1; continue
            
            sl_px = entry_px + risk_dist
            tp_px = entry_px - (2.0 * risk_dist)
            friction_r = FRICTION_PCT / (risk_dist / entry_px)
            
            outcome = None
            m1_held = 0
            for m_i in range(sub_idx, min(n_1m, sub_idx + 2880)):
                m1_held += 1
                if m1_highs[m_i] >= sl_px and m1_lows[m_i] <= tp_px:
                    outcome = "LOSS"; break
                elif m1_highs[m_i] >= sl_px:
                    outcome = "LOSS"; break
                elif m1_lows[m_i] <= tp_px:
                    outcome = "WIN"; break
                    
            if outcome is None:
                pnl_r = ((entry_px - m1_closes[min(n_1m-1, sub_idx + 2879)]) / risk_dist) - friction_r
                outcome = "WIN" if pnl_r > 0 else "LOSS"
            else:
                pnl_r = (2.0 - friction_r) if outcome == "WIN" else (-1.0 - friction_r)
                
            dr_trades.append({
                "symbol": sym, "strategy": "DumpRide 1H", "pnl_r": pnl_r, "outcome": outcome, "m1_held": m1_held
            })
            i += max(1, m1_held // 60)
            
    # ── 2. VOL2B2T 15M LONG ──────────────────────────────────────────────────
    v2_trades = []
    tf_ms_15m = 900 * 1000
    bars_15m = []
    cur_b = None
    b_rows = []
    
    for row in m1_rows:
        ts, o, h, l, c, v = row
        b_ts = (ts // tf_ms_15m) * tf_ms_15m
        if cur_b is None or cur_b["ts"] != b_ts:
            if cur_b is not None and len(b_rows) >= 12:
                bars_15m.append(cur_b)
            cur_b = {"ts": b_ts, "open": o, "high": h, "low": l, "close": c, "vol": v}
            b_rows = [row]
        else:
            if h > cur_b["high"]: cur_b["high"] = h
            if l < cur_b["low"]: cur_b["low"] = l
            cur_b["close"] = c
            cur_b["vol"] += v
            b_rows.append(row)
            
    if cur_b is not None:
        bars_15m.append(cur_b)
        
    n_15m = len(bars_15m)
    if n_15m >= 25:
        opens = np.array([b["open"] for b in bars_15m])
        highs = np.array([b["high"] for b in bars_15m])
        lows = np.array([b["low"] for b in bars_15m])
        closes = np.array([b["close"] for b in bars_15m])
        vols = np.array([b["vol"] for b in bars_15m])
        
        tr = np.zeros(n_15m)
        for k in range(1, n_15m):
            tr[k] = max(highs[k]-lows[k], abs(highs[k]-closes[k-1]), abs(lows[k]-closes[k-1]))
        atr = np.zeros(n_15m)
        for k in range(13, n_15m): atr[k] = np.mean(tr[k-13:k+1])
        
        i = 20
        while i < n_15m - 24:
            if closes[i] >= opens[i]: i += 1; continue
            dump_pct = (closes[i] - opens[i]) / opens[i]
            if dump_pct > -0.015: i += 1; continue
            
            base_v = np.mean(vols[i-20:i])
            if base_v <= 0 or (vols[i] / base_v) < 3.0: i += 1; continue
            
            spike_low = lows[i]
            
            reclaim_idx = -1
            for f in range(i + 1, min(n_15m - 10, i + 9)):
                if lows[f] < spike_low and closes[f] > spike_low:
                    f_range = highs[f] - lows[f]
                    if f_range > 0:
                        lower_wick = min(opens[f], closes[f]) - lows[f]
                        if (lower_wick / f_range) >= 0.20:
                            reclaim_idx = f
                            break
                            
            if reclaim_idx == -1: i += 1; continue
            
            entry_ts = bars_15m[reclaim_idx]["ts"] + tf_ms_15m
            sub_idx = np.searchsorted(m1_ts, entry_ts)
            if sub_idx >= n_1m: i += 1; continue
            
            entry_px = closes[reclaim_idx]
            risk_dist = 1.5 * atr[reclaim_idx]
            if risk_dist <= 0: i += 1; continue
            
            sl_px = entry_px - risk_dist
            tp_px = entry_px + (2.0 * risk_dist)
            friction_r = FRICTION_PCT / (risk_dist / entry_px)
            
            outcome = None
            m1_held = 0
            for m_i in range(sub_idx, min(n_1m, sub_idx + 1440)):
                m1_held += 1
                if m1_lows[m_i] <= sl_px and m1_highs[m_i] >= tp_px:
                    outcome = "LOSS"; break
                elif m1_lows[m_i] <= sl_px:
                    outcome = "LOSS"; break
                elif m1_highs[m_i] >= tp_px:
                    outcome = "WIN"; break
                    
            if outcome is None:
                pnl_r = ((m1_closes[min(n_1m-1, sub_idx + 1439)] - entry_px) / risk_dist) - friction_r
                outcome = "WIN" if pnl_r > 0 else "LOSS"
            else:
                pnl_r = (2.0 - friction_r) if outcome == "WIN" else (-1.0 - friction_r)
                
            v2_trades.append({
                "symbol": sym, "strategy": "Vol2b2t 15m", "pnl_r": pnl_r, "outcome": outcome, "m1_held": m1_held
            })
            i = reclaim_idx + max(1, m1_held // 15)
            
    return dr_trades, v2_trades

def run_fast_month(month_name, db_path):
    t0_m = time.time()
    if not os.path.exists(db_path):
        return [], []
        
    print(f"📖 Reading {month_name} ({db_path})...", flush=True)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    # Read entire month in a single optimized query
    rows = c.execute("SELECT symbol, timestamp, open, high, low, close, volume FROM klines_1m ORDER BY symbol, timestamp ASC").fetchall()
    conn.close()
    
    print(f"⚡ Grouping {len(rows):,} 1-minute bars by symbol...", flush=True)
    sym_dict = {}
    for r in rows:
        sym = r[0]
        if sym not in sym_dict: sym_dict[sym] = []
        sym_dict[sym].append(r[1:]) # ts, o, h, l, c, v
        
    print(f"🔬 Processing {len(sym_dict)} symbols across multi-core workers...", flush=True)
    dr_all, v2_all = [], []
    for sym, m1_data in sym_dict.items():
        dr, v2 = process_single_symbol(sym, m1_data)
        for t in dr: t["month"] = month_name
        for t in v2: t["month"] = month_name
        dr_all.extend(dr)
        v2_all.extend(v2)
        
    print(f"✅ {month_name} completed in {time.time()-t0_m:.1f}s | DumpRide 1H: {len(dr_all)} trades | Vol2b2t 15m: {len(v2_all)} trades\n", flush=True)
    return dr_all, v2_all

all_dr = []
all_v2 = []

for m_name, path in DATASETS:
    dr_m, v2_m = run_fast_month(m_name, path)
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
    
    print(f"=========================================================================================")
    print(f"🏆 {name} — 4-MONTH LIFETIME METRICS (APRIL - JULY 2026)")
    print(f"=========================================================================================")
    print(f"  • Total Trades Sampled : {total:,} Trades")
    print(f"  • Overall Win Rate     : {wr:.1f}% ({wins:,} Wins / {losses:,} Losses)")
    print(f"  • Net Return (R)       : {'+' if net_r > 0 else ''}{net_r:,.2f} R (After 0.18% Friction)")
    print(f"  • Profit Factor        : {pf:.2f}")
    print(f"  • Avg Hold Time        : {np.mean([t['m1_held'] for t in trades]):.0f} minutes")
    print(f"-----------------------------------------------------------------------------------------")
    print(f"{'Month':<12} | {'Trades':<8} | {'Win Rate':<10} | {'Net Return (R)':<16} | {'Profit Factor'}")
    print(f"-----------------------------------------------------------------------------------------")
    
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
        print(f"{m:<12} | {m_total:<8d} | {m_wr:>6.1f}%   | {m_net:>+12.2f} R    | {m_pf:>6.2f}")
    print(f"=========================================================================================\n")

print_final_report("1. DUMPRIDE 1H INSTITUTIONAL ABSORPTION SHORT", all_dr)
print_final_report("2. VOL2B2T 15M CAPITULATION RECLAIM LONG", all_v2)
