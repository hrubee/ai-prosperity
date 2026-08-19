#!/usr/bin/env python3
"""tools/backtest/backtest_volatility_regimes.py — Volatility & Regime Selection for Heikin-Ashi
Explores:
1. ADX Trend Strength Filter (ADX >= 25, ADX >= 30 vs No ADX)
2. ATR Volatility Expansion Regime (ATR% >= 1.5%, ATR% >= 2.5%)
3. Bollinger Band Width (BBW) Breakout Expansion
4. Liquidity Tier Universe (Top 30, Top 50, Top 100 vs Full Universe)
5. Kaufman Efficiency Ratio (ER >= 0.35, high trend efficiency)
"""
import os, sys, json, ssl, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional
import numpy as np
import pandas as pd

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

def fetch_candles_1h(pair: str, limit: int = 500) -> Optional[List[List[float]]]:
    url = f"https://public.coindcx.com/market_data/candles?pair={pair}&interval=1h&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=6, context=ctx) as r:
            rows = json.loads(r.read().decode())
        if not isinstance(rows, list) or len(rows) < 100: return None
        rows = sorted(rows, key=lambda x: x["time"])
        return [[float(x["time"]), float(x["open"]), float(x["high"]), float(x["low"]), float(x["close"]), float(x.get("volume", 0))] for x in rows]
    except Exception:
        return None

def compute_adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    tr = np.zeros(n)

    for i in range(1, n):
        up_move = highs[i] - highs[i-1]
        down_move = lows[i-1] - lows[i]

        if up_move > down_move and up_move > 0:
            plus_dm[i] = up_move
        if down_move > up_move and down_move > 0:
            minus_dm[i] = down_move

        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))

    atr = pd.Series(tr).ewm(alpha=1.0/period, adjust=False).mean().values
    plus_di = 100.0 * pd.Series(plus_dm).ewm(alpha=1.0/period, adjust=False).mean().values / (atr + 1e-9)
    minus_di = 100.0 * pd.Series(minus_dm).ewm(alpha=1.0/period, adjust=False).mean().values / (atr + 1e-9)

    dx = 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)
    adx = pd.Series(dx).ewm(alpha=1.0/period, adjust=False).mean().values
    return adx

