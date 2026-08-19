import urllib.request
import json
import time
import os
import ssl
import numpy as np
from concurrent.futures import ThreadPoolExecutor

ssl_ctx = ssl._create_unverified_context()

print("=========================================================================================")
print("🧪 DEEP 15M DUMPRIDE STUDY (20,000 1M CANDLES PER COIN = ~14 DAYS / 240,000 1M CANDLES)")
print("=========================================================================================")

UNIVERSE = [
    "BTC", "ETH", "SOL", "DOGE", "AVAX", "NEAR", "SUI", "1000PEPE", "WIF", "LINK", "ARB", "OP"
]

CANDLES_PER_SYMBOL = 15000 # 10 days of 1-minute data per symbol

def fetch_1m_deep(symbol, target_candles=15000):
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

print(f"📡 Fetching {CANDLES_PER_SYMBOL:,} 1m candles across {len(UNIVERSE)} symbols ({len(UNIVERSE)*CANDLES_PER_SYMBOL:,} total 1m candles)...")
t0 = time.time()
DEEP_1M = {}
with ThreadPoolExecutor(max_workers=6) as pool:
    results = pool.map(fetch_1m_deep, UNIVERSE)
    for sym, rows in results:
        if rows and len(rows) > 1000:
            DEEP_1M[sym] = rows

total_1m = sum(len(r) for r in DEEP_1M.values())
print(f"✅ Downloaded and cached {total_1m:,} 1-minute candles in {time.time()-t0:.1f}s!\n")

def run_deep_15m(sl_atr_mult=1.5, rr_target=2.0, min_vol_mult=3.5, min_wick_ratio=0.20):
    trades = []
    for sym, raw_1m in DEEP_1M.items():
        m1_times = [int(r[0]) for r in raw_1m]
        m1_opens = np.array([float(r[1]) for r in raw_1m])
        m1_highs = np.array([float(r[2]) for r in raw_1m])
        m1_lows = np.array([float(r[3]) for r in raw_1m])
        m1_closes = np.array([float(r[4]) for r in raw_1m])
        m1_vols = np.array([float(r[5]) for r in raw_1m])
        m1_usdt = np.array([float(r[7]) for r in raw_1m])
        
        n_1m = len(raw_1m)
        bars_15m = []
        cur_bucket = None
        bucket_rows = []
        
        for idx in range(n_1m):
            t = m1_times[idx]
            b_ts = (t // (900 * 1000)) * (900 * 1000)
            if b_ts != cur_bucket:
                if bucket_rows and len(bucket_rows) == 15:
                    bars_15m.append({
                        "ts": cur_bucket,
                        "open": bucket_rows[0]["open"],
                        "high": max(r["high"] for r in bucket_rows),
                        "low": min(r["low"] for r in bucket_rows),
                        "close": bucket_rows[-1]["close"],
                        "vol": sum(r["vol"] for r in bucket_rows),
                        "usdt_vol": sum(r["usdt"] for r in bucket_rows),
                        "end_1m_idx": idx - 1
                    })
                cur_bucket = b_ts
                bucket_rows = []
            bucket_rows.append({
                "open": m1_opens[idx], "high": m1_highs[idx], "low": m1_lows[idx],
                "close": m1_closes[idx], "vol": m1_vols[idx], "usdt": m1_usdt[idx]
            })
            
        n_15m = len(bars_15m)
        if n_15m < 30: continue
        
        tr = np.zeros(n_15m)
        for i in range(1, n_15m):
            h, l, prev_c = bars_15m[i]["high"], bars_15m[i]["low"], bars_15m[i-1]["close"]
            tr[i] = max(h - l, abs(h - prev_c), abs(l - prev_c))
        atr_15m = np.zeros(n_15m)
        for i in range(13, n_15m): atr_15m[i] = np.mean(tr[i-13:i+1])
        
        i = 25
        while i < n_15m - 10:
            b = bars_15m[i]
            c_open, c_high, c_low, c_close = b["open"], b["high"], b["low"], b["close"]
            c_vol, c_usdt = b["vol"], b["usdt_vol"]
            
            if c_close <= c_open: i += 1; continue
            pump_pct = ((c_close - c_open) / c_open) * 100.0
            if pump_pct > 2.0: i += 1; continue
            
            c_range = c_high - c_low
            if c_range <= 0: i += 1; continue
            upper_wick = c_high - max(c_open, c_close)
            if (upper_wick / c_range) < min_wick_ratio: i += 1; continue
            
            base_vol = np.mean([bars_15m[k]["vol"] for k in range(i-20, i)])
            base_usdt = np.mean([bars_15m[k]["usdt_vol"] for k in range(i-20, i)])
            if base_vol <= 0 or base_usdt < 15000.0 or (c_vol / base_vol) < min_vol_mult:
                i += 1; continue
                
            m1_entry_idx = b["end_1m_idx"] + 1
            if m1_entry_idx >= n_1m: break
            
            entry_px = m1_opens[m1_entry_idx]
            risk_dist = sl_atr_mult * atr_15m[i]
            if risk_dist <= 0: i += 1; continue
            
            sl_px = entry_px + risk_dist
            tp_px = entry_px - (rr_target * risk_dist)
            
            outcome = None
            m1_held = 0
            for m_idx in range(m1_entry_idx, min(n_1m, m1_entry_idx + 720)):
                m1_held += 1
                if m1_highs[m_idx] >= sl_px: outcome = "LOSS"; break
                if m1_lows[m_idx] <= tp_px: outcome = "WIN"; break
                
            if outcome is None:
                pnl_r = (entry_px - m1_closes[min(n_1m-1, m1_entry_idx + 719)]) / risk_dist
                outcome = "WIN" if pnl_r > 0 else "LOSS"
            else:
                pnl_r = rr_target if outcome == "WIN" else -1.0
                
            trades.append(pnl_r)
            i += max(1, m1_held // 15)
            
    total = len(trades)
    if total == 0: return "0 trades"
    wins = len([t for t in trades if t > 0])
    wr = (wins / total) * 100.0
    net_r = sum(trades)
    return f"Trades: {total:3d} | WinRate: {wr:5.1f}% | Net: {net_r:+6.1f} R"

print("=== 15M SL MULTIPLIER & RR SWEEP (AT 1-MINUTE PRECISION) ===")
for sl_mult in [1.0, 1.5, 2.0, 2.5]:
    for rr in [1.0, 1.5, 2.0, 2.5]:
        print(f"SL: {sl_mult:3.1f}x ATR | RR: 1:{rr:3.1f} -> {run_deep_15m(sl_atr_mult=sl_mult, rr_target=rr)}")
