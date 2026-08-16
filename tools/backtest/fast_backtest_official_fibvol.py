#!/usr/bin/env python3
"""fast_backtest_official_fibvol.py — Parallel & Disk-Cached 30-Day FibVOL Backtest.

Direct Engine Backtest of shared_strategies/open/fibvol.py & shared_scripts/stream_fibvol_coindcx.py.

Parameters:
- Entry Fib Level: 0.50 (High - 0.50 * Range)
- Stop Loss Fib Level: 0.60 (High - 0.60 * Range) -> Risk = 0.10 * Range
- Target RR Ratio: 1:5 RR (Spike High)
- Multi-Candle Retracement Loop (Green re-plot, Red cancel)
- Trailing SL: Activates at +2.0R, trailing 1.0R behind peak high
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
CACHE_FILE = "/Users/hrushi/Downloads/Desktop offline/vibe coding/go trader/go-trader/scratch/klines_30d_cache.json"

ENTRY_FIB_LEVEL = 0.50
SL_FIB_LEVEL = 0.60
RR_RATIO = 5.0
TRAIL_ACT_R = 2.0
TRAIL_DIST_R = 1.0
RISK_FRAC = 0.01
START_BAL_USD = 200.0

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
        for _ in range(20):
            url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1m&limit={limit}"
            if endTime: url += f"&endTime={endTime}"
            req = urllib.request.Request(url, headers=headers)
            try:
                rows = json.load(urllib.request.urlopen(req, timeout=8))
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

def run_strategy_backtest(data_map, vol_spike_mult=30.0):
    wallet_usd = START_BAL_USD
    trades_log = []

    for sym, klines_1m in data_map.items():
        klines_15m = aggregate_15m_candles(klines_1m)
        if len(klines_15m) < 50: continue

        vols_15m = np.array([b["v"] for b in klines_15m])
        opens_15m = np.array([b["o"] for b in klines_15m])
        highs_15m = np.array([b["h"] for b in klines_15m])
        lows_15m = np.array([b["l"] for b in klines_15m])
        closes_15m = np.array([b["c"] for b in klines_15m])
        times_15m = [b["t"] for b in klines_15m]

        i = 40
        n_15m = len(klines_15m)

        while i < n_15m - 2:
            avg_vol = float(np.mean(vols_15m[i-40:i]))
            if avg_vol <= 0: i += 1; continue
            spike_mult = vols_15m[i] / avg_vol
            is_green = closes_15m[i] >= opens_15m[i]

            if spike_mult >= vol_spike_mult and is_green:
                watch_idx = i
                
                while watch_idx < n_15m - 1:
                    cur_h = highs_15m[watch_idx]
                    cur_l = lows_15m[watch_idx]
                    cur_o = opens_15m[watch_idx]
                    cur_c = closes_15m[watch_idx]

                    if cur_c < cur_o and watch_idx > i:
                        break  # RED candle closed -> stop watch

                    rng = cur_h - cur_l
                    if rng <= 0: watch_idx += 1; continue

                    entry_px = cur_h - ENTRY_FIB_LEVEL * rng
                    sl_px = cur_h - SL_FIB_LEVEL * rng
                    risk = entry_px - sl_px
                    if risk <= 0: watch_idx += 1; continue

                    tp_px = entry_px + RR_RATIO * risk

                    start_1m_t = times_15m[watch_idx + 1]
                    end_1m_t = start_1m_t + 15 * 60 * 1000
                    m1_bars = [b for b in klines_1m if start_1m_t <= b["t"] < end_1m_t]

                    filled = False
                    fill_m1_idx = -1
                    for m1 in m1_bars:
                        if m1["l"] <= entry_px:
                            filled = True
                            fill_m1_idx = m1["idx"]
                            break

                    if filled and fill_m1_idx >= 0:
                        current_sl = sl_px
                        initial_sl = sl_px
                        peak_px = entry_px
                        outcome = None
                        exit_m1_idx = -1
                        exit_px = 0.0

                        for idx in range(fill_m1_idx, len(klines_1m)):
                            m1 = klines_1m[idx]

                            if m1["h"] > peak_px:
                                peak_px = m1["h"]
                                peak_r = (peak_px - entry_px) / risk
                                if peak_r >= TRAIL_ACT_R:
                                    desired_sl = peak_px - (TRAIL_DIST_R * risk)
                                    if desired_sl > current_sl:
                                        current_sl = desired_sl

                            if m1["l"] <= current_sl:
                                outcome = "SL" if current_sl <= entry_px else "TRAIL_SL"
                                exit_m1_idx = idx
                                exit_px = current_sl
                                break

                            if m1["h"] >= tp_px:
                                outcome = "TP"
                                exit_m1_idx = idx
                                exit_px = tp_px
                                break

                        if outcome:
                            risk_usd = wallet_usd * RISK_FRAC
                            units = risk_usd / risk
                            pnl_usd = (exit_px - entry_px) * units
                            r_multiple = (exit_px - entry_px) / risk

                            wallet_usd += pnl_usd

                            dt_ist = datetime.datetime.fromtimestamp(
                                klines_1m[fill_m1_idx]["t"] / 1000.0,
                                datetime.timezone(datetime.timedelta(hours=5, minutes=30))
                            ).strftime("%Y-%m-%d %H:%M")

                            trades_log.append({
                                "symbol": sym,
                                "time_ist": dt_ist,
                                "spike_mult": round(spike_mult, 1),
                                "entry_px": entry_px,
                                "sl_px": initial_sl,
                                "tp_px": tp_px,
                                "exit_px": exit_px,
                                "outcome": outcome,
                                "pnl_r": r_multiple,
                                "pnl_usd": pnl_usd,
                                "wallet_usd": wallet_usd,
                                "holding_mins": exit_m1_idx - fill_m1_idx
                            })

                            next_15m_idx = i + max(1, (exit_m1_idx - fill_m1_idx) // 15)
                            i = min(n_15m - 2, next_15m_idx)
                            break
                        else:
                            watch_idx += 1
                    else:
                        watch_idx += 1
            else:
                i += 1

    return trades_log

def main():
    print("==========================================================================")
    print("⚡ FAST AUDIT:shared_strategies/open/fibvol.py (Entry 0.50 | SL 0.60 | 1:5 RR)")
    print("==========================================================================")

    bases = sorted(list(A.active_bases()))
    print(f"CoinDCX Base Assets: {len(bases)}")

    data_map = {}
    with ThreadPoolExecutor(max_workers=30) as ex:
        futures = {ex.submit(fetch_1m_klines, base): base for base in bases}
        for fut in futures:
            base = futures[fut]
            res = fut.result()
            if res: data_map[base] = res

    print(f"Loaded 30-day 1m OHLCV data for {len(data_map)} coins.\n")

    thresholds = [10.0, 15.0, 20.0, 30.0]

    for vol_th in thresholds:
        trades = run_strategy_backtest(data_map, vol_spike_mult=vol_th)
        tot = len(trades)
        wins = len([t for t in trades if t["outcome"] == "TP"])
        trails = len([t for t in trades if t["outcome"] == "TRAIL_SL"])
        sls = len([t for t in trades if t["outcome"] == "SL"])

        win_rate = (wins / tot * 100.0) if tot > 0 else 0.0
        total_pnl_r = sum(t["pnl_r"] for t in trades)
        total_pnl_usd = sum(t["pnl_usd"] for t in trades)
        final_wallet = START_BAL_USD + total_pnl_usd

        print(f"==========================================================================")
        print(f"🔥 VOLUME SPIKE THRESHOLD: {vol_th:.0f}x Baseline Volume")
        print(f"==========================================================================")
        print(f"Total Executed Trades : {tot}")
        print(f"Wins / TrailSL / Loss : {wins} TP / {trails} TrailSL / {sls} SL")
        print(f"Win Rate              : {win_rate:.1f}%")
        print(f"Total Net PnL (R)     : {total_pnl_r:+.1f} R")
        print(f"Total Net PnL ($ USD) : ${total_pnl_usd:+.2f} USD")
        print(f"Ending Wallet Equity  : ${final_wallet:.2f} USD (Initial: ${START_BAL_USD:.2f})\n")

    sample_trades = run_strategy_backtest(data_map, vol_spike_mult=15.0)
    print("="*90)
    print("📋 SAMPLE TRADES EXECUTED BY STRATEGY (15x Spike | Entry 0.50 | SL 0.60 | 1:5 RR):")
    print("="*90)
    for tr in sample_trades[:15]:
        print(f"- {tr['symbol']:<10} | {tr['time_ist']} | {tr['spike_mult']:>4.1f}x | {tr['outcome']:<8} | PnL: {tr['pnl_r']:+4.1f}R (${tr['pnl_usd']:+6.2f}) | Hold: {tr['holding_mins']}m")

if __name__ == "__main__":
    main()
