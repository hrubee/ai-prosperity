#!/usr/bin/env python3
import sqlite3
import numpy as np
import pandas as pd

DB_PATH = "datasets/june_2026_1m.db"
conn = sqlite3.connect(DB_PATH)
df_all = pd.read_sql_query("SELECT symbol, timestamp, open, high, low, close, volume FROM klines_1m ORDER BY symbol, timestamp ASC", conn)
conn.close()

def resample_bars_np(arr, tf_min=15):
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
    return {"ts": bars_ts, "open": bars_o, "high": bars_h, "low": bars_l, "close": bars_c, "vol": bars_v, "m1_arr": arr}

def calc_ema_np(series, span=21):
    alpha = 2.0 / (span + 1.0)
    out = np.zeros_like(series)
    out[0] = series[0]
    for i in range(1, len(series)):
        out[i] = alpha * series[i] + (1.0 - alpha) * out[i - 1]
    return out

def calc_atr_np(h, l, c, period=14):
    n = len(h)
    tr = np.zeros(n)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr = np.zeros(n)
    atr[0] = tr[0]
    for i in range(1, n):
        if i < period: atr[i] = np.mean(tr[: i + 1])
        else: atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr

sym_arrays = {}
for sym, g in df_all.groupby("symbol"):
    sym_arrays[sym] = g[["timestamp", "open", "high", "low", "close", "volume"]].values

setups_15m = []
for sym, m1_arr in sym_arrays.items():
    if len(m1_arr) < 100: continue
    data = resample_bars_np(m1_arr, 15)
    n = len(data["ts"])
    if n < 30: continue
    lows = data["low"]
    highs = data["high"]
    closes = data["close"]
    times = data["ts"]
    ema21 = calc_ema_np(lows, 21)
    atr14 = calc_atr_np(highs, lows, closes, 14)
    m1_times = m1_arr[:, 0]
    m1_h = m1_arr[:, 2]
    m1_l = m1_arr[:, 3]
    m1_c = m1_arr[:, 4]
    last_setup_bar = -99
    for i in range(21, n - 2):
        if i - last_setup_bar < 4: continue
        if closes[i] <= ema21[i] or lows[i] <= ema21[i]: continue
        if highs[i] < np.max(highs[max(0, i-3) : i]): continue
        sw_low = np.min(lows[max(0, i-8) : i+1])
        sw_high = highs[i]
        impulse_size = sw_high - sw_low
        if impulse_size < (1.2 * atr14[i]) or (impulse_size / sw_low) < 0.010: continue
        next_ts = times[i + 1]
        start_idx = np.searchsorted(m1_times, next_ts)
        if start_idx >= len(m1_times): continue
        last_setup_bar = i
        setups_15m.append({
            "sw_low": sw_low, "sw_high": sw_high, "ema": ema21[i], "atr": atr14[i],
            "start_idx": start_idx, "m1_h": m1_h, "m1_l": m1_l, "m1_c": m1_c
        })

print(f"Total 15m Setups detected: {len(setups_15m)}")
fee_pct = 0.0010
slip_pct = 0.0010

results_15m = []
for fib_entry in [0.236, 0.382, 0.500, 0.618, 0.705, 0.786]:
    for sl_name, sl_mode in [("Below EMA21", "below_ema"), ("Swing Low (1.0)", "swing_low"), ("Fib 0.886", "fib_0886"), ("1.0x ATR", "atr_10")]:
        for rr in [1.5, 2.0, 2.5, 3.0, 4.0]:
            trades = []
            for s in setups_15m:
                fib_range = s["sw_high"] - s["sw_low"]
                entry_px = s["sw_high"] - (fib_entry * fib_range)
                if sl_mode == "below_ema": sl_px = s["ema"] * 0.997
                elif sl_mode == "swing_low": sl_px = s["sw_low"] * 0.998
                elif sl_mode == "fib_0886": sl_px = s["sw_high"] - (0.886 * fib_range)
                elif sl_mode == "atr_10": sl_px = entry_px - (1.0 * s["atr"])
                else: sl_px = s["sw_low"]
                
                risk_dist = entry_px - sl_px
                if risk_dist <= 0 or (risk_dist / entry_px) < 0.004: continue
                tp_px = entry_px + (rr * risk_dist)
                
                start_i = s["start_idx"]
                m1_l = s["m1_l"]
                m1_h = s["m1_h"]
                m1_c = s["m1_c"]
                max_scan = min(len(m1_l), start_i + (16 * 15))
                
                sl_slice = m1_l[start_i:max_scan]
                fill_mask = np.where(sl_slice <= entry_px)[0]
                if len(fill_mask) == 0: continue
                fill_idx = start_i + fill_mask[0]
                
                max_hold = min(len(m1_l), fill_idx + (32 * 15))
                hold_l = m1_l[fill_idx:max_hold]
                hold_h = m1_h[fill_idx:max_hold]
                sl_hits = np.where(hold_l <= sl_px)[0]
                tp_hits = np.where(hold_h >= tp_px)[0]
                first_sl = sl_hits[0] if len(sl_hits) > 0 else 999999
                first_tp = tp_hits[0] if len(tp_hits) > 0 else 999999
                
                if first_sl < first_tp:
                    real_sl = sl_px * (1.0 - slip_pct)
                    exit_r = -((entry_px - real_sl) / risk_dist)
                elif first_tp < first_sl:
                    real_tp = tp_px * (1.0 - slip_pct)
                    exit_r = (real_tp - entry_px) / risk_dist
                else:
                    exit_r = (m1_c[min(len(m1_c)-1, max_hold)] - entry_px) / risk_dist
                    
                fee_r = (2.0 * fee_pct) / (risk_dist / entry_px)
                trades.append(exit_r - fee_r)
                
            n = len(trades)
            if n < 10: continue
            wins = [t for t in trades if t > 0]
            losses = [t for t in trades if t <= 0]
            wr = len(wins) / n * 100
            tot_r = sum(trades)
            gw = sum(wins)
            gl = abs(sum(losses)) if losses else 0.001
            pf = gw / gl
            results_15m.append({
                "fib_entry": fib_entry, "sl_type": sl_name, "rr": rr,
                "trades": n, "win_rate": wr, "pf": pf, "total_r": tot_r
            })

results_15m.sort(key=lambda x: x["total_r"], reverse=True)
print("\n" + "=" * 110)
print("15-MINUTE TIMEFRAME FIBEMA SWEEP RESULTS (TOP 20)")
print("=" * 110)
print(f"{'Fib Entry':<10} | {'Stop Loss':<18} | {'RR Target':<10} | {'Trades':<8} | {'Win Rate':<10} | {'PF':<6} | {'Total Return (R)'}")
print("-" * 110)
for r in results_15m[:20]:
    fe = f"{r['fib_entry']:.3f}"
    st = r['sl_type']
    rr_str = f"1:{r['rr']:.1f}"
    tr = str(r['trades'])
    wr_str = f"{r['win_rate']:.1f}%"
    pf_str = f"{r['pf']:.2f}"
    r_str = f"{r['total_r']:+.2f} R"
    print(f"{fe:<10} | {st:<18} | {rr_str:<10} | {tr:<8} | {wr_str:>8}    | {pf_str:<6} | {r_str:>14}")
print("=" * 110)
