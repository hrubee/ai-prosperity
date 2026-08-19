import urllib.request
import json
import time
import os
import ssl
import numpy as np
from concurrent.futures import ThreadPoolExecutor

ssl_ctx = ssl._create_unverified_context()

print("=========================================================================================")
print("🧪 15-MINUTE DUMPRIDE BACKTEST WITH 1-MINUTE ULTRA-HIGH RESOLUTION EXECUTION")
print("=========================================================================================")

UNIVERSE = [
    "BTC", "ETH", "SOL", "DOGE", "AVAX", "NEAR", "SUI", "1000PEPE", "WIF", "LINK", "ARB", "OP"
]

CANDLES_PER_SYMBOL = 4500 # ~75 hours of 1-minute data per symbol = 54,000 1m candles total

def fetch_1m_data_paginated(symbol, target_candles=4500):
    all_rows = []
    end_time = int(time.time() * 1000)
    
    # Binance limit is 1500 per call, so 3 calls fetch 4500 1m candles
    for _ in range(target_candles // 1500):
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}USDT&interval=1m&limit=1500&endTime={end_time}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            res = json.load(urllib.request.urlopen(req, context=ssl_ctx, timeout=10))
            if not isinstance(res, list) or len(res) == 0:
                break
            all_rows = res + all_rows
            end_time = int(res[0][0]) - 1 # Page backwards in time
            time.sleep(0.1)
        except Exception:
            break
            
    # Deduplicate and sort ascending
    seen = set()
    deduped = []
    for r in all_rows:
        if r[0] not in seen:
            seen.add(r[0])
            deduped.append(r)
    deduped.sort(key=lambda r: r[0])
    return symbol, deduped

print(f"📡 Fetching {CANDLES_PER_SYMBOL:,} 1-minute candles across {len(UNIVERSE)} symbols ({len(UNIVERSE)*CANDLES_PER_SYMBOL:,} total 1m candles)...")
t0 = time.time()
DATA_1M = {}
with ThreadPoolExecutor(max_workers=6) as pool:
    results = pool.map(fetch_1m_data_paginated, UNIVERSE)
    for sym, rows in results:
        if rows and len(rows) > 500:
            DATA_1M[sym] = rows

total_1m = sum(len(r) for r in DATA_1M.values())
print(f"✅ Downloaded and cached {total_1m:,} 1-minute candles in {time.time()-t0:.1f}s!\n")

