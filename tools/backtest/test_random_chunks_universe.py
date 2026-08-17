#!/usr/bin/env python3
import sqlite3, random, datetime
import numpy as np
import pandas as pd

conn = sqlite3.connect("datasets/candles_4h_april_july.db")
df_all = pd.read_sql_query("SELECT symbol, timestamp, open, high, low, close, volume FROM candles_4h ORDER BY symbol, timestamp ASC", conn)
conn.close()

def calc_atr(highs, lows, closes, period=14):
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

def run_sim(data_dict, start_ts=None, end_ts=None, vol_mult=20.0, rr_target=2.0, fee_pct=0.0010, slip_pct=0.0010):
    trades = []
    for sym, arr in data_dict.items():
        times = arr[:, 0]
        opens = arr[:, 1]
        highs = arr[:, 2]
        lows = arr[:, 3]
        closes = arr[:, 4]
        vols = arr[:, 5]
        n = len(times)
        if n < 30: continue
        atr14 = calc_atr(highs, lows, closes, 14)
        vol_ma = np.zeros(n)
        for i in range(n):
            vol_ma[i] = np.mean(vols[max(0, i - 20) : i]) if i > 0 else vols[0]
            
        last_trade_bar = -99
        for i in range(20, n - 1):
            t = times[i]
            if start_ts is not None and t < start_ts: continue
            if end_ts is not None and t > end_ts: continue
            if i - last_trade_bar < 3: continue
            curr_vol = vols[i]
            base_vol = max(1e-5, vol_ma[i])
            spike = curr_vol / base_vol
            if spike >= vol_mult and closes[i] > opens[i]:
                entry_px = closes[i]
                sl_px = entry_px + (1.0 * atr14[i])
                risk_dist = sl_px - entry_px
                if risk_dist <= 0 or (risk_dist / entry_px) < 0.005: continue
                tp_px = entry_px - (rr_target * risk_dist)
                max_fwd = min(n, i + 13)
                fwd_highs = highs[i + 1 : max_fwd]
                fwd_lows = lows[i + 1 : max_fwd]
                fwd_closes = closes[i + 1 : max_fwd]
                sl_hits = np.where(fwd_highs >= sl_px)[0]
                tp_hits = np.where(fwd_lows <= tp_px)[0]
                first_sl = sl_hits[0] if len(sl_hits) > 0 else 9999
                first_tp = tp_hits[0] if len(tp_hits) > 0 else 9999
                if first_tp < first_sl:
                    real_tp = tp_px * (1.0 + slip_pct)
                    raw_r = (entry_px - real_tp) / risk_dist
                elif first_sl < first_tp:
                    real_sl = sl_px * (1.0 + slip_pct)
                    raw_r = -((real_sl - entry_px) / risk_dist)
                else:
                    raw_r = (entry_px - fwd_closes[-1]) / risk_dist
                fee_r = (2.0 * fee_pct) / (risk_dist / entry_px)
                trades.append(raw_r - fee_r)
                last_trade_bar = i
    return trades

min_t = int(df_all["timestamp"].min())
max_t = int(df_all["timestamp"].max()) - (30 * 86400 * 1000)

random.seed(42)
chunk_30d = []
for idx in range(50):
    s_t = random.randint(min_t, max_t)
    e_t = s_t + (30 * 86400 * 1000)
    t_res = run_sim(sym_arrays, start_ts=s_t, end_ts=e_t, vol_mult=20.0, rr_target=2.0)
    if len(t_res) > 0:
        n = len(t_res)
        wins = [x for x in t_res if x > 0]
        wr = len(wins) / n * 100
        tot_r = sum(t_res)
        s_dt = datetime.datetime.fromtimestamp(s_t/1000, tz=datetime.timezone.utc).strftime("%d %b %Y")
        e_dt = datetime.datetime.fromtimestamp(e_t/1000, tz=datetime.timezone.utc).strftime("%d %b %Y")
        chunk_30d.append({
            "start": s_dt, "end": e_dt, "trades": n, "wr": wr, "tot_r": tot_r
        })

print("\n" + "=" * 110)
print("🎲 334 ALTCOIN UNIVERSE: 50 RANDOM 30-DAY MONTE CARLO CHUNK SAMPLING RESULTS (>= 20X VOLUME SURGE)")
print("=" * 110)
print(f"{'Chunk Time Window':<32} | {'Trades':<8} | {'Win Rate':<10} | {'Net Return (R)'}")
print("-" * 110)
for c in chunk_30d[:20]:
    start_end_str = f"{c['start']} to {c['end']}"
    print(f"{start_end_str:<32} | {c['trades']:<8} | {c['wr']:>6.1f}%    | {c['tot_r']:>+10.2f} R")

profitable = [c for c in chunk_30d if c["tot_r"] > 0]
print("\n" + "-" * 110)
print(f"Summary across 50 random 30-day chunks:")
print(f"  Profitable Chunks : {len(profitable)} / {len(chunk_30d)} ({len(profitable)/len(chunk_30d)*100:.1f}%)")
print(f"  Mean Return per Chunk : {np.mean([c['tot_r'] for c in chunk_30d]):+.2f} R")
print(f"  Average Win Rate      : {np.mean([c['wr'] for c in chunk_30d]):.1f}%")
print("=" * 110)
