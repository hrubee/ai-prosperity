#!/usr/bin/env python3
"""tools/backtest/random_chunks_dumpride_1m.py

Random Chunk Monte Carlo Backtest of DumpRide on 1-MINUTE INTRABAR GRANULARITY:
- Uses the June 2026 1-minute database (15,313,290 1m candles across 357 coins).
- Samples 25 random time chunks (7-day, 10-day, 14-day windows).
- Simulates exact 1-minute tick-level intrabar entry, stop loss, and take profit hits.
- Applies 0.10% Taker Fee + 0.10% Slippage.
"""
import sqlite3, random, datetime, time
import numpy as np
import pandas as pd

DB_PATH = "datasets/june_2026_1m.db"
conn = sqlite3.connect(DB_PATH)
df_all = pd.read_sql_query("SELECT symbol, timestamp, open, high, low, close, volume FROM klines_1m ORDER BY symbol, timestamp ASC", conn)
conn.close()

def resample_bars_np(arr, tf_min=240):
    tf_ms = tf_min * 60 * 1000
    b_ts = (arr[:, 0] // tf_ms) * tf_ms
    unique_ts, split_indices = np.unique(b_ts, return_index=True)
    slices = np.split(arr, split_indices[1:])
    n_bars = len(slices)
    bars_ts = np.zeros(n_bars, dtype=np.int64)
    bars_o = np.zeros(n_bars)
    bars_h = np.zeros(n_bars)
    bars_l = np.zeros(n_bars)
    bars_c = np.zeros(n_bars)
    bars_v = np.zeros(n_bars)
    for idx, sl in enumerate(slices):
        bars_ts[idx] = int(sl[0, 0] // tf_ms * tf_ms)
        bars_o[idx] = sl[0, 1]
        bars_h[idx] = np.max(sl[:, 2])
        bars_l[idx] = np.min(sl[:, 3])
        bars_c[idx] = sl[-1, 4]
        bars_v[idx] = np.sum(sl[:, 5])
    return {
        "ts": bars_ts, "open": bars_o, "high": bars_h, "low": bars_l, "close": bars_c, "vol": bars_v,
        "m1_arr": arr
    }

def calc_atr_np(highs, lows, closes, period=14):
    n = len(highs)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr = np.zeros(n)
    atr[0] = tr[0]
    for i in range(1, n):
        if i < period: atr[i] = np.mean(tr[: i + 1])
        else: atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr

sym_arrays = {}
for sym, g in df_all.groupby("symbol"):
    sym_arrays[sym] = g[["timestamp", "open", "high", "low", "close", "volume"]].values

fee_pct = 0.0010
slip_pct = 0.0010

# Pre-extract all 4H spike events with 1m execution arrays
all_signals = []
for sym, m1_arr in sym_arrays.items():
    if len(m1_arr) < 100: continue
    data = resample_bars_np(m1_arr, 240)
    n = len(data["ts"])
    if n < 30: continue
    
    times = data["ts"]
    opens = data["open"]
    highs = data["high"]
    lows = data["low"]
    closes = data["close"]
    vols = data["vol"]
    
    atr14 = calc_atr_np(highs, lows, closes, 14)
    vol_ma = np.zeros(n)
    for i in range(n):
        vol_ma[i] = np.mean(vols[max(0, i - 20) : i]) if i > 0 else vols[0]
        
    m1_times = m1_arr[:, 0]
    m1_highs = m1_arr[:, 2]
    m1_lows = m1_arr[:, 3]
    m1_closes = m1_arr[:, 4]
    
    last_trade_bar = -99
    for i in range(20, n - 1):
        if i - last_trade_bar < 3: continue
        spike = vols[i] / max(1e-5, vol_ma[i])
        
        if spike >= 20.0 and closes[i] > opens[i]:
            entry_px = closes[i]
            sl_px = entry_px + (1.0 * atr14[i])
            risk_dist = sl_px - entry_px
            if risk_dist <= 0 or (risk_dist / entry_px) < 0.005: continue
            tp_px = entry_px - (2.0 * risk_dist)
            
            # Find 1m start index
            next_4h_ts = times[i + 1]
            start_m1_idx = np.searchsorted(m1_times, next_4h_ts)
            if start_m1_idx >= len(m1_times): continue
            
            # Forward 1m slice (up to 48 hours = 2880 1m candles)
            max_m1_idx = min(len(m1_times), start_m1_idx + 2880)
            fwd_l = m1_lows[start_m1_idx:max_m1_idx]
            fwd_h = m1_highs[start_m1_idx:max_m1_idx]
            fwd_c = m1_closes[start_m1_idx:max_m1_idx]
            
            # 1-minute intrabar SL / TP hits
            sl_hits = np.where(fwd_h >= sl_px)[0]
            tp_hits = np.where(fwd_l <= tp_px)[0]
            
            first_sl = sl_hits[0] if len(sl_hits) > 0 else 999999
            first_tp = tp_hits[0] if len(tp_hits) > 0 else 999999
            
            if first_tp < first_sl:
                real_tp = tp_px * (1.0 + slip_pct)
                raw_r = (entry_px - real_tp) / risk_dist
            elif first_sl < first_tp:
                real_sl = sl_px * (1.0 + slip_pct)
                raw_r = -((real_sl - entry_px) / risk_dist)
            else:
                raw_r = (entry_px - fwd_c[-1]) / risk_dist
                
            fee_r = (2.0 * fee_pct) / (risk_dist / entry_px)
            net_r = raw_r - fee_r
            
            all_signals.append({
                "symbol": sym,
                "ts": times[i],
                "spike": spike,
                "entry_px": entry_px,
                "sl_px": sl_px,
                "tp_px": tp_px,
                "net_r": net_r,
                "win": net_r > 0
            })
            last_trade_bar = i

min_ts = min(s["ts"] for s in all_signals)
max_ts = max(s["ts"] for s in all_signals)

# Sample 30 random 7-day to 14-day chunks with 1m execution
random.seed(42)
chunk_results = []
for idx in range(30):
    duration_days = random.choice([7, 10, 14])
    dur_ms = duration_days * 86400 * 1000
    s_t = random.randint(min_ts, max_ts - dur_ms)
    e_t = s_t + dur_ms
    
    trades = [s for s in all_signals if s_t <= s["ts"] <= e_t]
    if not trades: continue
    
    n = len(trades)
    wins = [t for t in trades if t["net_r"] > 0]
    wr = len(wins) / n * 100
    tot_r = sum(t["net_r"] for t in trades)
    
    s_dt = datetime.datetime.fromtimestamp(s_t/1000, tz=datetime.timezone.utc).strftime("%d %b %H:%M")
    e_dt = datetime.datetime.fromtimestamp(e_t/1000, tz=datetime.timezone.utc).strftime("%d %b %H:%M")
    
    chunk_results.append({
        "start": s_dt, "end": e_dt, "days": duration_days, "trades": n, "wr": wr, "tot_r": tot_r
    })

print("\n" + "=" * 115)
print("⚡ DUMPRIDE 1-MINUTE GRANULARITY RANDOM CHUNK MONTE CARLO (357 COINS - JUNE DATASET)")
print("   Exact Intrabar 1m Tick Simulation | 0.10% Taker Fee + 0.10% Slippage | >= 20.0x Volume Surge")
print("=" * 115)
print(f"{'Random Time Window (1m Execution)':<34} | {'Window':<8} | {'Trades':<8} | {'Win Rate':<10} | {'Net Return (R)'}")
print("-" * 115)

for c in chunk_results[:20]:
    win_str = f"{c['start']} to {c['end']}"
    dur_str = f"{c['days']} Days"
    print(f"{win_str:<34} | {dur_str:<8} | {c['trades']:<8} | {c['wr']:>6.1f}%    | {c['tot_r']:>+10.2f} R")

profitable = [c for c in chunk_results if c["tot_r"] > 0]
print("\n" + "-" * 115)
print(f"Summary across 30 random 1-minute granularity chunks:")
print(f"  Profitable Chunks : {len(profitable)} / {len(chunk_results)} ({len(profitable)/len(chunk_results)*100:.1f}%)")
print(f"  Mean Return per Chunk : {np.mean([c['tot_r'] for c in chunk_results]):+.2f} R")
print(f"  Average Win Rate      : {np.mean([c['wr'] for c in chunk_results]):.1f}%")
print("=" * 115)
