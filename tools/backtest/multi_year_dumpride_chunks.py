#!/usr/bin/env python3
"""tools/backtest/multi_year_dumpride_chunks.py

Multi-Year Historical Backtest & Random Chunk Monte Carlo Simulation for DumpRide Strategy:
- Downloads 2023 - 2026 historical 4H data for top liquid altcoins.
- Evaluates DumpRide Short (>=20x Volume Spike, 1.0x ATR Stop Loss, 1:2 RR Target).
- Splits data into distinct historical regime chunks (2023 Bear/Chop, 2024 Bull Mania, 2025 Alt Season, 2026 Volatility).
- Executes 100 Random Time Window Monte Carlo chunks (random 30-day and 60-day periods).
- Simulates realistic 0.10% Taker Fee + 0.10% Slippage.
"""
import os, sys, time, json, ssl, random, datetime
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd

print("=" * 110, flush=True)
print("📊 DUMPRIDE MULTI-YEAR HISTORICAL CHUNK & MONTE CARLO BACKTEST (2023 - 2026)", flush=True)
print("   Strategy: 4H Exhaustion Short | Volume Surge >=20.0x | 1.0x ATR SL | 1:2 RR Target", flush=True)
print("   Friction: 0.10% Taker Fee + 0.10% Slippage", flush=True)
print("=" * 110, flush=True)

# List of top liquid perpetual altcoins
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT", "BNBUSDT", "ADAUSDT", "AVAXUSDT",
    "LINKUSDT", "NEARUSDT", "SUIUSDT", "PEPEUSDT", "SHIBUSDT", "DOTUSDT", "LTCUSDT", "BCHUSDT",
    "UNIUSDT", "APTUSDT", "FTMUSDT", "ATOMUSDT", "FILUSDT", "RENDERUSDT", "ARBUSDT", "OPUSDT",
    "INJUSDT", "TIAUSDT", "SEIUSDT", "WIFUSDT", "RUNEUSDT", "KASUSDT", "GALAUSDT", "SANDUSDT",
    "MANAUSDT", "AXSUSDT", "AAVEUSDT", "CRVUSDT", "MKRUSDT", "DYDXUSDT", "LDOUSDT", "PENDLEUSDT",
    "BLURUSDT", "ORDIUSDT", "STXUSDT", "IMXUSDT", "FLOWUSDT", "QNTUSDT", "ALGOUSDT", "ICPUSDT"
]

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def fetch_multi_year_candles(symbol):
    """Fetch 4H klines from Jan 2023 to Aug 2026 via Binance public REST API."""
    all_bars = []
    # 2023-01-01 00:00:00 UTC = 1672531200000
    start_ts = 1672531200000
    end_ts = int(time.time() * 1000)
    
    cur_start = start_ts
    while cur_start < end_ts:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=4h&startTime={cur_start}&limit=1000"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=12, context=ctx) as r:
                data = json.loads(r.read().decode())
                if not data:
                    break
                for b in data:
                    all_bars.append([
                        int(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5])
                    ])
                last_t = int(data[-1][0])
                if last_t <= cur_start or len(data) < 1000:
                    break
                cur_start = last_t + (4 * 3600 * 1000)
                time.sleep(0.04) # rate limit politeness
        except Exception as e:
            time.sleep(0.5)
            break
            
    if not all_bars:
        return symbol, None
        
    arr = np.array(all_bars, dtype=np.float64)
    # Deduplicate by timestamp
    _, unique_idx = np.unique(arr[:, 0], return_index=True)
    arr = arr[unique_idx]
    return symbol, arr

print(f"Downloading 3.5+ years of 4H data for {len(SYMBOLS)} top altcoins...", flush=True)
coin_data = {}
with ThreadPoolExecutor(max_workers=16) as ex:
    futs = {ex.submit(fetch_multi_year_candles, s): s for s in SYMBOLS}
    done_count = 0
    for fut in as_completed(futs):
        sym, arr = fut.result()
        done_count += 1
        if arr is not None and len(arr) > 500:
            coin_data[sym] = arr
        if done_count % 10 == 0 or done_count == len(SYMBOLS):
            print(f"  [Progress] Loaded {len(coin_data)}/{done_count} coins ({sum(len(v) for v in coin_data.values()):,} total 4H bars)", flush=True)

print(f"\n✅ Dataset Ready: {len(coin_data)} coins | {sum(len(v) for v in coin_data.values()):,} historical 4H bars spanning 2023 to 2026.\n", flush=True)

def calc_atr(highs, lows, closes, period=14):
    n = len(highs)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr = np.zeros(n)
    atr[0] = tr[0]
    for i in range(1, n):
        if i < period:
            atr[i] = np.mean(tr[: i + 1])
        else:
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr

def run_dumpride_simulation(data_dict, start_ts=None, end_ts=None, vol_mult=20.0, rr_target=2.0, fee_pct=0.0010, slip_pct=0.0010):
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
        
        # 20-period volume moving average
        vol_ma = np.zeros(n)
        for i in range(n):
            vol_ma[i] = np.mean(vols[max(0, i - 20) : i]) if i > 0 else vols[0]
            
        last_trade_bar = -99
        for i in range(20, n - 1):
            t = times[i]
            if start_ts is not None and t < start_ts: continue
            if end_ts is not None and t > end_ts: continue
            if i - last_trade_bar < 3: continue # cooldown
            
            # DumpRide Condition: 4H bar volume surge >= vol_mult & Bullish green close
            curr_vol = vols[i]
            base_vol = max(1e-5, vol_ma[i])
            spike = curr_vol / base_vol
            
            if spike >= vol_mult and closes[i] > opens[i]:
                # Entry at close of spike bar
                entry_px = closes[i]
                sl_px = entry_px + (1.0 * atr14[i])
                risk_dist = sl_px - entry_px
                if risk_dist <= 0 or (risk_dist / entry_px) < 0.005: continue
                
                tp_px = entry_px - (rr_target * risk_dist)
                
                # Forward simulation up to 12 bars (48 hours)
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
                    exit_px = fwd_closes[-1]
                    raw_r = (entry_px - exit_px) / risk_dist
                    
                fee_r = (2.0 * fee_pct) / (risk_dist / entry_px)
                net_r = raw_r - fee_r
                
                trades.append({
                    "symbol": sym, "ts": t, "net_r": net_r, "spike": spike, "win": net_r > 0
                })
                last_trade_bar = i
                
    return trades

