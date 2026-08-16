#!/usr/bin/env python3
"""audit_fibvol_30days_06.py — Rigorous 30-Day FibVOL Backtest Audit (1m Intrabar Granularity).

Tests FibVOL Strategy:
- Signal: 15m candle with >= 30x volume spike & GREEN close.
- Entry: 0.60 Fib Retracement (High - 0.60 * (High - Low))
- Stop Loss options:
    1. SL @ 0.70 Fib (High - 0.70 * Range)
    2. SL @ 1.00 Fib (Low of Spike Candle: High - 1.00 * Range)
    3. SL @ 0.60 ATR (Entry - 0.60 * ATR(14))
- Execution: 1m intrabar evaluation (simulating minute-by-minute order fill, SL, and TP).
"""
import os
import sys
import json
import time
import datetime
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

# Ensure adapter import
sys.path.insert(0, "/root/go-trader/platforms/coindcx")
try:
    from adapter import CoinDCXExchangeAdapter
except ImportError:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../platforms/coindcx")))
    from adapter import CoinDCXExchangeAdapter

A = CoinDCXExchangeAdapter()

def fetch_1m_klines(symbol, max_days=30):
    """Fetch up to 30 days (~43,200 bars) of 1m OHLCV data from Binance / CoinDCX."""
    try:
        sym = f"{symbol}USDT"
        all_rows = []
        endTime = None
        limit = 1500
        n_batches = 28  # ~42,000 1m bars (~29 days)
        
        headers = {"User-Agent": UA}
        for _ in range(n_batches):
            url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1m&limit={limit}"
            if endTime:
                url += f"&endTime={endTime}"
            req = urllib.request.Request(url, headers=headers)
            try:
                rows = json.load(urllib.request.urlopen(req, timeout=10))
            except Exception:
                rows = A.get_ohlcv(symbol, interval="1m", limit=1000)
                if rows:
                    rows = [[r[0], r[1], r[2], r[3], r[4], r[5]] for r in rows]
                else:
                    break
                    
            if not isinstance(rows, list) or len(rows) == 0:
                break
            all_rows = rows + all_rows
            endTime = rows[0][0] - 1
            if len(rows) < limit:
                break
                
        if len(all_rows) < 1000:
            return None
            
        out = []
        seen = set()
        for r in sorted(all_rows, key=lambda x: x[0]):
            t = int(r[0])
            if t in seen: continue
            seen.add(t)
            out.append({
                "t": t,
                "o": float(r[1]),
                "h": float(r[2]),
                "l": float(r[3]),
                "c": float(r[4]),
                "v": float(r[5])
            })
        return out
    except Exception as e:
        return None

