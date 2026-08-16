#!/usr/bin/env python3
"""audit_fibvol_05_06.py — Rigorous 30-Day Audit for FibVOL Entry @ 0.50 Fib & SL @ 0.60 Fib.

Exact Fibonacci Parameters:
- Entry: 0.50 Fib Retracement (High - 0.50 * (High - Low))
- Stop Loss: 0.60 Fib Retracement (High - 0.60 * (High - Low)) -> Risk = 0.10 * Range
- Take Profit Targets:
    * 1:2 RR (High - 0.30 * Range)
    * 1:3 RR (High - 0.20 * Range)
    * 1:4 RR (High - 0.10 * Range)
    * 1:5 RR (Spike High = High - 0.00 * Range)
    * 1:6 RR (High + 0.10 * Range)
- Intrabar 1m Granularity: Simulates 1m-by-1m limit fills, SL hits, and TP hits.
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

def backtest_fibvol_05_06(data_map, vol_thresh=10.0, rr_target=5.0, move_be=False):
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

                # EXACT Fib Retracement Levels
                entry_px = spike_high - 0.50 * spike_range  # 0.50 Fib
                sl_px = spike_high - 0.60 * spike_range     # 0.60 Fib

                risk = entry_px - sl_px
                if risk <= 0: i += 1; continue

                tp_px = entry_px + rr_target * risk

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
                    current_sl = sl_px
                    be_active = False
                    outcome = None
                    exit_m1_idx = -1

                    for idx in range(fill_m1_idx, len(klines_1m)):
                        m1 = klines_1m[idx]

                        # Check SL
                        if m1["l"] <= current_sl:
                            outcome = "BE" if (be_active and current_sl >= entry_px) else "SL"
                            exit_m1_idx = idx
                            break

                        # Check TP
                        if m1["h"] >= tp_px:
                            outcome = "TP"
                            exit_m1_idx = idx
                            break

                        # Breakeven move at +1.0R
                        if move_be and not be_active and m1["h"] >= entry_px + 1.0 * risk:
                            be_active = True
                            current_sl = max(current_sl, entry_px)

                    if outcome:
                        if outcome == "TP": pnl_r = rr_target
                        elif outcome == "BE": pnl_r = 0.0
                        else: pnl_r = -1.0

                        dt_ist = datetime.datetime.fromtimestamp(
                            klines_1m[fill_m1_idx]["t"] / 1000.0,
                            datetime.timezone(datetime.timedelta(hours=5, minutes=30))
                        ).strftime("%Y-%m-%d %H:%M")

                        all_trades.append({
                            "symbol": sym,
                            "fill_time_ist": dt_ist,
                            "spike_mult": round(spike_mult, 1),
                            "entry_px": entry_px,
                            "sl_px": sl_px,
                            "tp_px": tp_px,
                            "outcome": outcome,
                            "pnl_r": pnl_r,
                            "pnl_usd": pnl_r * 2.0,
                            "holding_mins": exit_m1_idx - fill_m1_idx
                        })

                        next_15m_idx = i + max(1, (exit_m1_idx - fill_m1_idx) // 15)
                        i = min(n_15m - 2, next_15m_idx)
                    else: i += 1
                else: i += 1
            else: i += 1

    return all_trades

def main():
    print("==========================================================================")
    print("🔬 RIGOROUS AUDIT: FibVOL ENTRY @ 0.50 FIB & STOP LOSS @ 0.60 FIB (30 DAYS)")
    print("==========================================================================")
    bases = sorted(list(A.active_bases()))
    data_map = {}
    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = {ex.submit(fetch_1m_klines, base): base for base in bases}
        for fut in futures:
            base = futures[fut]
            res = fut.result()
            if res: data_map[base] = res

    print(f"Loaded 30-day 1m candle data for {len(data_map)} coins.\n")

    vol_thresholds = [5.0, 10.0, 15.0, 20.0, 30.0]
    rr_targets = [2.0, 3.0, 4.0, 5.0, 6.0]

    for vol_th in vol_thresholds:
        print(f"==========================================================================")
        print(f"🔥 VOLUME SPIKE THRESHOLD: {vol_th:.0f}x Baseline Volume")
        print(f"==========================================================================")
        print(f"{'Target RR Ratio':<20} | {'Trades':<8} | {'Win Rate':<10} | {'Total Net R':<12} | {'Net USD ($)':<12}")
        print("-" * 72)

        for rr in rr_targets:
            trades = backtest_fibvol_05_06(data_map, vol_thresh=vol_th, rr_target=rr, move_be=False)
            tot = len(trades)
            wins = len([t for t in trades if t["outcome"] == "TP"])
            wr = (wins / tot * 100.0) if tot > 0 else 0.0
            net_r = sum(t["pnl_r"] for t in trades)
            net_usd = sum(t["pnl_usd"] for t in trades)

            rr_label = f"1:{rr:.0f} RR (Spike High)" if rr == 5.0 else f"1:{rr:.0f} RR"
            print(f"{rr_label:<20} | {tot:<8} | {wr:>8.1f}%  | {net_r:>+10.1f} R  | ${net_usd:>+10.2f}")
        print()

    # Detailed trade sample for 10x volume spike @ 1:5 RR (Spike High Target)
    trades_sample = backtest_fibvol_05_06(data_map, vol_thresh=10.0, rr_target=5.0, move_be=False)
    print("="*80)
    print("📋 DETAILED TRADE LOG SAMPLE (10x Spike | Entry 0.50 | SL 0.60 | TP 1:5 RR):")
    print("="*80)
    for tr in trades_sample[:15]:
        print(f"- {tr['symbol']:<10} | {tr['fill_time_ist']} | {tr['spike_mult']:>4.1f}x | {tr['outcome']:<2} ({tr['pnl_r']:+1.0f}R, ${tr['pnl_usd']:+.2f}) | Hold: {tr['holding_mins']}m")

if __name__ == "__main__":
    main()
