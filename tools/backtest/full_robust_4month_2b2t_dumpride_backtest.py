import os
import sys
import sqlite3
import time
import numpy as np
import pandas as pd
from concurrent.futures import ProcessPoolExecutor

print("=========================================================================================")
print("🧪 FULL ROBUST 4-MONTH BACKTEST (APRIL - JULY 2026 / 58.1 MILLION 1-MIN BARS / 330+ COINS)")
print("=========================================================================================")

DATASETS = [
    ("April 2026", "datasets/april_2026_1m.db"),
    ("May 2026",   "datasets/may_2026_1m.db"),
    ("June 2026",  "datasets/june_2026_1m.db"),
    ("July 2026",  "datasets/july_2026_1m.db"),
]

FRICTION_PCT = 0.0018 # 0.10% fee + 0.08% slippage = 0.18% round-trip friction

def resample_1m_to_tf(m1_rows, tf_min):
    tf_ms = tf_min * 60 * 1000
    bars = []
    cur_bar = None
    cur_1m = []
    
    for row in m1_rows:
        ts, o, h, l, c, v = row
        b_ts = (ts // tf_ms) * tf_ms
        if cur_bar is None or cur_bar["ts"] != b_ts:
            if cur_bar is not None:
                cur_bar["m1_bars"] = cur_1m
                bars.append(cur_bar)
            cur_bar = {"ts": b_ts, "open": o, "high": h, "low": l, "close": c, "vol": v}
            cur_1m = [row]
        else:
            if h > cur_bar["high"]: cur_bar["high"] = h
            if l < cur_bar["low"]: cur_bar["low"] = l
            cur_bar["close"] = c
            cur_bar["vol"] += v
            cur_1m.append(row)
            
    if cur_bar is not None:
        cur_bar["m1_bars"] = cur_1m
        bars.append(cur_bar)
        
    return bars

def evaluate_month_dataset(month_name, db_path, strategy_type="dumpride_1h"):
    if not os.path.exists(db_path):
        return []
        
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    symbols = [r[0] for r in c.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]
    
    month_trades = []
    
    for sym in symbols:
        m1_rows = c.execute(
            "SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC",
            (sym,)
        ).fetchall()
        if len(m1_rows) < 1000:
            continue
            
        m1_ts = np.array([r[0] for r in m1_rows])
        m1_highs = np.array([r[2] for r in m1_rows])
        m1_lows = np.array([r[3] for r in m1_rows])
        m1_closes = np.array([r[4] for r in m1_rows])
        
        if strategy_type == "dumpride_1h":
            tf_min = 60
            bars = resample_1m_to_tf(m1_rows, tf_min)
            n_bars = len(bars)
            if n_bars < 25: continue
            
            opens = np.array([b["open"] for b in bars])
            highs = np.array([b["high"] for b in bars])
            lows = np.array([b["low"] for b in bars])
            closes = np.array([b["close"] for b in bars])
            vols = np.array([b["vol"] for b in bars])
            
            # ATR(14)
            tr = np.zeros(n_bars)
            for k in range(1, n_bars): tr[k] = max(highs[k]-lows[k], abs(highs[k]-closes[k-1]), abs(lows[k]-closes[k-1]))
            atr = np.zeros(n_bars)
            for k in range(13, n_bars): atr[k] = np.mean(tr[k-13:k+1])
            
            i = 20
            while i < n_bars - 24:
                # 1. Bullish green candle
                if closes[i] <= opens[i]: i += 1; continue
                pump_pct = (closes[i] - opens[i]) / opens[i]
                if pump_pct < 0.0 or pump_pct > 0.025: i += 1; continue # 0 to +2.5% pump
                
                # 2. Upper wick rejection >= 20%
                c_range = highs[i] - lows[i]
                if c_range <= 0: i += 1; continue
                upper_wick = highs[i] - max(opens[i], closes[i])
                if (upper_wick / c_range) < 0.20: i += 1; continue
                
                # 3. 20-bar volume surge >= 3.5x
                base_v = np.mean(vols[i-20:i])
                if base_v <= 0 or (vols[i] / base_v) < 3.5: i += 1; continue
                
                # Entry on 1m resolution
                entry_ts = bars[i]["ts"] + (60 * 60 * 1000)
                sub_idx = np.searchsorted(m1_ts, entry_ts)
                if sub_idx >= len(m1_rows): i += 1; continue
                
                entry_px = closes[i]
                risk_dist = 1.0 * atr[i]
                if risk_dist <= 0: i += 1; continue
                
                sl_px = entry_px + risk_dist
                tp_px = entry_px - (2.0 * risk_dist)
                
                # Friction calculation in R
                sl_pct = risk_dist / entry_px
                friction_r = FRICTION_PCT / sl_pct if sl_pct > 0 else 0.05
                
                # 1-minute step execution
                outcome = None
                m1_held = 0
                max_walk = 2880 # 48 hours
                for m_i in range(sub_idx, min(len(m1_rows), sub_idx + max_walk)):
                    m1_held += 1
                    h_m = m1_highs[m_i]
                    l_m = m1_lows[m_i]
                    
                    if h_m >= sl_px and l_m <= tp_px:
                        outcome = "LOSS"; break
                    elif h_m >= sl_px:
                        outcome = "LOSS"; break
                    elif l_m <= tp_px:
                        outcome = "WIN"; break
                        
                if outcome is None:
                    pnl_r = ((entry_px - m1_closes[min(len(m1_rows)-1, sub_idx + max_walk - 1)]) / risk_dist) - friction_r
                    outcome = "WIN" if pnl_r > 0 else "LOSS"
                else:
                    pnl_r = (2.0 - friction_r) if outcome == "WIN" else (-1.0 - friction_r)
                    
                month_trades.append({
                    "month": month_name, "symbol": sym, "strategy": "DumpRide 1H",
                    "pnl_r": pnl_r, "outcome": outcome, "m1_held": m1_held
                })
                i += max(1, m1_held // 60)
                
        elif strategy_type == "vol2b2t_15m":
            tf_min = 15
            bars = resample_1m_to_tf(m1_rows, tf_min)
            n_bars = len(bars)
            if n_bars < 25: continue
            
            opens = np.array([b["open"] for b in bars])
            highs = np.array([b["high"] for b in bars])
            lows = np.array([b["low"] for b in bars])
            closes = np.array([b["close"] for b in bars])
            vols = np.array([b["vol"] for b in bars])
            
            tr = np.zeros(n_bars)
            for k in range(1, n_bars): tr[k] = max(highs[k]-lows[k], abs(highs[k]-closes[k-1]), abs(lows[k]-closes[k-1]))
            atr = np.zeros(n_bars)
            for k in range(13, n_bars): atr[k] = np.mean(tr[k-13:k+1])
            
            i = 20
            while i < n_bars - 24:
                if closes[i] >= opens[i]: i += 1; continue
                dump_pct = (closes[i] - opens[i]) / opens[i]
                if dump_pct > -0.015: i += 1; continue
                
                base_v = np.mean(vols[i-20:i])
                if base_v <= 0 or (vols[i] / base_v) < 3.0: i += 1; continue
                
                spike_low = lows[i]
                
                reclaim_idx = -1
                for f in range(i + 1, min(n_bars - 10, i + 9)):
                    if lows[f] < spike_low and closes[f] > spike_low:
                        f_range = highs[f] - lows[f]
                        if f_range > 0:
                            lower_wick = min(opens[f], closes[f]) - lows[f]
                            if (lower_wick / f_range) >= 0.20:
                                reclaim_idx = f
                                break
                                
                if reclaim_idx == -1: i += 1; continue
                
                entry_ts = bars[reclaim_idx]["ts"] + (15 * 60 * 1000)
                sub_idx = np.searchsorted(m1_ts, entry_ts)
                if sub_idx >= len(m1_rows): i += 1; continue
                
                entry_px = closes[reclaim_idx]
                risk_dist = 1.5 * atr[reclaim_idx]
                if risk_dist <= 0: i += 1; continue
                
                sl_px = entry_px - risk_dist
                tp_px = entry_px + (2.0 * risk_dist)
                
                sl_pct = risk_dist / entry_px
                friction_r = FRICTION_PCT / sl_pct if sl_pct > 0 else 0.05
                
                outcome = None
                m1_held = 0
                max_walk = 1440 # 24 hours
                for m_i in range(sub_idx, min(len(m1_rows), sub_idx + max_walk)):
                    m1_held += 1
                    h_m = m1_highs[m_i]
                    l_m = m1_lows[m_i]
                    
                    if l_m <= sl_px and h_m >= tp_px:
                        outcome = "LOSS"; break
                    elif l_m <= sl_px:
                        outcome = "LOSS"; break
                    elif h_m >= tp_px:
                        outcome = "WIN"; break
                        
                if outcome is None:
                    pnl_r = ((m1_closes[min(len(m1_rows)-1, sub_idx + max_walk - 1)] - entry_px) / risk_dist) - friction_r
                    outcome = "WIN" if pnl_r > 0 else "LOSS"
                else:
                    pnl_r = (2.0 - friction_r) if outcome == "WIN" else (-1.0 - friction_r)
                    
                month_trades.append({
                    "month": month_name, "symbol": sym, "strategy": "Vol2b2t 15m",
                    "pnl_r": pnl_r, "outcome": outcome, "m1_held": m1_held
                })
                i = reclaim_idx + max(1, m1_held // 15)
                
    conn.close()
    return month_trades

print("\n🚀 EXECUTING 4-MONTH COMPREHENSIVE BACKTEST ACROSS ALL DATASETS...\n")

all_dumpride_trades = []
all_vol2b2t_trades = []

for m_name, path in DATASETS:
    t0_m = time.time()
    print(f"🔄 Processing {m_name} ({path})...")
    dr_trades = evaluate_month_dataset(m_name, path, strategy_type="dumpride_1h")
    v2_trades = evaluate_month_dataset(m_name, path, strategy_type="vol2b2t_15m")
    
    all_dumpride_trades.extend(dr_trades)
    all_vol2b2t_trades.extend(v2_trades)
    print(f"   -> {m_name} complete in {time.time()-t0_m:.1f}s | DumpRide 1H: {len(dr_trades)} trades | Vol2b2t 15m: {len(v2_trades)} trades\n")

def print_final_strategy_report(name, trades):
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
    
    # Monthly Breakdown Table
    df = pd.DataFrame(trades)
    
    print(f"=========================================================================================")
    print(f"🏆 {name} — 4-MONTH LIFETIME METRICS (APRIL - JULY 2026)")
    print(f"=========================================================================================")
    print(f"  • Total Trades Sampled : {total:,} Trades")
    print(f"  • Overall Win Rate     : {win_rate_str(wins, total)} ({wins:,} Wins / {losses:,} Losses)")
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

def win_rate_str(w, t):
    return f"{(w/t)*100.0:.1f}%" if t > 0 else "0.0%"

print_final_strategy_report("1. DUMPRIDE 1H INSTITUTIONAL ABSORPTION SHORT", all_dumpride_trades)
print_final_strategy_report("2. VOL2B2T 15M CAPITULATION RECLAIM LONG", all_vol2b2t_trades)