def aggregate_15m_candles(klines_1m):
    """Aggregates 1m bars into 15m OHLCV candles with 1m bar indices."""
    buckets = {}
    for idx, bar in enumerate(klines_1m):
        bar["idx"] = idx
        b_time = (bar["t"] // (15 * 60 * 1000)) * (15 * 60 * 1000)
        if b_time not in buckets:
            buckets[b_time] = {
                "t": b_time,
                "o": bar["o"],
                "h": bar["h"],
                "l": bar["l"],
                "c": bar["c"],
                "v": bar["v"],
                "m1_start_idx": idx
            }
        else:
            b = buckets[b_time]
            b["h"] = max(b["h"], bar["h"])
            b["l"] = min(b["l"], bar["l"])
            b["c"] = bar["c"]
            b["v"] += bar["v"]
            
    return [buckets[k] for k in sorted(buckets.keys())]

def backtest_fibvol_symbol(symbol, klines_1m, sl_mode="spike_low"):
    """
    sl_mode options:
      - "spike_low": SL @ Low of Spike Candle (1.0 Fib)
      - "fib_07": SL @ 0.70 Fib Retracement
      - "atr_06": SL @ 0.60 ATR(14) below Entry
    """
    if not klines_1m or len(klines_1m) < 1000:
        return []

    klines_15m = aggregate_15m_candles(klines_1m)
    if len(klines_15m) < 50:
        return []

    vols_15m = np.array([b["v"] for b in klines_15m])
    opens_15m = np.array([b["o"] for b in klines_15m])
    highs_15m = np.array([b["h"] for b in klines_15m])
    lows_15m = np.array([b["l"] for b in klines_15m])
    closes_15m = np.array([b["c"] for b in klines_15m])
    times_15m = [b["t"] for b in klines_15m]

    trades = []
    i = 40
    n_15m = len(klines_15m)

    while i < n_15m - 2:
        avg_vol = float(np.mean(vols_15m[i-40:i]))
        if avg_vol <= 0:
            i += 1
            continue

        spike_mult = vols_15m[i] / avg_vol
        is_green = closes_15m[i] >= opens_15m[i]

        # Trigger Condition: 30x Volume Spike AND Green Candle
        if spike_mult >= 30.0 and is_green:
            spike_high = highs_15m[i]
            spike_low = lows_15m[i]
            spike_range = spike_high - spike_low

            if spike_range <= 0:
                i += 1
                continue

            entry_px = spike_high - 0.60 * spike_range  # 0.60 Fib Retracement Entry

            # Determine Stop Loss Price based on mode
            if sl_mode == "spike_low":
                sl_px = spike_low  # 1.0 Fib level (Low of Spike)
            elif sl_mode == "fib_07":
                sl_px = spike_high - 0.70 * spike_range  # 0.70 Fib level
            elif sl_mode == "atr_06":
                # Compute ATR(14) on 15m bars
                ranges = [highs_15m[k] - lows_15m[k] for k in range(max(0, i-14), i)]
                atr14 = float(np.mean(ranges)) if len(ranges) > 0 else spike_range * 0.5
                sl_px = entry_px - 0.60 * atr14
            else:
                sl_px = spike_low

            risk = entry_px - sl_px
            if risk <= 0:
                i += 1
                continue

            tp_px = entry_px + 4.0 * risk  # 1:4 Risk-Reward

            # Search forward up to 24 15m bars (6 hours) for retracement order fill
            start_m1_idx = klines_15m[i + 1]["m1_start_idx"]
            end_m1_idx = min(len(klines_1m) - 1, start_m1_idx + 24 * 15)

            filled = False
            fill_m1_idx = -1

            for idx in range(start_m1_idx, end_m1_idx):
                m1 = klines_1m[idx]
                if m1["l"] <= entry_px:
                    filled = True
                    fill_m1_idx = idx
                    break

            if filled and fill_m1_idx >= 0:
                # Order filled! Now simulate intrabar trade execution
                outcome = None
                exit_m1_idx = -1
                exit_px = 0.0

                for idx in range(fill_m1_idx, len(klines_1m)):
                    m1 = klines_1m[idx]

                    # Check SL hit (Low hits SL price)
                    if m1["l"] <= sl_px:
                        outcome = "SL"
                        exit_m1_idx = idx
                        exit_px = sl_px
                        break

                    # Check TP hit (High hits TP price)
                    if m1["h"] >= tp_px:
                        outcome = "TP"
                        exit_m1_idx = idx
                        exit_px = tp_px
                        break

                if outcome:
                    dt_ist = datetime.datetime.fromtimestamp(
                        klines_1m[fill_m1_idx]["t"] / 1000.0,
                        datetime.timezone(datetime.timedelta(hours=5, minutes=30))
                    ).strftime("%Y-%m-%d %H:%M")

                    pnl_r = 4.0 if outcome == "TP" else -1.0
                    pnl_usd = pnl_r * 2.0  # Risk $2 USD per trade on $200 capital

                    trades.append({
                        "symbol": symbol,
                        "fill_time_ist": dt_ist,
                        "spike_mult": round(spike_mult, 1),
                        "entry_px": entry_px,
                        "sl_px": sl_px,
                        "tp_px": tp_px,
                        "outcome": outcome,
                        "pnl_r": pnl_r,
                        "pnl_usd": pnl_usd,
                        "holding_mins": exit_m1_idx - fill_m1_idx
                    })

                    # Advance past trade exit time
                    next_15m_idx = i + max(1, (exit_m1_idx - fill_m1_idx) // 15)
                    i = min(n_15m - 2, next_15m_idx)
                else:
                    i += 1
            else:
                i += 1
        else:
            i += 1

    return trades

def main():
    print("==========================================================================")
    print("🔬 RIGOROUS 30-DAY FibVOL BACKTEST AUDIT (ENTRY @ 0.60 FIB RETRACEMENT)")
    print("==========================================================================")
    
    bases = sorted(list(A.active_bases()))
    print(f"Total Active CoinDCX Futures Coins: {len(bases)}")

    print("\nFetching 30 days of 1m intrabar OHLCV data across all coins...")
    data_map = {}
    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = {ex.submit(fetch_1m_klines, base): base for base in bases}
        for fut in futures:
            base = futures[fut]
            res = fut.result()
            if res:
                data_map[base] = res

    print(f"Successfully loaded 30-day 1m candle history for {len(data_map)} coins.")

    # Evaluate 3 SL Variations:
    # 1. SL @ Low of Spike Candle (1.0 Fib)
    # 2. SL @ 0.70 Fib Retracement
    # 3. SL @ 0.60 ATR(14) below Entry
    modes = [
        ("Spike Low (1.0 Fib SL)", "spike_low"),
        ("0.70 Fib Retracement SL", "fib_07"),
        ("0.60 ATR SL", "atr_06"),
    ]

    for label, mode in modes:
        all_trades = []
        for sym, klines in data_map.items():
            t = backtest_fibvol_symbol(sym, klines, sl_mode=mode)
            all_trades.extend(t)

        wins = [tr for tr in all_trades if tr["outcome"] == "TP"]
        losses = [tr for tr in all_trades if tr["outcome"] == "SL"]
        total_pnl_r = sum(tr["pnl_r"] for tr in all_trades)
        total_pnl_usd = sum(tr["pnl_usd"] for tr in all_trades)

        win_rate = (len(wins) / len(all_trades) * 100.0) if len(all_trades) > 0 else 0.0

        print(f"\n📊 --- AUDIT RESULT: {label} ---")
        print(f"Total Executed Trades : {len(all_trades)}")
        print(f"Wins / Losses         : {len(wins)} Wins / {len(losses)} Losses")
        print(f"Win Rate              : {win_rate:.1f}%")
        print(f"Total Net PnL (R)     : {total_pnl_r:+.1f} R")
        print(f"Total Net PnL ($ USD) : ${total_pnl_usd:+.2f} USD")

        if len(all_trades) > 0:
            print("\n  Sample Recent Trades:")
            for tr in all_trades[-5:]:
                print(f"    - {tr['symbol']:<10} | {tr['fill_time_ist']} | {tr['spike_mult']:>4.1f}x | {tr['outcome']:<2} ({tr['pnl_r']:+1.0f}R, ${tr['pnl_usd']:+.2f}) | Hold: {tr['holding_mins']}m")

if __name__ == "__main__":
    main()