def compute_efficiency_ratio(closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    er = np.zeros(n)
    change = np.abs(closes[period:] - closes[:-period])
    volatility = pd.Series(np.abs(np.diff(closes))).rolling(period).sum().values[period-1:]
    er[period:] = change / (volatility + 1e-9)
    return er

def compute_heikin_ashi(opens, highs, lows, closes):
    n = len(closes)
    ha_close = (opens + highs + lows + closes) / 4.0
    ha_open = np.zeros(n)
    ha_open[0] = (opens[0] + closes[0]) / 2.0
    for i in range(1, n):
        ha_open[i] = (ha_open[i-1] + ha_close[i-1]) / 2.0
    ha_high = np.maximum(highs, np.maximum(ha_open, ha_close))
    ha_low = np.minimum(lows, np.minimum(ha_open, ha_close))
    return ha_open, ha_high, ha_low, ha_close

def evaluate_regime_combination(dataset, min_adx: float, min_atr_pct: float, min_er: float, top_n_coins: int = 999):
    active_set = dataset[:top_n_coins]
    
    total_trades = 0
    wins = 0
    bes = 0
    losses = 0
    net_r = 0.0
    
    for p, kl in active_set:
        n = len(kl)
        times = np.array([k[0] for k in kl])
        opens = np.array([k[1] for k in kl])
        highs = np.array([k[2] for k in kl])
        lows = np.array([k[3] for k in kl])
        closes = np.array([k[4] for k in kl])
        volumes = np.array([k[5] for k in kl])

        # Technical Indicators
        ema50 = pd.Series(closes).ewm(span=50).mean().values
        adx = compute_adx(highs, lows, closes, 14)
        er = compute_efficiency_ratio(closes, 14)

        tr = np.maximum(highs[1:] - lows[1:], np.maximum(abs(highs[1:] - closes[:-1]), abs(lows[1:] - closes[:-1])))
        atr = pd.Series(tr).rolling(14, min_periods=5).mean().values
        atr = np.insert(atr, 0, tr[:13].mean())
        atr_pct = (atr / (closes + 1e-9)) * 100.0

        ha_o, ha_h, ha_l, ha_c = compute_heikin_ashi(opens, highs, lows, closes)

        in_trade = -1
        consec_g = 0
        consec_r = 0

        for i in range(50, n - 2):
            if i <= in_trade: continue

            is_g = ha_c[i] > ha_o[i]
            is_r = ha_c[i] < ha_o[i]

            if is_g:
                consec_g += 1; consec_r = 0
            elif is_r:
                consec_r += 1; consec_g = 0
            else:
                consec_g = 0; consec_r = 0

            # Only 1st or 2nd bar
            if not ((is_g and 1 <= consec_g <= 2) or (is_r and 1 <= consec_r <= 2)):
                continue

            flat_b = is_g and np.isclose(ha_l[i], ha_o[i], rtol=0.0005)
            flat_t = is_r and np.isclose(ha_h[i], ha_o[i], rtol=0.0005)

            # REGIME FILTERS
            if min_adx > 0 and adx[i] < min_adx:
                continue
            if min_atr_pct > 0 and atr_pct[i] < min_atr_pct:
                continue
            if min_er > 0 and er[i] < min_er:
                continue

            curr_atr = atr[i] if (i < len(atr) and atr[i] > 0) else (closes[i] * 0.01)

            # LONG
            if flat_b and closes[i] > ema50[i]:
                entry = closes[i+1]
                raw_sl = min(lows[i], ha_l[i])
                sl_dist = max(entry - raw_sl, 0.8 * curr_atr)
                sl = entry - sl_dist
                risk = entry - sl
                if risk <= 0 or (risk/entry) < 0.003: continue

                tp = entry + (3.5 * risk)
                curr_sl = sl
                be_hit = False
                lock_hit = False

                for j in range(i+2, min(n, i+100)):
                    h_j = highs[j]
                    l_j = lows[j]

                    if not be_hit and h_j >= (entry + 1.5 * risk):
                        curr_sl = entry
                        be_hit = True
                    if not lock_hit and h_j >= (entry + 2.5 * risk):
                        curr_sl = entry + (1.0 * risk)
                        lock_hit = True

                    if l_j <= curr_sl:
                        r_pnl = -1.0 if not be_hit else (1.0 if lock_hit else 0.0)
                        total_trades += 1
                        net_r += r_pnl
                        if r_pnl > 0: wins += 1
                        elif r_pnl == 0: bes += 1
                        else: losses += 1
                        in_trade = j
                        break
                    elif h_j >= tp:
                        total_trades += 1
                        wins += 1
                        net_r += 3.5
                        in_trade = j
                        break

            # SHORT
            elif flat_t and closes[i] < ema50[i]:
                entry = closes[i+1]
                raw_sl = max(highs[i], ha_h[i])
                sl_dist = max(raw_sl - entry, 0.8 * curr_atr)
                sl = entry + sl_dist
                risk = sl - entry
                if risk <= 0 or (risk/entry) < 0.003: continue

                tp = entry - (3.5 * risk)
                curr_sl = sl
                be_hit = False
                lock_hit = False

                for j in range(i+2, min(n, i+100)):
                    h_j = highs[j]
                    l_j = lows[j]

                    if not be_hit and l_j <= (entry - 1.5 * risk):
                        curr_sl = entry
                        be_hit = True
                    if not lock_hit and l_j <= (entry - 2.5 * risk):
                        curr_sl = entry - (1.0 * risk)
                        lock_hit = True

                    if h_j >= curr_sl:
                        r_pnl = -1.0 if not be_hit else (1.0 if lock_hit else 0.0)
                        total_trades += 1
                        net_r += r_pnl
                        if r_pnl > 0: wins += 1
                        elif r_pnl == 0: bes += 1
                        else: losses += 1
                        in_trade = j
                        break
                    elif l_j <= tp:
                        total_trades += 1
                        wins += 1
                        net_r += 3.5
                        in_trade = j
                        break

    wr = (wins / total_trades * 100) if total_trades > 0 else 0.0
    be_r = (bes / total_trades * 100) if total_trades > 0 else 0.0
    loss_r = (losses / total_trades * 100) if total_trades > 0 else 0.0
    exp = (net_r / total_trades) if total_trades > 0 else 0.0
    gross_p = (wins * 3.5) # approximate
    pf = (gross_p / (losses + 1e-9)) if losses > 0 else 999.0
    return {
        "trades": total_trades,
        "wins": wins,
        "bes": bes,
        "losses": losses,
        "win_rate": wr,
        "be_rate": be_r,
        "loss_rate": loss_r,
        "net_r": net_r,
        "exp": exp,
        "pf": pf
    }

def main():
    print("=" * 100)
    print("🌊 VOLATILITY & MARKET REGIME SELECTION BACKTESTER (1H CANDLESTICKS)")
    print("=" * 100)

    # 1. Fetch Instruments
    url = "https://api.coindcx.com/exchange/v1/derivatives/futures/data/active_instruments"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
        coins = [p for p in json.loads(resp.read().decode()) if p.startswith("B-") and p.endswith("_USDT")]

    print(f"Loading 1H candle series for {len(coins)} coins...")
    with ThreadPoolExecutor(max_workers=30) as ex:
        raw_data = list(ex.map(lambda c: (c, fetch_candles_1h(c, 500)), coins))
    dataset = [(c, kl) for c, kl in raw_data if kl is not None and len(kl) >= 120]
    print(f"Loaded {len(dataset)} valid historical series.\n")

    test_matrix = [
        # Label, ADX, ATR%, ER, TopN
        ("Baseline (No Regime Filter)", 0, 0, 0, 999),
        ("ADX >= 25 (Active Trend)", 25, 0, 0, 999),
        ("ADX >= 30 (Strong Trend)", 30, 0, 0, 999),
        ("ADX >= 35 (Super Trend)", 35, 0, 0, 999),
        ("ATR% >= 1.5% (High Volatility)", 0, 1.5, 0, 999),
        ("ATR% >= 2.5% (Ultra High Volatility)", 0, 2.5, 0, 999),
        ("Efficiency Ratio >= 0.35 (Smooth Trend)", 0, 0, 0.35, 999),
        ("ADX >= 25 + ATR% >= 1.5%", 25, 1.5, 0, 999),
        ("ADX >= 30 + ATR% >= 2.0%", 30, 2.0, 0, 999),
        ("ADX >= 25 + ER >= 0.35", 25, 0, 0.35, 999),
        ("Top 50 Liquid Coins + ADX >= 25", 25, 0, 0, 50),
        ("Top 50 Liquid Coins + ADX >= 30 + ATR% >= 1.5%", 30, 1.5, 0, 50),
        ("Top 30 Liquid Coins + ADX >= 25", 25, 0, 0, 30),
    ]

    print(f"{'Regime Filter Configuration':<45} | {'Trades':<8} | {'Win Rate':<10} | {'BE Rate':<9} | {'Net Return':<14} | {'Expectancy':<12}")
    print("-" * 105)

    for label, min_adx, min_atr, min_er, top_n in test_matrix:
        res = evaluate_regime_combination(dataset, min_adx, min_atr, min_er, top_n)
        if res["trades"] > 0:
            print(f"{label:<45} | {res['trades']:<8,d} | {res['win_rate']:<9.2f}% | {res['be_rate']:<8.2f}% | {res['net_r']:+12.1f} R | {res['exp']:+10.3f} R")

    print("=" * 105)

if __name__ == "__main__":
    main()
