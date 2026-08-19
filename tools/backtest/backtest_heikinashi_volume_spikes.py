#!/usr/bin/env python3
"""tools/backtest/backtest_heikinashi_volume_spikes.py — Pairing Heikin-Ashi with Volume Spikes
Tests Volume Spike Multipliers: 1.5x, 2.0x, 3.0x, 5.0x, 10.0x across 15m, 30m, 1h, 4h on all 495 CoinDCX pairs.
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

def fetch_raw_candles(pair: str, interval: str = "1h", limit: int = 500) -> Optional[List[List[float]]]:
    url = f"https://public.coindcx.com/market_data/candles?pair={pair}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=6, context=ctx) as resp:
            data = json.loads(resp.read().decode())
        if not isinstance(data, list) or len(data) < 60: return None
        data = sorted(data, key=lambda x: x["time"])
        return [[
            float(x["time"]),
            float(x["open"]),
            float(x["high"]),
            float(x["low"]),
            float(x["close"]),
            float(x.get("volume", 0.0))
        ] for x in data]
    except Exception:
        return None

def aggregate_candles(candles: List[List[float]], factor: int) -> List[List[float]]:
    if not candles: return []
    out = []
    for i in range(0, len(candles) - factor + 1, factor):
        chunk = candles[i:i+factor]
        t = chunk[0][0]
        o = chunk[0][1]
        h = max(c[2] for c in chunk)
        l = min(c[3] for c in chunk)
        c_ = chunk[-1][4]
        v = sum(c[5] for c in chunk)
        out.append([t, o, h, l, c_, v])
    return out

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

def evaluate_volume_spike_strategy(candles_dataset, vol_multiplier: float, rr_target: float = 3.5, use_be: bool = True):
    total_trades = 0
    wins = 0
    bes = 0
    losses = 0
    net_r = 0.0

    for p, kl in candles_dataset:
        n = len(kl)
        times = np.array([k[0] for k in kl])
        opens = np.array([k[1] for k in kl])
        highs = np.array([k[2] for k in kl])
        lows = np.array([k[3] for k in kl])
        closes = np.array([k[4] for k in kl])
        volumes = np.array([k[5] for k in kl])

        # Volume MA 20
        vol_ma20 = pd.Series(volumes).rolling(20, min_periods=5).mean().values
        # ATR 14
        tr = np.maximum(highs[1:] - lows[1:], np.maximum(abs(highs[1:] - closes[:-1]), abs(lows[1:] - closes[:-1])))
        atr = pd.Series(tr).rolling(14, min_periods=5).mean().values
        atr = np.insert(atr, 0, tr[:13].mean())

        # HA
        ha_o, ha_h, ha_l, ha_c = compute_heikin_ashi(opens, highs, lows, closes)

        in_trade = -1
        for i in range(25, n - 2):
            if i <= in_trade: continue

            # Volume Spike Condition
            has_vol_spike = volumes[i] >= (vol_multiplier * vol_ma20[i]) if (vol_ma20[i] > 0) else False
            if not has_vol_spike:
                continue

            is_g = ha_c[i] > ha_o[i]
            is_r = ha_c[i] < ha_o[i]

            flat_b = is_g and np.isclose(ha_l[i], ha_o[i], rtol=0.0005)
            flat_t = is_r and np.isclose(ha_h[i], ha_o[i], rtol=0.0005)

            curr_atr = atr[i] if (i < len(atr) and atr[i] > 0) else (closes[i] * 0.01)

            # LONG EXECUTION
            if flat_b:
                entry = closes[i+1]
                raw_sl = min(lows[i], ha_l[i])
                sl_dist = max(entry - raw_sl, 0.8 * curr_atr)
                sl = entry - sl_dist
                risk = entry - sl
                if risk <= 0 or (risk / entry) < 0.003: continue

                tp = entry + (rr_target * risk)
                curr_sl = sl
                be_hit = False
                lock_hit = False

                for j in range(i+2, min(n, i+100)):
                    h_j = highs[j]
                    l_j = lows[j]

                    if use_be:
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
                        net_r += rr_target
                        in_trade = j
                        break

            # SHORT EXECUTION
            elif flat_t:
                entry = closes[i+1]
                raw_sl = max(highs[i], ha_h[i])
                sl_dist = max(raw_sl - entry, 0.8 * curr_atr)
                sl = entry + sl_dist
                risk = sl - entry
                if risk <= 0 or (risk / entry) < 0.003: continue

                tp = entry - (rr_target * risk)
                curr_sl = sl
                be_hit = False
                lock_hit = False

                for j in range(i+2, min(n, i+100)):
                    h_j = highs[j]
                    l_j = lows[j]

                    if use_be:
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
                        net_r += rr_target
                        in_trade = j
                        break

    wr = (wins / total_trades * 100) if total_trades > 0 else 0.0
    be_r = (bes / total_trades * 100) if total_trades > 0 else 0.0
    loss_r = (losses / total_trades * 100) if total_trades > 0 else 0.0
    exp = (net_r / total_trades) if total_trades > 0 else 0.0
    gross_p = wins * rr_target
    pf = (gross_p / (losses + 1e-9)) if losses > 0 else 999.0

    return {
        "vol_multiplier": vol_multiplier,
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
    print("=" * 105)
    print("🔥 HEIKIN-ASHI WICKLESS + VOLUME SPIKE COMBO BACKTESTER (ALL 495 COINS) 🔥")
    print("=" * 105)

    url = "https://api.coindcx.com/exchange/v1/derivatives/futures/data/active_instruments"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
        coins = [p for p in json.loads(resp.read().decode()) if p.startswith("B-") and p.endswith("_USDT")]

    print(f"Loaded {len(coins)} CoinDCX instruments.\n")

    for tf_label, base_iv, factor in [("15m", "15m", 1), ("30m", "15m", 2), ("1h", "1h", 1), ("4h", "1h", 4)]:
        print(f"\n🚀 FETCHING CANDLES & RUNNING VOLUME SPIKE SWEEPS: {tf_label.upper()}...")
        with ThreadPoolExecutor(max_workers=30) as ex:
            raw_data = list(ex.map(lambda c: (c, fetch_raw_candles(c, base_iv, 500)), coins))

        dataset = []
        for c, raw in raw_data:
            if raw and len(raw) >= 60:
                agg = aggregate_candles(raw, factor) if factor > 1 else raw
                if len(agg) >= 50:
                    dataset.append((c, agg))

        print(f"Dataset ready: {len(dataset)} valid coin series for {tf_label.upper()}.")
        print(f"{'Volume Threshold':<25} | {'Trades':<8} | {'Win Rate':<10} | {'BE Rate':<9} | {'Net Return':<14} | {'Expectancy':<12} | {'PF':<6}")
        print("-" * 95)

        for vol_mult in [1.0, 1.5, 2.0, 3.0, 5.0, 10.0]:
            lbl = f"Vol >= {vol_mult:.1f}x SMA20" if vol_mult > 1.0 else "Baseline (No Vol Filter)"
            res = evaluate_volume_spike_strategy(dataset, vol_mult, rr_target=3.5, use_be=True)
            if res["trades"] > 0:
                print(f"{lbl:<25} | {res['trades']:<8,d} | {res['win_rate']:<9.2f}% | {res['be_rate']:<8.2f}% | {res['net_r']:+12.1f} R | {res['exp']:+10.3f} R | {res['pf']:<5.2f}")

    print("=" * 105)

if __name__ == "__main__":
    main()
