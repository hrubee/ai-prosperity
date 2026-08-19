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
print("🧪 COMPREHENSIVE QUANTITATIVE ANALYSIS: VOL2B2T RECLAIM STRATEGY")
print("=========================================================================================")

UNIVERSE = [
    "BTC", "ETH", "SOL", "DOGE", "NEAR", "AVAX", "SUI", "APT", "INJ", 
    "RENDER", "FET", "LINK", "ARB", "OP", "PEPE", "WIF", "SHIB", "GALA",
    "MATIC", "LDO", "TIA", "SEI", "FTM", "RUNE", "AAVE", "UNI", "FIL",
    "1000PEPE", "1000SHIB", "1000BONK", "1000FLOKI", "NOT", "PEOPLE",
    "JASMY", "ORDI", "KAS", "SATS", "STX", "BOME", "MEME", "WLD"
]

def fetch_15m_klines(symbol, limit=1500):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}USDT&interval=15m&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        rows = json.load(urllib.request.urlopen(req, context=ssl_ctx, timeout=8))
        if isinstance(rows, list) and len(rows) > 500:
            return symbol, rows
    except Exception:
        pass
    return symbol, None

print(f"📡 Fetching 1,500 15m candles across {len(UNIVERSE)} symbols (60,000 total 15m candles)...")
t0 = time.time()
CACHED_15M = {}
with ThreadPoolExecutor(max_workers=20) as pool:
    results = pool.map(fetch_15m_klines, UNIVERSE)
    for sym, data in results:
        if data:
            CACHED_15M[sym] = data

print(f"✅ Downloaded and cached {len(CACHED_15M)} perpetual pairs ({sum(len(r) for r in CACHED_15M.values()):,} candles) in {time.time()-t0:.1f}s!\n")

def run_vol2b2t_simulation(
    vol_mult=5.0,
    min_dump_pct=-0.025, # -2.5% dump
    vol_lookback=20, # or 100
    reclaim_window_bars=12, # within 12 bars (3 hours)
    rr_target=2.0,
    sl_type="sweep_low", # 'sweep_low' or 'atr'
    min_candle_notional=100000.0
):
    trades = []
    
    for sym, klines in CACHED_15M.items():
        n = len(klines)
        if n < vol_lookback + 50: continue
        
        times = [int(r[0]) for r in klines]
        opens = np.array([float(r[1]) for r in klines])
        highs = np.array([float(r[2]) for r in klines])
        lows = np.array([float(r[3]) for r in klines])
        closes = np.array([float(r[4]) for r in klines])
        vols = np.array([float(r[5]) for r in klines])
        usdt_vols = np.array([float(r[7]) for r in klines])
        
        # Calculate ATR(14)
        tr = np.zeros(n)
        for i in range(1, n):
            tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        atr = np.zeros(n)
        for i in range(13, n):
            atr[i] = np.mean(tr[i-13:i+1])
            
        i = vol_lookback
        while i < n - 40:
            # 1. Check Volume Dump Signal
            c_open = opens[i]
            c_close = closes[i]
            c_low = lows[i]
            c_vol = vols[i]
            c_usdt = usdt_vols[i]
            
            # Must be a red dump candle
            dump_pct = (c_close - c_open) / c_open
            if dump_pct > min_dump_pct: # Not red enough
                i += 1; continue
                
            base_vol = np.mean(vols[i-vol_lookback:i])
            if base_vol <= 0 or c_usdt < min_candle_notional:
                i += 1; continue
                
            mult = c_vol / base_vol
            if mult < vol_mult:
                i += 1; continue
                
            spike_low = c_low
            spike_idx = i
            
            # 2. Look forward in the next reclaim_window_bars for 2B Reclaim
            reclaim_found = False
            reclaim_idx = -1
            
            for f in range(spike_idx + 1, min(n - 24, spike_idx + 1 + reclaim_window_bars)):
                # Sweep below spike_low and close back ABOVE spike_low
                if lows[f] < spike_low and closes[f] > spike_low:
                    reclaim_found = True
                    reclaim_idx = f
                    break
                    
            if not reclaim_found:
                i += 1
                continue
                
            # 3. Enter LONG at close of reclaim bar
            entry_px = closes[reclaim_idx]
            
            if sl_type == "sweep_low":
                sl_px = lows[reclaim_idx] * 0.999 # Just below sweep low
                risk_dist = entry_px - sl_px
            else:
                risk_dist = 1.2 * atr[reclaim_idx]
                sl_px = entry_px - risk_dist
                
            if risk_dist <= 0 or (risk_dist / entry_px) < 0.002: # Risk too small or zero
                i = reclaim_idx + 1
                continue
                
            tp_px = entry_px + (rr_target * risk_dist)
            
            # 4. Simulate trade execution over forward bars
            outcome = None
            bars_held = 0
            for t in range(reclaim_idx + 1, min(n, reclaim_idx + 48)): # Max 12 hours hold
                bars_held += 1
                t_high = highs[t]
                t_low = lows[t]
                
                sl_hit = (t_low <= sl_px)
                tp_hit = (t_high >= tp_px)
                
                if sl_hit and tp_hit:
                    outcome = "LOSS"
                    break
                elif sl_hit:
                    outcome = "LOSS"
                    break
                elif tp_hit:
                    outcome = "WIN"
                    break
                    
            if outcome is None:
                exit_px = closes[min(n-1, reclaim_idx + 47)]
                pnl_r = (exit_px - entry_px) / risk_dist
                outcome = "WIN" if pnl_r > 0 else "LOSS"
            else:
                pnl_r = rr_target if outcome == "WIN" else -1.0
                
            trades.append({
                "symbol": sym,
                "spike_time": times[spike_idx],
                "entry_time": times[reclaim_idx],
                "dump_pct": dump_pct * 100.0,
                "vol_mult": mult,
                "entry_px": entry_px,
                "sl_px": sl_px,
                "tp_px": tp_px,
                "outcome": outcome,
                "pnl_r": pnl_r,
                "bars_held": bars_held
            })
            
            i = reclaim_idx + max(1, bars_held)
            
    return trades

