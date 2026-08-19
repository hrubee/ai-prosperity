#!/usr/bin/env python3
"""tools/backtest/backtest_heikinashi_improved.py — Optimized Heikin-Ashi Strategy
Implements:
1. Trend Filter: EMA 50 alignment
2. Color Transition Flip: Only triggers on 1st or 2nd candle of new color sequence
3. ATR Stop Loss Buffer: Minimum SL distance = max(Trigger Wick, 0.8 * ATR_14)
4. Dynamic Profit Protection:
   - Move Stop to Break-Even (0.0R) when trade reaches +1.5R
   - Lock +1.0R profit when trade reaches +2.5R
   - Take Profit target at +3.5R
5. Multi-Timeframe Sweeps: 15m, 30m, 1h, 4h across 495 CoinDCX futures pairs
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
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            data = json.loads(resp.read().decode())
        if not isinstance(data, list) or len(data) < 60:
            return None
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

def get_market_pairs() -> List[str]:
    url = "https://api.coindcx.com/exchange/v1/derivatives/futures/data/active_instruments"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode())
            return [p for p in data if p.startswith("B-") and p.endswith("_USDT")]
    except Exception as e:
        print(f"Error fetching pairs: {e}")
        return []

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

def backtest_pair(pair: str, candles: List[List[float]], timeframe_label: str) -> List[Dict[str, Any]]:
    n = len(candles)
    if n < 55: return []

    times = np.array([c[0] for c in candles])
    opens = np.array([c[1] for c in candles])
    highs = np.array([c[2] for c in candles])
    lows = np.array([c[3] for c in candles])
    closes = np.array([c[4] for c in candles])
    volumes = np.array([c[5] for c in candles])

    # 1. EMA 50
    ema50 = pd.Series(closes).ewm(span=50).mean().values
    
    # 2. Volume SMA 20
    vol_ma20 = pd.Series(volumes).rolling(20, min_periods=5).mean().values

    # 3. ATR 14
    tr = np.maximum(highs[1:] - lows[1:], np.maximum(abs(highs[1:] - closes[:-1]), abs(lows[1:] - closes[:-1])))
    atr_series = pd.Series(tr).rolling(14, min_periods=5).mean().values
    atr = np.insert(atr_series, 0, tr[:13].mean())

    # 4. Heikin-Ashi
    ha_open, ha_high, ha_low, ha_close = compute_heikin_ashi(opens, highs, lows, closes)

    trades = []
    in_trade_until_idx = -1
    consec_green = 0
    consec_red = 0

    for i in range(50, n - 2):
        if i <= in_trade_until_idx:
            continue

        is_green = ha_close[i] > ha_open[i]
        is_red = ha_close[i] < ha_open[i]

        if is_green:
            consec_green += 1
            consec_red = 0
        elif is_red:
            consec_red += 1
            consec_green = 0
        else:
            consec_green = 0
            consec_red = 0

        # Only allow 1st or 2nd candle of transition
        valid_streak = (is_green and (1 <= consec_green <= 2)) or (is_red and (1 <= consec_red <= 2))
        if not valid_streak:
            continue

        flat_bottom = is_green and np.isclose(ha_low[i], ha_open[i], rtol=0.0005)
        flat_top = is_red and np.isclose(ha_high[i], ha_open[i], rtol=0.0005)

        # Volume condition (must be at least 80% of 20MA to avoid dead bars)
        vol_ok = volumes[i] >= (0.8 * vol_ma20[i]) if (vol_ma20[i] > 0) else True
        if not vol_ok:
            continue

        curr_atr = atr[i] if (i < len(atr) and atr[i] > 0) else (closes[i] * 0.01)

        # ==========================================
        # 🟢 LONG SIGNAL
        # ==========================================
        if flat_bottom and closes[i] > ema50[i]:
            entry_px = closes[i+1]
            raw_sl = min(lows[i], ha_low[i])
            # ATR Floor on Stop Loss (min 0.8 * ATR)
            min_sl_dist = 0.8 * curr_atr
            sl_dist = max(entry_px - raw_sl, min_sl_dist)
            sl_px = entry_px - sl_dist

            risk = entry_px - sl_px
            if risk <= 0 or (risk / entry_px) < 0.003: # Min 0.3% risk
                continue

            target_tp = entry_px + (3.5 * risk) # 1:3.5 Target
            curr_sl = sl_px
            be_triggered = False
            lock_profit_triggered = False
            trade_done = False

            for j in range(i+2, min(n, i+100)):
                bar_h = highs[j]
                bar_l = lows[j]
                
                # Check BE condition: price reached +1.5R
                if not be_triggered and bar_h >= (entry_px + 1.5 * risk):
                    curr_sl = entry_px # Move to BE
                    be_triggered = True

                # Check Lock +1.0R condition: price reached +2.5R
                if not lock_profit_triggered and bar_h >= (entry_px + 2.5 * risk):
                    curr_sl = entry_px + (1.0 * risk) # Lock +1.0R
                    lock_profit_triggered = True

                # 1. Check Stop Loss / Trailing Hit
                if bar_l <= curr_sl:
                    r_pnl = -1.0 if not be_triggered else (1.0 if lock_profit_triggered else 0.0)
                    res_label = "WIN" if r_pnl > 0 else ("BE" if r_pnl == 0 else "LOSS")
                    trades.append({
                        "pair": pair,
                        "timeframe": timeframe_label,
                        "side": "LONG",
                        "entry_time": int(times[i+1]),
                        "exit_time": int(times[j]),
                        "entry_px": entry_px,
                        "exit_px": curr_sl,
                        "risk_pct": round((risk/entry_px)*100, 2),
                        "result": res_label,
                        "r_pnl": r_pnl,
                        "holding_bars": j - (i + 1)
                    })
                    in_trade_until_idx = j
                    trade_done = True
                    break

                # 2. Check Take Profit Hit
                elif bar_h >= target_tp:
                    trades.append({
                        "pair": pair,
                        "timeframe": timeframe_label,
                        "side": "LONG",
                        "entry_time": int(times[i+1]),
                        "exit_time": int(times[j]),
                        "entry_px": entry_px,
                        "exit_px": target_tp,
                        "risk_pct": round((risk/entry_px)*100, 2),
                        "result": "WIN",
                        "r_pnl": 3.5,
                        "holding_bars": j - (i + 1)
                    })
                    in_trade_until_idx = j
                    trade_done = True
                    break

        # ==========================================
        # 🔴 SHORT SIGNAL
        # ==========================================
        elif flat_top and closes[i] < ema50[i]:
            entry_px = closes[i+1]
            raw_sl = max(highs[i], ha_high[i])
            min_sl_dist = 0.8 * curr_atr
            sl_dist = max(raw_sl - entry_px, min_sl_dist)
            sl_px = entry_px + sl_dist

            risk = sl_px - entry_px
            if risk <= 0 or (risk / entry_px) < 0.003:
                continue

            target_tp = entry_px - (3.5 * risk)
            curr_sl = sl_px
            be_triggered = False
            lock_profit_triggered = False
            trade_done = False

            for j in range(i+2, min(n, i+100)):
                bar_h = highs[j]
                bar_l = lows[j]

                # Check BE condition: price dropped +1.5R in profit
                if not be_triggered and bar_l <= (entry_px - 1.5 * risk):
                    curr_sl = entry_px
                    be_triggered = True

                # Check Lock +1.0R condition: price dropped +2.5R
                if not lock_profit_triggered and bar_l <= (entry_px - 2.5 * risk):
                    curr_sl = entry_px - (1.0 * risk)
                    lock_profit_triggered = True

                # 1. Check Stop Loss / Trailing Hit
                if bar_h >= curr_sl:
                    r_pnl = -1.0 if not be_triggered else (1.0 if lock_profit_triggered else 0.0)
                    res_label = "WIN" if r_pnl > 0 else ("BE" if r_pnl == 0 else "LOSS")
                    trades.append({
                        "pair": pair,
                        "timeframe": timeframe_label,
                        "side": "SHORT",
                        "entry_time": int(times[i+1]),
                        "exit_time": int(times[j]),
                        "entry_px": entry_px,
                        "exit_px": curr_sl,
                        "risk_pct": round((risk/entry_px)*100, 2),
                        "result": res_label,
                        "r_pnl": r_pnl,
                        "holding_bars": j - (i + 1)
                    })
                    in_trade_until_idx = j
                    trade_done = True
                    break

                # 2. Check Take Profit Hit
                elif bar_l <= target_tp:
                    trades.append({
                        "pair": pair,
                        "timeframe": timeframe_label,
                        "side": "SHORT",
                        "entry_time": int(times[i+1]),
                        "exit_time": int(times[j]),
                        "entry_px": entry_px,
                        "exit_px": target_tp,
                        "risk_pct": round((risk/entry_px)*100, 2),
                        "result": "WIN",
                        "r_pnl": 3.5,
                        "holding_bars": j - (i + 1)
                    })
                    in_trade_until_idx = j
                    trade_done = True
                    break

    return trades

def run_timeframe_test(pairs: List[str], base_interval: str, agg_factor: int, label: str):
    print(f"\n{'='*80}")
    print(f"🚀 RUNNING IMPROVED BACKTEST: {label.upper()} ({len(pairs)} Instruments)")
    print(f"{'='*80}")

    all_trades = []
    t0 = time.time()

    def process_coin(pair):
        raw = fetch_raw_candles(pair, base_interval, 500)
        if not raw: return []
        c = aggregate_candles(raw, agg_factor) if agg_factor > 1 else raw
        if len(c) < 60: return []
        return backtest_pair(pair, c, label)

    with ThreadPoolExecutor(max_workers=30) as ex:
        results = list(ex.map(process_coin, pairs))
        for res in results:
            all_trades.extend(res)

    elapsed = time.time() - t0
    df = pd.DataFrame(all_trades)
    
    if df.empty:
        print(f"No trades generated for {label}.")
        return None

    wins = (df["result"] == "WIN").sum()
    bes = (df["result"] == "BE").sum()
    losses = (df["result"] == "LOSS").sum()
    total = len(df)
    net_r = df["r_pnl"].sum()
    exp = net_r / total if total > 0 else 0
    win_rate = (wins / total) * 100
    be_rate = (bes / total) * 100
    loss_rate = (losses / total) * 100

    gross_profit = df[df["r_pnl"] > 0]["r_pnl"].sum()
    gross_loss = abs(df[df["r_pnl"] < 0]["r_pnl"].sum())
    pf = (gross_profit / gross_loss) if gross_loss > 0 else 999.0

    print(f"✅ Completed {label} sweep in {elapsed:.1f}s. Total Trades: {total:,d}")
    print(f"\n📈 ── PERFORMANCE: HEIKIN-ASHI IMPROVED {label.upper()} ──")
    print(f"  • Total Trades       : {total:,d} (Longs: {(df['side']=='LONG').sum():,d}, Shorts: {(df['side']=='SHORT').sum():,d})")
    print(f"  • Win Rate (TP/Lock) : {win_rate:.2f}% ({wins:,d} Wins)")
    print(f"  • Break-Even Rate    : {be_rate:.2f}% ({bes:,d} Break-Evens at 0.0R)")
    print(f"  • Loss Rate          : {loss_rate:.2f}% ({losses:,d} Losses)")
    print(f"  • Net Return (R)     : {net_r:+,.1f} R")
    print(f"  • Expectancy / Trade : {exp:+.3f} R")
    print(f"  • Profit Factor      : {pf:.2f}")
    print(f"  • Avg Holding Time   : {df['holding_bars'].mean():.1f} bars")

    csv_out = f"/root/trading-bot/heikinashi_improved_{label}_trades.csv"
    df.to_csv(csv_out, index=False)
    print(f"  -> Saved to {csv_out}")

    return {
        "label": label,
        "trades": total,
        "wins": wins,
        "bes": bes,
        "losses": losses,
        "win_rate": win_rate,
        "be_rate": be_rate,
        "loss_rate": loss_rate,
        "net_r": net_r,
        "exp": exp,
        "pf": pf
    }

def main():
    print("=" * 80)
    print("💎 HEIKIN-ASHI IMPROVED (DYNAMIC BE + ATR BUFFER + EMA 50) BACKTESTER 💎")
    print("=" * 80)

    pairs = get_market_pairs()
    print(f"Discovered {len(pairs)} active CoinDCX futures pairs.\n")

    configs = [
        ("15m", 1, "15m"),
        ("15m", 2, "30m"),
        ("1h", 1, "1h"),
        ("1h", 4, "4h"),
    ]

    summaries = []
    for base_iv, factor, label in configs:
        summary = run_timeframe_test(pairs, base_iv, factor, label)
        if summary: summaries.append(summary)

    print("\n" + "=" * 95)
    print("🏆 FINAL COMPARISON: HEIKIN-ASHI IMPROVED STRATEGY")
    print("=" * 95)
    print(f"{'Timeframe':<12} | {'Trades':<8} | {'Win Rate':<10} | {'BE Rate':<9} | {'Loss Rate':<10} | {'Net Return':<14} | {'Expectancy':<12} | {'Profit Factor':<6}")
    print("-" * 95)
    for s in summaries:
        print(f"{s['label']:<12} | {s['trades']:<8,d} | {s['win_rate']:<9.2f}% | {s['be_rate']:<8.2f}% | {s['loss_rate']:<9.2f}% | {s['net_r']:+12.1f} R | {s['exp']:+10.3f} R | {s['pf']:<6.2f}")
    print("=" * 95)

if __name__ == "__main__":
    main()
