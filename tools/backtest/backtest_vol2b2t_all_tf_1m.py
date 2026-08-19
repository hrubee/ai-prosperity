import urllib.request
import json
import time
import os
import ssl
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor

ssl_ctx = ssl._create_unverified_context()

print("=========================================================================================")
print("🧪 VOL2B2T MULTI-TIMEFRAME QUANTITATIVE STUDY (1M GRANULARITY EXECUTION)")
print("=========================================================================================")

UNIVERSE = [
    "BTC", "ETH", "SOL", "DOGE", "AVAX", "NEAR", "SUI", "1000PEPE", "WIF", "LINK", "ARB", "OP"
]

CANDLES_PER_SYMBOL = 15000 # ~10.4 days of 1-minute continuous data per symbol (180,000 1m candles)

def fetch_1m_data(symbol, target_candles=15000):
    all_rows = []
    end_time = int(time.time() * 1000)
    for _ in range(target_candles // 1500):
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}USDT&interval=1m&limit=1500&endTime={end_time}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            res = json.load(urllib.request.urlopen(req, context=ssl_ctx, timeout=10))
            if not isinstance(res, list) or len(res) == 0: break
            all_rows = res + all_rows
            end_time = int(res[0][0]) - 1
            time.sleep(0.05)
        except Exception:
            break
    seen = set()
    deduped = []
    for r in all_rows:
        if r[0] not in seen:
            seen.add(r[0]); deduped.append(r)
    deduped.sort(key=lambda r: r[0])
    return symbol, deduped

print(f"📡 Downloading {CANDLES_PER_SYMBOL:,} 1m candles across {len(UNIVERSE)} symbols (180,000 total 1m candles)...")
t0 = time.time()
DATA_1M = {}
with ThreadPoolExecutor(max_workers=6) as pool:
    results = pool.map(fetch_1m_data, UNIVERSE)
    for sym, rows in results:
        if rows and len(rows) > 1000:
            DATA_1M[sym] = rows

print(f"✅ Cached {sum(len(r) for r in DATA_1M.values()):,} 1m candles in {time.time()-t0:.1f}s!\n")

