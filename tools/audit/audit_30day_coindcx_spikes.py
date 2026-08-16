#!/usr/bin/env python3
"""audit_30day_coindcx_spikes.py — 30-Day Quantitative Volume Spike Audit across ALL CoinDCX Perpetual Futures Coins.

Calculates:
1. All coins with >= 30x (3,000%) Volume Spikes on 15m timeframe over the last 30 days.
2. All coins with >= 1.30x (30%+ volume surges) on 15m timeframe over the last 30 days.
3. For every spike event:
   - Symbol & Exact IST Timestamp
   - Spike Volume Multiplier (x baseline)
   - Spike Candle Price Expansion %
   - Subsequent Peak Rally % (MFE)
   - Subsequent Max Drawdown % (MAE)
"""
import os
import sys
import json
import time
import datetime
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

# Ensure adapter import
sys.path.insert(0, "/root/go-trader/platforms/coindcx")
try:
    from adapter import CoinDCXExchangeAdapter
except ImportError:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../platforms/coindcx")))
    from adapter import CoinDCXExchangeAdapter

A = CoinDCXExchangeAdapter()

def fetch_30day_klines(base):
    """Fetch 1 month (~2880 bars) of 15m OHLCV candles."""
    try:
        sym = f"{base}USDT"
        all_rows = []
        endTime = None
        
        for _ in range(2):
            url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=15m&limit=1500"
            if endTime:
                url += f"&endTime={endTime}"
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            rows = json.load(urllib.request.urlopen(req, timeout=10))
            if not isinstance(rows, list) or len(rows) == 0:
                break
            all_rows = rows + all_rows
            endTime = rows[0][0] - 1
            if len(rows) < 1500:
                break
                
        if len(all_rows) < 100:
            return None
            
        out = []
        seen = set()
        for r in sorted(all_rows, key=lambda x: x[0]):
            t = int(r[0])
            if t in seen: continue
            seen.add(t)
            out.append({
                "t": t,
                "o": float(r[1]),
                "h": float(r[2]),
                "l": float(r[3]),
                "c": float(r[4]),
                "v": float(r[5])
            })
        return out
    except Exception:
        return None

def analyze_spikes(symbol, klines):
    if not klines or len(klines) < 100:
        return []

    vols = np.array([b["v"] for b in klines])
    opens = np.array([b["o"] for b in klines])
    highs = np.array([b["h"] for b in klines])
    lows = np.array([b["l"] for b in klines])
    closes = np.array([b["c"] for b in klines])
    times = [b["t"] for b in klines]
    
    n_bars = len(klines)
    spikes = []
    
    for i in range(40, n_bars - 24):
        baseline_vols = vols[i-40:i]
        avg_vol = float(np.mean(baseline_vols))
        if avg_vol <= 0:
            continue
            
        cur_vol = vols[i]
        mult = cur_vol / avg_vol
        
        # Check both 30x threshold and >= 1.30x (30% increase)
        if mult >= 30.0 or mult >= 1.30:
            ts_sec = times[i] / 1000.0
            dt_ist = datetime.datetime.fromtimestamp(ts_sec, datetime.timezone(datetime.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M")
            
            entry_px = closes[i]
            body_pct = float((closes[i] - opens[i]) / opens[i] * 100.0)
            is_green = bool(closes[i] >= opens[i])
            
            follow_vol = float(vols[i+1]) if i+1 < n_bars else 0.0
            follow_mult = float(follow_vol / avg_vol)
            
            # Forward Max Rally (MFE) over next 48 bars (12h)
            fut_highs = highs[i+1:min(i+49, n_bars)]
            fut_lows = lows[i+1:min(i+49, n_bars)]
            
            max_high = float(max(fut_highs)) if len(fut_highs) > 0 else entry_px
            min_low = float(min(fut_lows)) if len(fut_lows) > 0 else entry_px
            
            mfe_pct = float((max_high - entry_px) / entry_px * 100.0)
            mae_pct = float((min_low - entry_px) / entry_px * 100.0)
            
            spikes.append({
                "symbol": symbol,
                "time_ist": dt_ist,
                "price": entry_px,
                "body_pct": body_pct,
                "is_green": is_green,
                "mult_x": float(mult),
                "follow_mult_x": follow_mult,
                "mfe_pct": mfe_pct,
                "mae_pct": mae_pct
            })
            
    return spikes

def main():
    print("=== STARTING 30-DAY QUANTITATIVE VOLUME SPIKE RESEARCH FOR COINDCX FUTURES UNIVERSE ===")
    universe = list(A.active_bases())
    print(f"Total CoinDCX Active Futures Base Assets: {len(universe)}")
    
    print("Fetching 1 month (~2,880 15m bars) per coin...")
    all_data = {}
    with ThreadPoolExecutor(max_workers=30) as ex:
        results = list(ex.map(fetch_30day_klines, universe))
        for sym, k in zip(universe, results):
            if k:
                all_data[sym] = k
                
    print(f"Successfully fetched OHLCV for {len(all_data)} CoinDCX futures coins.")
    
    all_spikes = []
    for sym, klines in all_data.items():
        coin_spikes = analyze_spikes(sym, klines)
        all_spikes.extend(coin_spikes)
        
    spikes_30x = [s for s in all_spikes if s["mult_x"] >= 30.0]
    spikes_30pct = [s for s in all_spikes if s["mult_x"] >= 1.30]
    
    print("\n===================================================================================")
    print(f"SUMMARY OF 30-DAY VOLUME SPIKE FINDINGS ACROSS {len(all_data)} COINDCX FUTURES COINS:")
    print(f"  • Total 30x (3,000%) Volume Spikes Detected:      {len(spikes_30x)} events")
    print(f"  • Total >= 1.30x (30%+ Volume Surges) Detected:  {len(spikes_30pct)} events")
    print("===================================================================================\n")
    
    # Save output JSON
    os.makedirs("/root/go-trader/scratch", exist_ok=True)
    with open("/root/go-trader/scratch/spikes_30day_data.json", "w") as f:
        json.dump(all_spikes, f, indent=2)
        
    # Group 30x spikes by coin
    coins_with_30x = {}
    for s in spikes_30x:
        c = s["symbol"]
        if c not in coins_with_30x:
            coins_with_30x[c] = []
        coins_with_30x[c].append(s)
        
    print(f"Distinct CoinDCX Coins with >= 30x Volume Spikes in Last 30 Days: {len(coins_with_30x)} coins\n")
    
    # Print Table of Distinct Coins with 30x Spikes
    print("=== COINDCX COINS WITH 30x VOLUME SPIKES (LAST 30 DAYS) ===")
    print(f"{'Coin':<10} {'30x Spikes Count':<18} {'Largest Spike (x)':<20} {'Avg Spike Body %':<20} {'Avg Max Rally % (MFE)'}")
    print("-" * 90)
    
    coin_summary = []
    for coin, events in coins_with_30x.items():
        max_mult = max(e["mult_x"] for e in events)
        avg_body = sum(e["body_pct"] for e in events) / len(events)
        avg_mfe = sum(e["mfe_pct"] for e in events) / len(events)
        coin_summary.append((coin, len(events), max_mult, avg_body, avg_mfe))
        
    coin_summary.sort(key=lambda x: x[2], reverse=True)
    for c, cnt, max_m, avg_b, avg_r in coin_summary[:40]:
        print(f"{c:<10} {cnt:<18} {max_m:>18.2f}x {avg_b:>18.2f}% {avg_r:>22.2f}%")

if __name__ == "__main__":
    main()
