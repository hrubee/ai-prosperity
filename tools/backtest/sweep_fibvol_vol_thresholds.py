#!/usr/bin/env python3
"""sweep_fibvol_vol_thresholds.py — Multi-Threshold Audit for FibVOL Strategy (Entry @ 0.60 Retracement).

Sweeps:
- Volume Thresholds: 5x, 10x, 15x, 20x, 25x, 30x
- SL Anchors: Spike Low (1.0 Fib), 0.70 Fib, 1.5x ATR, 2.0x ATR
- RR Ratios: 1:2, 1:3, 1:4
"""
import os
import sys
import json
import time
import datetime
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

sys.path.insert(0, "/root/go-trader/platforms/coindcx")
try:
    from adapter import CoinDCXExchangeAdapter
except ImportError:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../platforms/coindcx")))
    from adapter import CoinDCXExchangeAdapter

A = CoinDCXExchangeAdapter()

def fetch_1m_klines(symbol):
    try:
        sym = f"{symbol}USDT"
        all_rows = []
        endTime = None
        limit = 1500
        headers = {"User-Agent": UA}
        for _ in range(25):
            url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1m&limit={limit}"
            if endTime: url += f"&endTime={endTime}"
            req = urllib.request.Request(url, headers=headers)
            try: rows = json.load(urllib.request.urlopen(req, timeout=10))
            except Exception:
                rows = A.get_ohlcv(symbol, interval="1m", limit=1000)
                if rows: rows = [[r[0], r[1], r[2], r[3], r[4], r[5]] for r in rows]
                else: break
            if not isinstance(rows, list) or len(rows) == 0: break
            all_rows = rows + all_rows
            endTime = rows[0][0] - 1
            if len(rows) < limit: break
        if len(all_rows) < 1000: return None
        out = []
        seen = set()
        for r in sorted(all_rows, key=lambda x: x[0]):
            t = int(r[0])
            if t in seen: continue
            seen.add(t)
            out.append({"t": t, "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "v": float(r[5])})
        return out
    except Exception: return None

def aggregate_15m_candles(klines_1m):
    buckets = {}
    for idx, bar in enumerate(klines_1m):
        bar["idx"] = idx
        b_time = (bar["t"] // (15 * 60 * 1000)) * (15 * 60 * 1000)
        if b_time not in buckets:
            buckets[b_time] = {"t": b_time, "o": bar["o"], "h": bar["h"], "l": bar["l"], "c": bar["c"], "v": bar["v"], "m1_start_idx": idx}
        else:
            b = buckets[b_time]
            b["h"] = max(b["h"], bar["h"])
            b["l"] = min(b["l"], bar["l"])
            b["c"] = bar["c"]
            b["v"] += bar["v"]
    return [buckets[k] for k in sorted(buckets.keys())]

def backtest_fibvol_params(data_map, vol_thresh, sl_mode="spike_low", rr_ratio=3.0):
    all_trades = []

    for sym, klines_1m in data_map.items():
        if not klines_1m or len(klines_1m) < 1000: continue
        klines_15m = aggregate_15m_candles(klines_1m)
        if len(klines_15m) < 50: continue

        vols_15m = np.array([b["v"] for b in klines_15m])
        opens_15m = np.array([b["o"] for b in klines_15m])
        highs_15m = np.array([b["h"] for b in klines_15m])
        lows_15m = np.array([b["l"] for b in klines_15m])
        closes_15m = np.array([b["c"] for b in klines_15m])

        i = 40
        n_15m = len(klines_15m)

        while i < n_15m - 2:
            avg_vol = float(np.mean(vols_15m[i-40:i]))
            if avg_vol <= 0: i += 1; continue
            spike_mult = vols_15m[i] / avg_vol
            is_green = closes_15m[i] >= opens_15m[i]

            if spike_mult >= vol_thresh and is_green:
                spike_high = highs_15m[i]
                spike_low = lows_15m[i]
                spike_range = spike_high - spike_low
                if spike_range <= 0: i += 1; continue

                entry_px = spike_high - 0.60 * spike_range  # 0.60 Fib Retracement

                if sl_mode == "spike_low":
                    sl_px = spike_low
                elif sl_mode == "fib_07":
                    sl_px = spike_high - 0.70 * spike_range
                elif sl_mode == "atr_15":
                    ranges = [highs_15m[k] - lows_15m[k] for k in range(max(0, i-14), i)]
                    atr14 = float(np.mean(ranges)) if len(ranges) > 0 else spike_range * 0.5
                    sl_px = entry_px - 1.5 * atr14
                elif sl_mode == "atr_20":
                    ranges = [highs_15m[k] - lows_15m[k] for k in range(max(0, i-14), i)]
                    atr14 = float(np.mean(ranges)) if len(ranges) > 0 else spike_range * 0.5
                    sl_px = entry_px - 2.0 * atr14
                else:
                    sl_px = spike_low

                risk = entry_px - sl_px
                if risk <= 0: i += 1; continue

                tp_px = entry_px + rr_ratio * risk

                start_m1_idx = klines_15m[i + 1]["m1_start_idx"]
                end_m1_idx = min(len(klines_1m) - 1, start_m1_idx + 24 * 15)

                filled = False
                fill_m1_idx = -1
                for idx in range(start_m1_idx, end_m1_idx):
                    if klines_1m[idx]["l"] <= entry_px:
                        filled = True
                        fill_m1_idx = idx
                        break

                if filled and fill_m1_idx >= 0:
                    outcome = None
                    exit_m1_idx = -1
                    for idx in range(fill_m1_idx, len(klines_1m)):
                        m1 = klines_1m[idx]
                        if m1["l"] <= sl_px: outcome = "SL"; exit_m1_idx = idx; break
                        if m1["h"] >= tp_px: outcome = "TP"; exit_m1_idx = idx; break

                    if outcome:
                        pnl_r = rr_ratio if outcome == "TP" else -1.0
                        all_trades.append({"pnl_r": pnl_r, "outcome": outcome, "holding_mins": exit_m1_idx - fill_m1_idx})
                        next_15m_idx = i + max(1, (exit_m1_idx - fill_m1_idx) // 15)
                        i = min(n_15m - 2, next_15m_idx)
                    else: i += 1
                else: i += 1
            else: i += 1

    return all_trades

def main():
    print("=== MULTI-PARAM FibVOL RETRACEMENT (0.60) BACKTEST AUDIT (30 DAYS) ===")
    bases = sorted(list(A.active_bases()))
    data_map = {}
    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = {ex.submit(fetch_1m_klines, base): base for base in bases}
        for fut in futures:
            base = futures[fut]
            res = fut.result()
            if res: data_map[base] = res

    print(f"Loaded 30-day 1m candle data for {len(data_map)} coins.\n")

    thresholds = [5.0, 10.0, 15.0, 20.0, 25.0, 30.0]
    sl_modes = [("Spike Low (1.0 Fib)", "spike_low"), ("0.70 Fib", "fib_07"), ("1.5x ATR", "atr_15"), ("2.0x ATR", "atr_20")]
    rrs = [2.0, 3.0, 4.0]

    for vol_th in thresholds:
        print(f"==========================================================================")
        print(f"🔥 VOLUME THRESHOLD: {vol_th:.0f}x Baseline Volume")
        print(f"==========================================================================")
        print(f"{'SL Mode':<24} | {'RR Ratio':<8} | {'Trades':<6} | {'Win Rate':<8} | {'Net PnL (R)':<12}")
        print("-" * 65)

        for sl_name, sl_mode in sl_modes:
            for rr in rrs:
                trades = backtest_fibvol_params(data_map, vol_th, sl_mode, rr)
                tot = len(trades)
                wins = len([t for t in trades if t["outcome"] == "TP"])
                wr = (wins / tot * 100.0) if tot > 0 else 0.0
                net_r = sum(t["pnl_r"] for t in trades)
                print(f"{sl_name:<24} | 1:{rr:<6.0f} | {tot:<6} | {wr:>6.1f}%  | {net_r:>+10.1f} R")
        print()

if __name__ == "__main__":
    main()
