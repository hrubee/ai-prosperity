#!/usr/bin/env python3
"""audit_mfe.py — Max Favorable Excursion (MFE) Analysis for FIBVOL Strategy.
Evaluates how far trades rally (Max R) after filling at 0.6 Fib.
"""
import os
import sys
import json
import datetime
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np

sys.path.insert(0, "/root/go-trader/platforms/coindcx")
try:
    from adapter import CoinDCXExchangeAdapter
except ImportError:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../platforms/coindcx")))
    from adapter import CoinDCXExchangeAdapter

A = CoinDCXExchangeAdapter()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

def fetch_15m(sym):
    try:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}USDT&interval=15m&limit=1500"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        return json.load(urllib.request.urlopen(req, timeout=8))
    except Exception:
        return None

def fetch_1m(sym, start_ms, end_ms):
    try:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}USDT&interval=1m&startTime={start_ms}&endTime={end_ms}&limit=1500"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        rows = json.load(urllib.request.urlopen(req, timeout=8))
        return [{"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4])} for r in rows]
    except Exception:
        return []

def run_mfe_audit(sl_fib_level=0.8):
    universe = sorted(list(A.active_bases()))
    
    # 1. Fetch 15m klines
    klines_15m_map = {}
    with ThreadPoolExecutor(max_workers=30) as ex:
        fmap = {ex.submit(fetch_15m, sym): sym for sym in universe}
        for fut in fmap:
            sym = fmap[fut]
            res = fut.result()
            if res: klines_15m_map[sym] = res

    # 2. Identify candidate 30x spikes
    spikes = []
    for sym, rows in klines_15m_map.items():
        vols = np.array([float(r[5]) for r in rows])
        opens = np.array([float(r[1]) for r in rows])
        highs = np.array([float(r[2]) for r in rows])
        lows = np.array([float(r[3]) for r in rows])
        closes = np.array([float(r[4]) for r in rows])
        times = [int(r[0]) for r in rows]

        for i in range(40, len(rows) - 10):
            avg_vol = float(np.mean(vols[i-40:i]))
            if avg_vol <= 0: continue
            mult = vols[i] / avg_vol
            if mult >= 30.0 and closes[i] >= opens[i]:
                spikes.append((sym, i, times[i], times[min(i+48, len(rows)-1)], mult, rows[i-40:min(i+48, len(rows))]))

    # 3. Simulate entries & track Max R MFE
    results = []
    for sym, idx, start_t, end_t, mult, rows_15m in spikes:
        klines_1m = fetch_1m(sym, start_t, end_t)
        if not klines_1m: continue

        highs_15m = [float(r[2]) for r in rows_15m]
        lows_15m = [float(r[3]) for r in rows_15m]
        opens_15m = [float(r[1]) for r in rows_15m]
        closes_15m = [float(r[4]) for r in rows_15m]
        times_15m = [int(r[0]) for r in rows_15m]

        watch_idx = 40
        while watch_idx < len(rows_15m) - 1:
            cur_h, cur_l = highs_15m[watch_idx], lows_15m[watch_idx]
            cur_o, cur_c = opens_15m[watch_idx], closes_15m[watch_idx]

            if cur_c < cur_o and watch_idx > 40: break
            rng = cur_h - cur_l
            if rng <= 0: watch_idx += 1; continue

            entry_px = cur_h - 0.6 * rng
            sl_px = cur_h - sl_fib_level * rng
            risk = entry_px - sl_px
            if risk <= 0: watch_idx += 1; continue

            start_bar_t = times_15m[watch_idx + 1]
            end_bar_t = start_bar_t + 15 * 60 * 1000
            m1_window = [b for b in klines_1m if start_bar_t <= b["t"] < end_bar_t]

            fill_m1_t = None
            for b in m1_window:
                if b["l"] <= entry_px:
                    fill_m1_t = b["t"]
                    break

            if fill_m1_t:
                trade_1m_bars = [b for b in klines_1m if b["t"] >= fill_m1_t]
                max_h_before_sl = entry_px
                hit_sl_first = False
                sl_t = None

                for b in trade_1m_bars:
                    if b["l"] <= sl_px:
                        hit_sl_first = True
                        sl_t = b["t"]
                        break
                    if b["h"] > max_h_before_sl:
                        max_h_before_sl = b["h"]

                max_r = (max_h_before_sl - entry_px) / risk
                dt_entry = datetime.datetime.fromtimestamp(fill_m1_t/1000.0, datetime.timezone(datetime.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M")
                results.append({
                    "symbol": sym,
                    "entry_time": dt_entry,
                    "entry_px": entry_px,
                    "sl_px": sl_px,
                    "risk": risk,
                    "max_r": max_r,
                    "hit_sl_first": hit_sl_first,
                    "spike_mult": mult
                })
                break
            else:
                watch_idx += 1

    return results

def main():
    print("===================================================================")
    print("🚀 FIBVOL MAXIMUM FAVORABLE EXCURSION (MAX R / MFE) AUDIT")
    print("===================================================================")

    for sl_lvl in [0.8, 0.7]:
        print(f"\n--- AUDITING SL LEVEL = {sl_lvl} Fib ---")
        res = run_mfe_audit(sl_lvl)
        
        # Sort by Max R descending
        res.sort(key=lambda x: x["max_r"], reverse=True)
        
        total_trades = len(res)
        trades_4r_plus = [r for r in res if r["max_r"] >= 4.0]
        trades_5r_plus = [r for r in res if r["max_r"] >= 5.0]
        trades_8r_plus = [r for r in res if r["max_r"] >= 8.0]
        trades_10r_plus = [r for r in res if r["max_r"] >= 10.0]
        trades_15r_plus = [r for r in res if r["max_r"] >= 15.0]
        
        max_r_trade = res[0] if res else None
        
        print(f"\nSummary for {sl_lvl} Fib SL:")
        print(f"  Total Filled Trades: {total_trades}")
        print(f"  Trades reaching >= 4.0R: {len(trades_4r_plus)} ({len(trades_4r_plus)/total_trades*100:.1f}%)" if total_trades else "")
        print(f"  Trades reaching >= 5.0R: {len(trades_5r_plus)} ({len(trades_5r_plus)/total_trades*100:.1f}%)" if total_trades else "")
        print(f"  Trades reaching >= 8.0R: {len(trades_8r_plus)} ({len(trades_8r_plus)/total_trades*100:.1f}%)" if total_trades else "")
        print(f"  Trades reaching >= 10.0R: {len(trades_10r_plus)} ({len(trades_10r_plus)/total_trades*100:.1f}%)" if total_trades else "")
        print(f"  Trades reaching >= 15.0R: {len(trades_15r_plus)} ({len(trades_15r_plus)/total_trades*100:.1f}%)" if total_trades else "")
        
        if max_r_trade:
            print(f"  🔥 MAXIMUM R ACHIEVED IN A SINGLE TRADE: +{max_r_trade['max_r']:.2f}R ({max_r_trade['symbol']} @ {max_r_trade['entry_time']})")

        print("\n  Detailed Trade MFE Breakdown (Ranked by Max R):")
        print(f"  {'Symbol':<10} {'Entry Time (IST)':<18} {'Entry Px':<12} {'Max R (MFE)':<14} {'Hit SL First?':<15} {'Spike Mult'}")
        print("  " + "-" * 80)
        for r in res:
            sl_status = "YES (Stopped Out)" if r["hit_sl_first"] else "NO (Reached Max R)"
            print(f"  {r['symbol']:<10} {r['entry_time']:<18} {r['entry_px']:<12.6f} {r['max_r']:>+10.2f}R   {sl_status:<15} {r['spike_mult']:>6.1f}x")

if __name__ == "__main__":
    main()
