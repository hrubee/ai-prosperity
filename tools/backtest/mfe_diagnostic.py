#!/usr/bin/env python3
"""tools/backtest/mfe_diagnostic.py — Calculate Maximum Favorable Excursion on Heikin-Ashi Signals
Analyzes:
1. Exact MFE distribution on losing trades (Did they hit +1R, +2R, +3R before dying?)
2. Chop vs Trend failure rate
3. Candle body size (ATR) ratio
"""
import os, sys, json, ssl, urllib.request
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0"

def fetch_candles(pair: str, interval="1h", limit=350):
    url = f"https://public.coindcx.com/market_data/candles?pair={pair}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
            rows = json.loads(r.read().decode())
        if not isinstance(rows, list) or len(rows) < 80: return None
        rows = sorted(rows, key=lambda x: x["time"])
        return [[x["time"], float(x["open"]), float(x["high"]), float(x["low"]), float(x["close"]), float(x.get("volume", 0))] for x in rows]
    except Exception:
        return None

def main():
    url = "https://api.coindcx.com/exchange/v1/derivatives/futures/data/active_instruments"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
        coins = [p for p in json.loads(resp.read().decode()) if p.startswith("B-") and p.endswith("_USDT")]

    print(f"Fetching 1H candle series for {len(coins)} coins to perform MFE Diagnostic...")
    with ThreadPoolExecutor(max_workers=30) as ex:
        raw = list(ex.map(lambda c: (c, fetch_candles(c, "1h", 350)), coins))
    dataset = [(c, kl) for c, kl in raw if kl is not None and len(kl) >= 100]

    all_losses_mfe = []
    all_wins_mfe = []
    consecutive_candle_results = {1: {"win": 0, "loss": 0}, 2: {"win": 0, "loss": 0}, 3: {"win": 0, "loss": 0}, "4+": {"win": 0, "loss": 0}}
    
    # Body-to-ATR ratio on triggers
    trigger_body_losses = []
    trigger_body_wins = []

    for p, kl in dataset:
        n = len(kl)
        opens = np.array([k[1] for k in kl])
        highs = np.array([k[2] for k in kl])
        lows = np.array([k[3] for k in kl])
        closes = np.array([k[4] for k in kl])

        # ATR 14
        tr = np.maximum(highs[1:] - lows[1:], np.maximum(abs(highs[1:] - closes[:-1]), abs(lows[1:] - closes[:-1])))
        atr = pd.Series(tr).rolling(14).mean().values
        atr = np.insert(atr, 0, tr[:13].mean())

        # HA
        ha_c = (opens + highs + lows + closes) / 4.0
        ha_o = np.zeros(n)
        ha_o[0] = (opens[0] + closes[0]) / 2.0
        for i in range(1, n): ha_o[i] = (ha_o[i-1] + ha_c[i-1]) / 2.0
        ha_h = np.maximum(highs, np.maximum(ha_o, ha_c))
        ha_l = np.minimum(lows, np.minimum(ha_o, ha_c))

        in_t = -1
        consec = 0
        for i in range(30, n - 2):
            if i <= in_t: continue
            is_g = ha_c[i] > ha_o[i]
            is_r = ha_c[i] < ha_o[i]
            flat_b = is_g and np.isclose(ha_l[i], ha_o[i], rtol=0.0005)
            flat_t = is_r and np.isclose(ha_h[i], ha_o[i], rtol=0.0005)

            # Count consecutive same-color candles
            if is_g:
                consec = consec + 1 if (i > 0 and ha_c[i-1] > ha_o[i-1]) else 1
            elif is_r:
                consec = consec + 1 if (i > 0 and ha_c[i-1] < ha_o[i-1]) else 1
            else:
                consec = 0

            c_bucket = consec if consec <= 3 else "4+"

            body_size = abs(closes[i] - opens[i])
            curr_atr = atr[i] if i < len(atr) and atr[i] > 0 else body_size
            rel_body = body_size / curr_atr if curr_atr > 0 else 1.0

            if flat_b:
                sl = lows[i]
                entry = closes[i+1]
                risk = entry - sl
                if risk > 0 and (risk/entry) >= 0.002:
                    tp = entry + (4.0 * risk)
                    max_favorable = 0.0
                    res = None
                    for j in range(i+2, min(n, i+100)):
                        fav_dist = highs[j] - entry
                        if fav_dist > max_favorable: max_favorable = fav_dist
                        
                        if lows[j] <= sl:
                            res = "LOSS"
                            in_t = j
                            break
                        elif highs[j] >= tp:
                            res = "WIN"
                            in_t = j
                            break
                    if res == "LOSS":
                        mfe_r = max_favorable / risk
                        all_losses_mfe.append(mfe_r)
                        trigger_body_losses.append(rel_body)
                        consecutive_candle_results[c_bucket]["loss"] += 1
                    elif res == "WIN":
                        all_wins_mfe.append(4.0)
                        trigger_body_wins.append(rel_body)
                        consecutive_candle_results[c_bucket]["win"] += 1

            elif flat_t:
                sl = highs[i]
                entry = closes[i+1]
                risk = sl - entry
                if risk > 0 and (risk/entry) >= 0.002:
                    tp = entry - (4.0 * risk)
                    max_favorable = 0.0
                    res = None
                    for j in range(i+2, min(n, i+100)):
                        fav_dist = entry - lows[j]
                        if fav_dist > max_favorable: max_favorable = fav_dist
                        
                        if highs[j] >= sl:
                            res = "LOSS"
                            in_t = j
                            break
                        elif lows[j] <= tp:
                            res = "WIN"
                            in_t = j
                            break
                    if res == "LOSS":
                        mfe_r = max_favorable / risk
                        all_losses_mfe.append(mfe_r)
                        trigger_body_losses.append(rel_body)
                        consecutive_candle_results[c_bucket]["loss"] += 1
                    elif res == "WIN":
                        all_wins_mfe.append(4.0)
                        trigger_body_wins.append(rel_body)
                        consecutive_candle_results[c_bucket]["win"] += 1

    print("\n" + "=" * 80)
    print("🔬 DATA SCIENCE LOSS ROOT CAUSE REPORT (Heikin-Ashi 1H)")
    print("=" * 80)
    
    losses_mfe = np.array(all_losses_mfe)
    total_l = len(losses_mfe)
    print(f"\n1️⃣ MAXIMUM PROFIT REACHED BEFORE LOSING (Total Losses: {total_l:,d}):")
    print(f"  • Immediate Loss (Reached < 0.2R profit)     : {(losses_mfe < 0.2).sum():,d} ({(losses_mfe < 0.2).mean()*100:.1f}%)")
    print(f"  • Reached +0.5R to +1.0R then stopped out    : {((losses_mfe >= 0.5) & (losses_mfe < 1.0)).sum():,d} ({((losses_mfe >= 0.5) & (losses_mfe < 1.0)).mean()*100:.1f}%)")
    print(f"  • Reached +1.0R to +2.0R then stopped out    : {((losses_mfe >= 1.0) & (losses_mfe < 2.0)).sum():,d} ({((losses_mfe >= 1.0) & (losses_mfe < 2.0)).mean()*100:.1f}%)")
    print(f"  • Reached +2.0R to +3.0R then stopped out    : {((losses_mfe >= 2.0) & (losses_mfe < 3.0)).sum():,d} ({((losses_mfe >= 2.0) & (losses_mfe < 3.0)).mean()*100:.1f}%)")
    print(f"  • Reached +3.0R to +3.9R then stopped out    : {((losses_mfe >= 3.0) & (losses_mfe < 4.0)).sum():,d} ({((losses_mfe >= 3.0) & (losses_mfe < 4.0)).mean()*100:.1f}%)")
    print(f"  ⚠️ SUMMARY: {((losses_mfe >= 1.0)).mean()*100:.1f}% of ALL losses were in at least +1.0R profit before reversing!")
    print(f"  ⚠️ SUMMARY: {((losses_mfe >= 2.0)).mean()*100:.1f}% of ALL losses were in at least +2.0R profit before reversing!")

    print("\n2️⃣ CONSECUTIVE CANDLE POSITION PERFORMANCE:")
    for c_pos in [1, 2, 3, "4+"]:
        w = consecutive_candle_results[c_pos]["win"]
        l = consecutive_candle_results[c_pos]["loss"]
        tot = w + l
        wr = (w / tot * 100) if tot > 0 else 0
        print(f"  • Candle #{c_pos} Trigger : {tot:<6,d} trades | Win Rate: {wr:.2f}% | Wins: {w:<5,d} | Losses: {l:<5,d}")

    print("\n3️⃣ TRIGGER CANDLE BODY SIZE VS ATR:")
    print(f"  • Average Body/ATR on Winning Set : {np.mean(trigger_body_wins):.2f}x ATR")
    print(f"  • Average Body/ATR on Losing Set  : {np.mean(trigger_body_losses):.2f}x ATR")
    print("=" * 80)

if __name__ == "__main__":
    main()