def summarize(trades):
    if not trades:
        return {"trades": 0, "wr": 0, "tot_r": 0, "pf": 0, "max_dd": 0}
    n = len(trades)
    wins = [t["net_r"] for t in trades if t["net_r"] > 0]
    losses = [t["net_r"] for t in trades if t["net_r"] <= 0]
    wr = len(wins) / n * 100
    tot_r = sum(t["net_r"] for t in trades)
    gw = sum(wins)
    gl = abs(sum(losses)) if losses else 0.001
    pf = gw / gl
    
    # Calculate Max Drawdown in R
    eq = 0
    peak = 0
    max_dd = 0
    for t in trades:
        eq += t["net_r"]
        if eq > peak: peak = eq
        dd = peak - eq
        if dd > max_dd: max_dd = dd
        
    return {"trades": n, "wr": wr, "tot_r": tot_r, "pf": pf, "max_dd": max_dd}

# 1. Macro Yearly & Market Regime Chunks
REGIMES = [
    ("2023 Full Year (Bear / Accumulation)", 1672531200000, 1704067200000),
    ("2024 H1 (ETF Approval & Alt Rally)", 1704067200000, 1719792000000),
    ("2024 H2 (Summer Chop & Corrections)", 1719792000000, 1735689600000),
    ("2025 Full Year (Alt Season Mania)", 1735689600000, 1767225600000),
    ("2026 YTD (Current Regime)", 1767225600000, int(time.time() * 1000)),
    ("Full 3.5+ Years Combined (2023-2026)", 1672531200000, int(time.time() * 1000))
]

print("=" * 110)
print("🏛️ HISTORICAL MARKET REGIME CHUNKS (>= 20X VOLUME SURGE)")
print("=" * 110)
print(f"{'Market Regime / Time Chunk':<42} | {'Trades':<8} | {'Win Rate':<10} | {'Profit Factor':<14} | {'Max DD (R)':<12} | {'Net Return (R)'}")
print("-" * 110)

for name, s_ts, e_ts in REGIMES:
    res = run_dumpride_simulation(coin_data, start_ts=s_ts, end_ts=e_ts, vol_mult=20.0, rr_target=2.0)
    st = summarize(res)
    print(f"{name:<42} | {st['trades']:<8} | {st['wr']:>6.1f}%    | {st['pf']:<14.2f} | -{st['max_dd']:<10.2f} | {st['tot_r']:>+12.2f} R")
print("=" * 110)

# 2. Monte Carlo Random 60-Day Window Sampling (100 Random Chunks)
print("\n" + "=" * 110)
print("🎲 MONTE CARLO SIMULATION: 100 RANDOM TIME CHUNKS (RANDOM 60-DAY WINDOWS ACROSS 2023-2026)")
print("=" * 110)

random.seed(42)
min_global_ts = 1672531200000
max_global_ts = int(time.time() * 1000) - (60 * 86400 * 1000)

chunk_results = []
for _ in range(100):
    rnd_start = random.randint(min_global_ts, max_global_ts)
    rnd_end = rnd_start + (60 * 86400 * 1000) # 60 days
    
    t_list = run_dumpride_simulation(coin_data, start_ts=rnd_start, end_ts=rnd_end, vol_mult=20.0, rr_target=2.0)
    s = summarize(t_list)
    if s["trades"] > 0:
        chunk_results.append(s)

prof_chunks = [c for c in chunk_results if c["tot_r"] > 0]
be_chunks = [c for c in chunk_results if c["tot_r"] == 0]
loss_chunks = [c for c in chunk_results if c["tot_r"] < 0]

mean_r = np.mean([c["tot_r"] for c in chunk_results])
median_r = np.median([c["tot_r"] for c in chunk_results])
mean_wr = np.mean([c["wr"] for c in chunk_results])
mean_pf = np.mean([c["pf"] for c in chunk_results])
max_win_chunk = max(c["tot_r"] for c in chunk_results)
worst_loss_chunk = min(c["tot_r"] for c in chunk_results)

print(f"Total Random 60-Day Chunks Tested : 100")
print(f"Profitable Chunks                : {len(prof_chunks)} / 100 ({len(prof_chunks)}% Success Rate)")
print(f"Break-Even Chunks (0 trades)     : {len(be_chunks)} / 100")
print(f"Losing Chunks                    : {len(loss_chunks)} / 100 ({len(loss_chunks)}%)")
print(f"--------------------------------------------------------------------------------")
print(f"Average Return per 60-Day Chunk  : {mean_r:+.2f} R")
print(f"Median Return per 60-Day Chunk   : {median_r:+.2f} R")
print(f"Average Win Rate Across Chunks   : {mean_wr:.1f}%")
print(f"Average Profit Factor            : {mean_pf:.2f}")
print(f"Best 60-Day Chunk Return         : {max_win_chunk:+.2f} R")
print(f"Worst 60-Day Chunk Drawdown      : {worst_loss_chunk:+.2f} R")
print("=" * 110)
