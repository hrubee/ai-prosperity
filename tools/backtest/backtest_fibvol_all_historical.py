#!/usr/bin/env python3
import sys, json, time, urllib.request, os
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np

def fetch_pairs():
    url = "https://public.coindcx.com/market_data/v3/current_prices/futures/rt"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        raw = json.loads(urllib.request.urlopen(req).read().decode())
        prices_dict = raw.get("prices", {})
        pairs = []
        for p in prices_dict.keys():
            if p.startswith("B-") and p.endswith("_USDT"):
                base = p[2:-5]
                pairs.append(base)
        return sorted(list(set(pairs)))
    except Exception as e:
        print("Error fetching pairs:", e)
        return []

def fetch_klines(sym, interval="15m", limit=1000):
    url = f"https://public.coindcx.com/market_data/candles?pair=B-{sym}_USDT&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        raw = json.loads(urllib.request.urlopen(req).read().decode())
        if isinstance(raw, list) and len(raw) > 0:
            return sym, sorted(raw, key=lambda c: float(c.get('time', 0)))
    except Exception:
        pass
    return sym, []

def run_backtest_for_config(all_market_data, entry_fib, sl_fib, rr_ratio=5.0, trail_act_r=2.0, trail_dist_r=1.0):
    total_trades = 0
    wins = 0
    losses = 0
    total_pnl_usd = 0.0
    trade_pnls = []
    
    for sym, candles in all_market_data.items():
        if len(candles) < 50:
            continue
            
        opens = np.array([float(c.get('open', 0)) for c in candles])
        highs = np.array([float(c.get('high', 0)) for c in candles])
        lows = np.array([float(c.get('low', 0)) for c in candles])
        closes = np.array([float(c.get('close', 0)) for c in candles])
        vols = np.array([float(c.get('volume', 0)) for c in candles])
        
        n = len(closes)
        in_trade = False
        trade_fill = 0.0
        trade_sl = 0.0
        trade_tp = 0.0
        trade_qty = 0.0
        trade_risk = 0.0
        peak_r = 0.0
        trail_sl = 0.0
        
        for i in range(40, n):
            # Check open trade exit conditions
            if in_trade:
                c_high = highs[i]
                c_low = lows[i]
                
                curr_r = (c_high - trade_fill) / trade_risk if trade_risk > 0 else 0.0
                if curr_r > peak_r:
                    peak_r = curr_r
                    
                if peak_r >= trail_act_r:
                    new_t = trade_fill + ((peak_r - trail_dist_r) * trade_risk)
                    if new_t > trail_sl:
                        trail_sl = new_t
                        
                # Exit SL
                if c_low <= trail_sl:
                    exit_px = trail_sl
                    pnl = (exit_px - trade_fill) * trade_qty
                    total_pnl_usd += pnl
                    trade_pnls.append(pnl)
                    if pnl > 0:
                        wins += 1
                    else:
                        losses += 1
                    total_trades += 1
                    in_trade = False
                    continue
                    
                # Exit TP
                if c_high >= trade_tp:
                    exit_px = trade_tp
                    pnl = (exit_px - trade_fill) * trade_qty
                    total_pnl_usd += pnl
                    trade_pnls.append(pnl)
                    wins += 1
                    total_trades += 1
                    in_trade = False
                    continue
                    
                continue
                
            # Scan for 15x volume spike
            hist_vol = vols[i-40:i]
            avg_vol = np.mean(hist_vol)
            if avg_vol <= 0:
                continue
                
            cur_v = vols[i]
            is_green = closes[i] > opens[i]
            vol_mult = cur_v / avg_vol
            
            if is_green and vol_mult >= 15.0:
                h = highs[i]
                l = lows[i]
                rng = h - l
                if rng <= 0:
                    continue
                    
                entry_px = h - (entry_fib * rng)
                sl_px = h - (sl_fib * rng)
                risk = entry_px - sl_px
                if risk <= 0:
                    continue
                    
                tp_px = entry_px + (rr_ratio * risk)
                
                # Check if next candle fills entry
                if i + 1 < n:
                    next_low = lows[i+1]
                    
                    if next_low <= sl_px:
                        # Skipped, dropped below SL
                        continue
                        
                    if next_low <= entry_px:
                        in_trade = True
                        trade_fill = entry_px
                        trade_sl = sl_px
                        trade_tp = tp_px
                        trade_risk = risk
                        risk_usd = 1.50 # $1.50 USD (1% risk of $150 USDT)
                        trade_qty = risk_usd / risk
                        peak_r = 0.0
                        trail_sl = sl_px
                        
    win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0
    profit_factor = (sum(p for p in trade_pnls if p > 0) / abs(sum(p for p in trade_pnls if p < 0))) if any(p < 0 for p in trade_pnls) else 999.0
    expectancy = (total_pnl_usd / total_trades) if total_trades > 0 else 0.0
    
    return {
        "trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "pnl_usd": total_pnl_usd,
        "pnl_inr": total_pnl_usd * 84.0,
        "profit_factor": profit_factor,
        "expectancy": expectancy
    }

print("=== FETCHING HISTORICAL CANDLE DATA ACROSS ALL COINDCX PAIRS (FAST MULTI-THREADED) ===")
pairs = fetch_pairs()
print(f"Total CoinDCX Perpetual Pairs Found: {len(pairs)}")

all_market_data = {}
with ThreadPoolExecutor(max_workers=25) as executor:
    futures = [executor.submit(fetch_klines, sym) for sym in pairs]
    completed = 0
    for f in as_completed(futures):
        sym, candles = f.result()
        if candles:
            all_market_data[sym] = candles
        completed += 1

print(f"Successfully downloaded 1000 15m candles for {len(all_market_data)} active pairs!")

print("\n=== COMPREHENSIVE HISTORICAL BACKTEST RESULTS ===")
configs = [
    {"name": "Baseline Engine (Entry 0.5 Fib / SL 0.6 Fib)", "entry": 0.50, "sl": 0.60},
    {"name": "User Proposed Wider SL (Entry 0.5 Fib / SL 0.7 Fib)", "entry": 0.50, "sl": 0.70},
    {"name": "Deeper Pullback (Entry 0.6 Fib / SL 0.7 Fib)", "entry": 0.60, "sl": 0.70},
    {"name": "Ultra-Wide SL (Entry 0.5 Fib / SL 0.8 Fib)", "entry": 0.50, "sl": 0.80}
]

print(f"{'Strategy Configuration':<52} | {'Trades':<7} | {'Win Rate':<9} | {'Net PnL ($)':<12} | {'Net PnL (INR)':<14} | {'Expectancy/Trade':<16} | {'Profit Factor':<13}")
print("-" * 135)

for c in configs:
    res = run_backtest_for_config(all_market_data, c["entry"], c["sl"])
    print(f"{c['name']:<52} | {res['trades']:<7} | {res['win_rate']:<8.1f}% | ${res['pnl_usd']:<11.2f} | ₹{res['pnl_inr']:<13.2f} | ${res['expectancy']:<15.2f} | {res['profit_factor']:<13.2f}")