def run_tf_backtest_with_1m_precision(tf_mins=15, vol_mult=3.5, min_dump_pct=-0.015, min_wick_ratio=0.20, rr_target=2.0, sl_atr_mult=1.5):
    trades = []
    bucket_ms = tf_mins * 60 * 1000
    
    for sym, raw_1m in DATA_1M.items():
        m1_times = [int(r[0]) for r in raw_1m]
        m1_opens = np.array([float(r[1]) for r in raw_1m])
        m1_highs = np.array([float(r[2]) for r in raw_1m])
        m1_lows = np.array([float(r[3]) for r in raw_1m])
        m1_closes = np.array([float(r[4]) for r in raw_1m])
        m1_vols = np.array([float(r[5]) for r in raw_1m])
        m1_usdt = np.array([float(r[7]) for r in raw_1m])
        
        n_1m = len(raw_1m)
        
        # Aggregate 1m into target TF bars
        bars_tf = []
        cur_bucket = None
        bucket_rows = []
        
        for idx in range(n_1m):
            t = m1_times[idx]
            b_ts = (t // bucket_ms) * bucket_ms
            if b_ts != cur_bucket:
                if bucket_rows and len(bucket_rows) == tf_mins:
                    bars_tf.append({
                        "ts": cur_bucket,
                        "open": bucket_rows[0]["open"],
                        "high": max(r["high"] for r in bucket_rows),
                        "low": min(r["low"] for r in bucket_rows),
                        "close": bucket_rows[-1]["close"],
                        "vol": sum(r["vol"] for r in bucket_rows),
                        "usdt": sum(r["usdt"] for r in bucket_rows),
                        "end_1m_idx": idx - 1
                    })
                cur_bucket = b_ts
                bucket_rows = []
            bucket_rows.append({
                "open": m1_opens[idx], "high": m1_highs[idx], "low": m1_lows[idx],
                "close": m1_closes[idx], "vol": m1_vols[idx], "usdt": m1_usdt[idx]
            })
            
        n_tf = len(bars_tf)
        if n_tf < 30: continue
        
        # Compute ATR(14) on TF
        tr = np.zeros(n_tf)
        tr[0] = bars_tf[0]["high"] - bars_tf[0]["low"]
        for i in range(1, n_tf):
            h, l, prev_c = bars_tf[i]["high"], bars_tf[i]["low"], bars_tf[i-1]["close"]
            tr[i] = max(h - l, abs(h - prev_c), abs(l - prev_c))
        atr_tf = np.zeros(n_tf)
        for i in range(13, n_tf): atr_tf[i] = np.mean(tr[i-13:i+1])
        
        i = 20
        while i < n_tf - 15:
            b = bars_tf[i]
            c_open, c_high, c_low, c_close = b["open"], b["high"], b["low"], b["close"]
            c_vol, c_usdt = b["vol"], b["usdt"]
            
            # 1. Volume Dump Signal Bar
            if c_close >= c_open: i += 1; continue
            dump_pct = (c_close - c_open) / c_open
            if dump_pct > min_dump_pct: i += 1; continue
            
            base_vol = np.mean([bars_tf[k]["vol"] for k in range(i-20, i)])
            if base_vol <= 0 or (c_vol / base_vol) < vol_mult:
                i += 1; continue
                
            spike_low = c_low
            spike_idx = i
            
            # 2. Look forward in next 8 bars for 2B Reclaim
            reclaim_idx = -1
            for f in range(spike_idx + 1, min(n_tf - 10, spike_idx + 9)):
                f_bar = bars_tf[f]
                # Sweep below spike_low and close back above
                if f_bar["low"] < spike_low and f_bar["close"] > spike_low:
                    # Check lower wick absorption
                    f_range = f_bar["high"] - f_bar["low"]
                    if f_range > 0:
                        lower_wick = min(f_bar["open"], f_bar["close"]) - f_bar["low"]
                        if (lower_wick / f_range) >= min_wick_ratio:
                            reclaim_idx = f
                            break
                            
            if reclaim_idx == -1:
                i += 1; continue
                
            # 3. Enter LONG at close of reclaim bar on 1-minute resolution
            m1_entry_idx = bars_tf[reclaim_idx]["end_1m_idx"] + 1
            if m1_entry_idx >= n_1m: break
            
            entry_px = m1_opens[m1_entry_idx]
            risk_dist = sl_atr_mult * atr_tf[reclaim_idx]
            if risk_dist <= 0 or (risk_dist / entry_px) < 0.001:
                i = reclaim_idx + 1; continue
                
            sl_px = entry_px - risk_dist
            tp_px = entry_px + (rr_target * risk_dist)
            
            # 4. Tick-by-tick 1-minute execution (up to 48 hours / 2880 1m bars)
            outcome = None
            m1_held = 0
            for m_idx in range(m1_entry_idx, min(n_1m, m1_entry_idx + 2880)):
                m1_held += 1
                if m1_lows[m_idx] <= sl_px: outcome = "LOSS"; break
                if m1_highs[m_idx] >= tp_px: outcome = "WIN"; break
                
            if outcome is None:
                pnl_r = (m1_closes[min(n_1m-1, m1_entry_idx + 2879)] - entry_px) / risk_dist
                outcome = "WIN" if pnl_r > 0 else "LOSS"
            else:
                pnl_r = rr_target if outcome == "WIN" else -1.0
                
            trades.append({
                "symbol": sym,
                "tf": f"{tf_mins}m" if tf_mins < 60 else f"{tf_mins//60}h",
                "entry_time": m1_times[m1_entry_idx],
                "pnl_r": pnl_r,
                "outcome": outcome,
                "m1_held": m1_held
            })
            
            bars_held_tf = max(1, m1_held // tf_mins)
            i = reclaim_idx + bars_held_tf
            
    return trades

def print_tf_summary(tf_label, tf_mins, vol_mult, min_dump, min_wick, rr, sl_mult):
    t0_run = time.time()
    trades = run_tf_backtest_with_1m_precision(
        tf_mins=tf_mins, vol_mult=vol_mult, min_dump_pct=min_dump,
        min_wick_ratio=min_wick, rr_target=rr, sl_atr_mult=sl_mult
    )
    if not trades:
        print(f"| {tf_label:<8} | 0 Trades | N/A | N/A | N/A | N/A | N/A |")
        return
        
    total = len(trades)
    wins = len([t for t in trades if t["outcome"] == "WIN"])
    losses = total - wins
    wr = (wins / total) * 100.0
    net_r = sum(t["pnl_r"] for t in trades)
    gross_win = sum(t["pnl_r"] for t in trades if t["pnl_r"] > 0)
    gross_loss = abs(sum(t["pnl_r"] for t in trades if t["pnl_r"] < 0))
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    
    # Max DD
    eq = [0.0]
    for t in trades: eq.append(eq[-1] + t["pnl_r"])
    peak = eq[0]
    max_dd = 0.0
    for e in eq:
        if e > peak: peak = e
        dd = peak - e
        if dd > max_dd: max_dd = dd
        
    avg_min = np.mean([t["m1_held"] for t in trades])
    hold_str = f"{avg_min:.0f}m" if avg_min < 120 else f"{avg_min/60:.1f}h"
    
    print(f"| {tf_label:<8} | {total:>5d} ({wins:>2d}W/{losses:>2d}L) | {wr:>5.1f}% | {net_r:>+7.1f} R | {pf:>5.2f} | -{max_dd:>4.1f} R | {hold_str:>6} |")

print("=========================================================================================")
print("📊 VOL2B2T MULTI-TIMEFRAME PERFORMANCE MATRIX (1-MINUTE EXECUTION PRECISION)")
print("=========================================================================================")
print("| Timeframe| Trades (W/L) | WinRate | Net Return | ProfitF | Max DD  | Avg Hold |")
print("|:---------|:-------------|:--------|:-----------|:--------|:--------|:---------|")

# Backtest across all standard timeframes: 5m, 15m, 30m, 1h, 4h
print_tf_summary("5m",   5,   vol_mult=3.0, min_dump=-0.008, min_wick=0.20, rr=2.0, sl_mult=2.5)
print_tf_summary("15m",  15,  vol_mult=3.5, min_dump=-0.015, min_wick=0.20, rr=2.0, sl_mult=2.0)
print_tf_summary("30m",  30,  vol_mult=3.5, min_dump=-0.020, min_wick=0.20, rr=2.0, sl_mult=1.5)
print_tf_summary("1h",   60,  vol_mult=3.5, min_dump=-0.025, min_wick=0.20, rr=2.0, sl_mult=1.5)
print_tf_summary("4h",   240, vol_mult=3.0, min_dump=-0.040, min_wick=0.20, rr=2.0, sl_mult=1.2)
print("=========================================================================================")
