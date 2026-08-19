#!/usr/bin/env python3
"""tools/backtest/optimize_heikinashi.py — Filter & Parameter Explorer for Heikin-Ashi
Explores:
1. Pure Heikin-Ashi Wickless (Baseline 1:4 RR)
2. Trend Filter (EMA 50 / EMA 200 alignment)
3. Trend-Flip Filter (Only take 1st or 2nd candle of color change)
4. Risk-Reward Curve (1:1.5, 1:2, 1:3, 1:4)
"""
import os, sys, json, ssl, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pandas as pd

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

def fetch_candles_1h(pair: str, limit: int = 400):
    url = f"https://public.coindcx.com/market_data/candles?pair={pair}&interval=1h&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=6, context=ctx) as r:
            rows = json.loads(r.read().decode())
        if not isinstance(rows, list) or len(rows) < 80: return None
        rows = sorted(rows, key=lambda x: x["time"])
        return [[x["time"], float(x["open"]), float(x["high"]), float(x["low"]), float(x["close"]), float(x.get("volume", 0))] for x in rows]
    except Exception:
        return None

def run_sweep():
    # 1. Fetch Instruments
    url = "https://api.coindcx.com/exchange/v1/derivatives/futures/data/active_instruments"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
        coins = [p for p in json.loads(resp.read().decode()) if p.startswith("B-") and p.endswith("_USDT")]

    print(f"Loaded {len(coins)} CoinDCX instruments. Fetching 1H candle history in parallel...")
    with ThreadPoolExecutor(max_workers=30) as ex:
        raw_data = list(ex.map(lambda c: (c, fetch_candles_1h(c, 400)), coins))

    dataset = [(c, kl) for c, kl in raw_data if kl is not None and len(kl) >= 100]
    print(f"Successfully loaded {len(dataset)} valid historical series.\n")

    print("=" * 95)
    print("🔬 HEIKIN-ASHI PARAMETER & FILTER MATRIX EXPLORATION (1H Candlesticks)")
    print("=" * 95)
    print(f"{'RR Ratio':<10} | {'EMA Filter':<12} | {'Flip Filter':<12} | {'Trades':<8} | {'Win Rate':<10} | {'Net Return (R)':<16} | {'Expectancy (R)':<14} | {'PF':<6}")
    print("-" * 95)

    for rr in [1.5, 2.0, 2.5, 3.0, 4.0]:
        for use_ema in [False, True]:
            for flip_only in [False, True]:
                total_trades = 0
                wins = 0
                net_r = 0.0
                
                for p, kl in dataset:
                    n = len(kl)
                    times = np.array([k[0] for k in kl])
                    opens = np.array([k[1] for k in kl])
                    highs = np.array([k[2] for k in kl])
                    lows = np.array([k[3] for k in kl])
                    closes = np.array([k[4] for k in kl])
                    
                    # Indicators
                    ema50 = pd.Series(closes).ewm(span=50).mean().values
                    
                    # Heikin-Ashi
                    ha_c = (opens + highs + lows + closes) / 4.0
                    ha_o = np.zeros(n)
                    ha_o[0] = (opens[0] + closes[0]) / 2.0
                    for i in range(1, n):
                        ha_o[i] = (ha_o[i-1] + ha_c[i-1]) / 2.0
                    ha_h = np.maximum(highs, np.maximum(ha_o, ha_c))
                    ha_l = np.minimum(lows, np.minimum(ha_o, ha_c))
                    
                    in_trade_until = -1
                    for i in range(50, n - 2):
                        if i <= in_trade_until: continue
                        
                        is_green = ha_c[i] > ha_o[i]
                        is_red = ha_c[i] < ha_o[i]
                        
                        flat_bottom = is_green and np.isclose(ha_l[i], ha_o[i], rtol=0.0005)
                        flat_top = is_red and np.isclose(ha_h[i], ha_o[i], rtol=0.0005)
                        
                        if flip_only:
                            # Trigger bar must be 1st bar after opposite color
                            prev_was_red = ha_c[i-1] < ha_o[i-1]
                            prev_was_green = ha_c[i-1] > ha_o[i-1]
                            if flat_bottom and not prev_was_red: flat_bottom = False
                            if flat_top and not prev_was_green: flat_top = False
                            
                        if use_ema:
                            if flat_bottom and closes[i] < ema50[i]: flat_bottom = False
                            if flat_top and closes[i] > ema50[i]: flat_top = False
                            
                        # Long execution
                        if flat_bottom:
                            sl = lows[i]
                            entry = closes[i+1]
                            risk = entry - sl
                            if risk > 0 and (risk / entry) >= 0.003: # Min 0.3% risk
                                tp = entry + (rr * risk)
                                for j in range(i+2, min(n, i+80)):
                                    if lows[j] <= sl:
                                        total_trades += 1
                                        net_r -= 1.0
                                        in_trade_until = j
                                        break
                                    elif highs[j] >= tp:
                                        total_trades += 1
                                        wins += 1
                                        net_r += rr
                                        in_trade_until = j
                                        break
                                        
                        # Short execution
                        elif flat_top:
                            sl = highs[i]
                            entry = closes[i+1]
                            risk = sl - entry
                            if risk > 0 and (risk / entry) >= 0.003:
                                tp = entry - (rr * risk)
                                for j in range(i+2, min(n, i+80)):
                                    if highs[j] >= sl:
                                        total_trades += 1
                                        net_r -= 1.0
                                        in_trade_until = j
                                        break
                                    elif lows[j] <= tp:
                                        total_trades += 1
                                        wins += 1
                                        net_r += rr
                                        in_trade_until = j
                                        break
                
                wr = (wins / total_trades * 100) if total_trades > 0 else 0
                exp = (net_r / total_trades) if total_trades > 0 else 0
                pf = (wins * rr) / (total_trades - wins) if (total_trades - wins) > 0 else 0
                ema_lbl = "EMA 50" if use_ema else "None"
                flip_lbl = "Flip 1st Bar" if flip_only else "All Bars"
                print(f"1:{rr:<8} | {ema_lbl:<12} | {flip_lbl:<12} | {total_trades:<8,d} | {wr:<9.2f}% | {net_r:+15.1f} R | {exp:+13.3f} R | {pf:<5.2f}")

    print("=" * 95)

if __name__ == "__main__":
    run_sweep()