def run_15m_backtest_1m_precision(min_vol_mult=3.5, min_wick_ratio=0.20, max_pump_pct=2.0, min_pump_pct=0.0, min_15m_notional=75000.0, rr_target=2.0, sl_atr_mult=1.0):
    all_trades = []
    
    for sym, raw_1m in DATA_1M.items():
        # Parse 1m candles
        m1_times = [int(r[0]) for r in raw_1m]
        m1_opens = np.array([float(r[1]) for r in raw_1m])
        m1_highs = np.array([float(r[2]) for r in raw_1m])
        m1_lows = np.array([float(r[3]) for r in raw_1m])
        m1_closes = np.array([float(r[4]) for r in raw_1m])
        m1_vols = np.array([float(r[5]) for r in raw_1m])
        m1_usdt = np.array([float(r[7]) for r in raw_1m])
        
        n_1m = len(raw_1m)
        
        # Aggregate 15 1m candles into 15m bars aligned on 15m boundaries (00, 15, 30, 45)
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
                "open": m1_opens[idx],
                "high": m1_highs[idx],
                "low": m1_lows[idx],
                "close": m1_closes[idx],
                "vol": m1_vols[idx],
                "usdt": m1_usdt[idx]
            })
            
        n_15m = len(bars_15m)
        if n_15m < 30: continue
        
        # Compute 15m ATR(14)
        tr = np.zeros(n_15m)
        tr[0] = bars_15m[0]["high"] - bars_15m[0]["low"]
        for i in range(1, n_15m):
            h, l, prev_c = bars_15m[i]["high"], bars_15m[i]["low"], bars_15m[i-1]["close"]
            tr[i] = max(h - l, abs(h - prev_c), abs(l - prev_c))
        atr_15m = np.zeros(n_15m)
        for i in range(13, n_15m):
            atr_15m[i] = np.mean(tr[i-13:i+1])
            
        i = 25
        while i < n_15m - 10:
            b = bars_15m[i]
            c_open, c_high, c_low, c_close = b["open"], b["high"], b["low"], b["close"]
            c_vol, c_usdt = b["vol"], b["usdt_vol"]
            
            # Condition 1: Green candle
            if c_close <= c_open:
                i += 1; continue
                
            pump_pct = ((c_close - c_open) / c_open) * 100.0
            if pump_pct < min_pump_pct or pump_pct > max_pump_pct:
                i += 1; continue
                
            # Condition 2: Upper Wick Rejection (Whale Absorption)
            c_range = c_high - c_low
            if c_range <= 0:
                i += 1; continue
            upper_wick = c_high - max(c_open, c_close)
            wick_ratio = upper_wick / c_range
            if wick_ratio < min_wick_ratio:
                i += 1; continue
                
            # Condition 3: 20-period baseline volume
            base_vol = np.mean([bars_15m[k]["vol"] for k in range(i-20, i)])
            base_usdt = np.mean([bars_15m[k]["usdt_vol"] for k in range(i-20, i)])
            if base_vol <= 0 or base_usdt < 15000.0:
                i += 1; continue
                
            vol_mult = c_vol / base_vol
            if vol_mult < min_vol_mult or c_usdt < min_15m_notional:
                i += 1; continue
                
            # Entry on the very next 1-minute candle at exact close of 15m bar
            m1_entry_idx = b["end_1m_idx"] + 1
            if m1_entry_idx >= n_1m: break
            
            entry_px = m1_opens[m1_entry_idx]
            risk_dist = sl_atr_mult * atr_15m[i]
            if risk_dist <= 0: i += 1; continue
            
            sl_px = entry_px + risk_dist
            tp_px = entry_px - (rr_target * risk_dist)
            
            # 1-MINUTE PRECISION EVALUATION (Tick-by-tick up to 12 hours / 720 1m candles)
            outcome = None
            m1_held = 0
            for m_idx in range(m1_entry_idx, min(n_1m, m1_entry_idx + 720)):
                m1_held += 1
                m_high = m1_highs[m_idx]
                m_low = m1_lows[m_idx]
                
                sl_hit = (m_high >= sl_px)
                tp_hit = (m_low <= tp_px)
                
                if sl_hit and tp_hit:
                    # Same 1-minute collision: assume conservative loss
                    outcome = "LOSS"
                    break
                elif sl_hit:
                    outcome = "LOSS"
                    break
                elif tp_hit:
                    outcome = "WIN"
                    break
                    
            if outcome is None:
                exit_px = m1_closes[min(n_1m-1, m1_entry_idx + 719)]
                pnl_r = (entry_px - exit_px) / risk_dist
                outcome = "WIN" if pnl_r > 0 else "LOSS"
            else:
                pnl_r = rr_target if outcome == "WIN" else -1.0
                
            all_trades.append({
                "symbol": sym,
                "entry_time": m1_times[m1_entry_idx],
                "pump_pct": pump_pct,
                "vol_mult": vol_mult,
                "wick_ratio": wick_ratio,
                "entry_px": entry_px,
                "sl_px": sl_px,
                "tp_px": tp_px,
                "outcome": outcome,
                "pnl_r": pnl_r,
                "m1_held": m1_held
            })
            
            # Advance 15m index by the duration of the trade
            bars_held_15m = max(1, m1_held // 15)
            i += bars_held_15m
            
    return all_trades

def print_metrics(label, trades):
    if not trades:
        print(f"[{label}] No trades generated.\n")
        return
    total = len(trades)
    wins = len([t for t in trades if t["outcome"] == "WIN"])
    losses = total - wins
    win_rate = (wins / total) * 100.0
    net_r = sum(t["pnl_r"] for t in trades)
    gross_win_r = sum(t["pnl_r"] for t in trades if t["pnl_r"] > 0)
    gross_loss_r = abs(sum(t["pnl_r"] for t in trades if t["pnl_r"] < 0))
    profit_factor = (gross_win_r / gross_loss_r) if gross_loss_r > 0 else float("inf")
    
    equity_curve = [0.0]
    for t in trades:
        equity_curve.append(equity_curve[-1] + t["pnl_r"])
    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak: peak = eq
        dd = peak - eq
        if dd > max_dd: max_dd = dd
        
    avg_minutes = np.mean([t["m1_held"] for t in trades])
    
    print(f"=========================================================================================")
    print(f"📊 {label}")
    print(f"=========================================================================================")
    print(f"  • Total Trades       : {total}")
    print(f"  • Win / Loss         : {wins} Wins / {losses} Losses")
    print(f"  • Win Rate           : {win_rate:.2f}% (Break-even needed: 33.3%)")
    print(f"  • Net Return (R)     : {'+' if net_r > 0 else ''}{net_r:.2f} R")
    print(f"  • Profit Factor      : {profit_factor:.2f}")
    print(f"  • Max Drawdown (R)   : -{max_dd:.2f} R")
    print(f"  • Avg Hold Time      : {avg_minutes:.1f} minutes ({avg_minutes/60:.1f} hours)")
    print(f"  • Return / Drawdown  : {(net_r / max_dd):.2f}x" if max_dd > 0 else "N/A")
    print()

# Configurations to test on 15m timeframe with 1m execution:
print("🔬 TESTING 15M TIMEFRAME CONFIGURATIONS AT 1-MINUTE PRECISION:")
t_std = run_15m_backtest_1m_precision(min_vol_mult=3.5, min_wick_ratio=0.20, max_pump_pct=2.0, min_pump_pct=0.0, rr_target=2.0)
t_strict = run_15m_backtest_1m_precision(min_vol_mult=4.0, min_wick_ratio=0.30, max_pump_pct=1.5, min_pump_pct=0.0, rr_target=2.0)
t_high_rr = run_15m_backtest_1m_precision(min_vol_mult=3.5, min_wick_ratio=0.25, max_pump_pct=2.0, min_pump_pct=0.0, rr_target=2.5)
t_scalp = run_15m_backtest_1m_precision(min_vol_mult=3.0, min_wick_ratio=0.20, max_pump_pct=2.0, min_pump_pct=0.0, rr_target=1.5)

print_metrics("1. 15M STANDARD ABSORPTION (3.5x Surge, >=20% Upper Wick, 1:2.0 RR)", t_std)
print_metrics("2. 15M STRICT ABSORPTION (4.0x Surge, >=30% Upper Wick, 1:2.0 RR)", t_strict)
print_metrics("3. 15M HIGH REWARD (3.5x Surge, >=25% Upper Wick, 1:2.5 RR)", t_high_rr)
print_metrics("4. 15M QUICK SCALP (3.0x Surge, >=20% Upper Wick, 1:1.5 RR)", t_scalp)
print("=========================================================================================")