def summarize_trades(label, trades):
    if not trades:
        return f"{label:<55} -> 0 trades"
    total = len(trades)
    wins = len([t for t in trades if t["outcome"] == "WIN"])
    losses = total - wins
    wr = (wins / total) * 100.0
    net_r = sum(t["pnl_r"] for t in trades)
    gross_win = sum(t["pnl_r"] for t in trades if t["pnl_r"] > 0)
    gross_loss = abs(sum(t["pnl_r"] for t in trades if t["pnl_r"] < 0))
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    return f"{label:<55} -> Trades: {total:3d} | WinRate: {wr:5.1f}% | Net: {net_r:+6.1f} R | PF: {pf:4.2f}"

print("=== 1. SWEEPING VOLUME SPIKE THRESHOLD (15m, -2.5% Dump, 1:2.0 RR) ===")
for vm in [2.5, 3.5, 5.0, 7.5, 10.0]:
    t = run_vol2b2t_simulation(vol_mult=vm, min_dump_pct=-0.025, rr_target=2.0)
    print(summarize_trades(f"Vol Multiplier >= {vm:4.1f}x", t))

print("\n=== 2. SWEEPING MINIMUM DUMP THRESHOLD (5.0x Vol, 1:2.0 RR) ===")
for dp in [-0.015, -0.025, -0.035, -0.050]:
    t = run_vol2b2t_simulation(vol_mult=5.0, min_dump_pct=dp, rr_target=2.0)
    print(summarize_trades(f"Dump Candle Drop <= {dp*100:4.1f}%", t))

print("\n=== 3. SWEEPING RECLAIM WINDOW (Time to Sweep Low) ===")
for w in [3, 6, 12, 24]:
    t = run_vol2b2t_simulation(vol_mult=4.0, min_dump_pct=-0.02, reclaim_window_bars=w, rr_target=2.0)
    print(summarize_trades(f"Reclaim Window <= {w:2d} bars ({w*15:3d} mins)", t))

print("\n=== 4. SWEEPING RISK-TO-REWARD RATIOS (4.0x Vol, -2.0% Dump) ===")
for rr in [1.0, 1.5, 2.0, 2.5, 3.0]:
    t = run_vol2b2t_simulation(vol_mult=4.0, min_dump_pct=-0.02, rr_target=rr)
    print(summarize_trades(f"Take Profit Target 1:{rr:3.1f} RR", t))

print("\n=== 5. STOP LOSS METHODOLOGY: SWEEP LOW VS ATR BUFFER ===")
t_sweep = run_vol2b2t_simulation(vol_mult=4.0, min_dump_pct=-0.02, sl_type="sweep_low", rr_target=2.0)
t_atr = run_vol2b2t_simulation(vol_mult=4.0, min_dump_pct=-0.02, sl_type="atr", rr_target=2.0)
print(summarize_trades("SL = Sweep Low Wick (Tight)", t_sweep))
print(summarize_trades("SL = 1.2x ATR Buffer (Wide)", t_atr))

print("=========================================================================================")
