#!/usr/bin/env python3
"""backtest_fibvol.py — Multi-Timeframe (15m Signal + 1m Intrabar Execution) Backtester for the FIBVOL Strategy.

Rules:
1. 15m Timeframe: 30x Volume Spike over 40-bar baseline volume.
2. Candle must close GREEN (Close >= Open).
3. Fibonacci Retracement on Spike Candle (Low to High):
   - Entry: 0.6 Retracement level (High - 0.6 * (High - Low))
   - Stop Loss (SL): 0.7 Retracement level (High - 0.7 * (High - Low))
   - Take Profit (TP): 1:4 Risk-Reward Ratio (Entry + 4 * (Entry - SL))
4. 1m Intrabar Execution:
   - Simulates minute-by-minute price movement for order fills, SL hits, and TP hits.
5. Multi-Candle Retracement Loop:
   - If price retraces to 0.6 level: Order fills -> enter LONG.
   - If price does NOT hit 0.6 and next 15m candle closes GREEN: Re-plot Fib levels on new green candle and update Limit order.
   - If next 15m candle closes RED: Cancel Limit order, stop watch, and exit loop.
"""
import os
import sys
import json
import time
import datetime
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

# Configuration
VOL_THRESHOLD = 30.0    # 30x volume spike
FIB_ENTRY = 0.6        # 0.6 Fib Retracement
FIB_SL = 0.7           # 0.7 Fib Retracement
RR_RATIO = 4.0         # 1:4 Risk-Reward Ratio
RISK_FRAC = 0.01       # 1% wallet risk per trade
START_BAL_USDT = 200.0 # $200 initial backtest equity

# Ensure adapter import
sys.path.insert(0, "/root/go-trader/platforms/coindcx")
try:
    from adapter import CoinDCXExchangeAdapter
except ImportError:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../platforms/coindcx")))
    from adapter import CoinDCXExchangeAdapter

A = CoinDCXExchangeAdapter()

def fetch_futures_universe():
    """Fetch active CoinDCX perpetual futures coins."""
    try:
        bases = sorted(list(A.active_bases()))
        if len(bases) > 0:
            return bases
    except Exception as e:
        print("Adapter universe error:", e)
    return ["BTC", "ETH", "SOL", "XRP", "DOGE", "AVAX", "LINK", "ADA", "SUI", "NEAR", "PEPE", "WIF"]

def fetch_1m_klines(symbol, days=14):
    """Fetch 1m OHLCV bars from CoinDCX adapter / Binance endpoint."""
    try:
        sym = f"{symbol}USDT"
        all_rows = []
        endTime = None
        limit = 1500
        n_batches = 10  # ~15,000 1m bars (~10 days of 1m data)
        
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}
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
                
        if len(all_rows) < 500:
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
    except Exception:
        return None

def aggregate_15m_candles(klines_1m):
    """Aggregates 1m bars into 15m OHLCV candles."""
    buckets = {}
    for bar in klines_1m:
        b_time = (bar["t"] // (15 * 60 * 1000)) * (15 * 60 * 1000)
        if b_time not in buckets:
            buckets[b_time] = {
                "t": b_time,
                "o": bar["o"],
                "h": bar["h"],
                "l": bar["l"],
                "c": bar["c"],
                "v": bar["v"],
                "m1_start_idx": bar["idx"]
            }
        else:
            b = buckets[b_time]
            b["h"] = max(b["h"], bar["h"])
            b["l"] = min(b["l"], bar["l"])
            b["c"] = bar["c"]
            b["v"] += bar["v"]
            
    out = [buckets[k] for k in sorted(buckets.keys())]
    return out

def backtest_single_coin(symbol, klines_1m):
    if not klines_1m or len(klines_1m) < 1000:
        return []

    # Assign 1m bar indices
    for idx, b in enumerate(klines_1m):
        b["idx"] = idx

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
        # Check 40-bar baseline volume
        avg_vol = float(np.mean(vols_15m[i-40:i]))
        if avg_vol <= 0:
            i += 1
            continue

        spike_mult = vols_15m[i] / avg_vol
        is_green = closes_15m[i] >= opens_15m[i]

        # Trigger Condition: 30x Volume Spike AND Green Candle Close
        if spike_mult >= VOL_THRESHOLD and is_green:
            # Active watch for Limit Order Retracement
            watch_bar_idx = i
            in_trade = False
            
            while watch_bar_idx < n_15m - 1:
                cur_h = highs_15m[watch_bar_idx]
                cur_l = lows_15m[watch_bar_idx]
                cur_o = opens_15m[watch_bar_idx]
                cur_c = closes_15m[watch_bar_idx]
                
                # Check if candle is RED -> Stop watch immediately!
                if cur_c < cur_o and watch_bar_idx > i:
                    break  # RED candle closed -> stop watch loop!

                rng = cur_h - cur_l
                if rng <= 0:
                    watch_bar_idx += 1
                    continue

                # Calculate Fib retracement levels from High to Low
                entry_px = cur_h - FIB_ENTRY * rng
                sl_px = cur_h - FIB_SL * rng
                risk = entry_px - sl_px
                
                if risk <= 0:
                    watch_bar_idx += 1
                    continue
                    
                tp_px = entry_px + RR_RATIO * risk

                # Now simulate next 15m bar minute-by-minute on 1m granularity
                start_1m_t = times_15m[watch_bar_idx + 1]
                end_1m_t = start_1m_t + 15 * 60 * 1000

                # Find 1m bars in this 15m window
                m1_bars = [b for b in klines_1m if start_1m_t <= b["t"] < end_1m_t]

                filled = False
                for m1 in m1_bars:
                    if m1["l"] <= entry_px:
                        # Order Filled! Enter LONG
                        filled = True
                        fill_time_ms = m1["t"]
                        fill_1m_idx = m1["idx"]
                        break

                if filled:
                    # Trade entered! Track outcome minute-by-minute from fill_1m_idx onwards
                    outcome = None
                    exit_px = None
                    exit_time_ms = None

                    for m1 in klines_1m[fill_1m_idx:]:
                        # Check SL hit
                        if m1["l"] <= sl_px:
                            outcome = "SL"
                            exit_px = sl_px
                            exit_time_ms = m1["t"]
                            break
                        # Check TP hit
                        if m1["h"] >= tp_px:
                            outcome = "TP"
                            exit_px = tp_px
                            exit_time_ms = m1["t"]
                            break

                    if outcome:
                        pnl_r = 4.0 if outcome == "TP" else -1.0
                        dt_entry = datetime.datetime.fromtimestamp(fill_time_ms/1000.0, datetime.timezone(datetime.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M")
                        dt_exit = datetime.datetime.fromtimestamp(exit_time_ms/1000.0, datetime.timezone(datetime.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M")
                        
                        trades.append({
                            "symbol": symbol,
                            "entry_time": dt_entry,
                            "exit_time": dt_exit,
                            "entry_px": entry_px,
                            "sl_px": sl_px,
                            "tp_px": tp_px,
                            "exit_px": exit_px,
                            "outcome": outcome,
                            "pnl_r": pnl_r,
                            "spike_mult": spike_mult
                        })
                        in_trade = True
                        break  # Exit watch loop, trade finished!
                else:
                    # Not filled in this candle. Move to next 15m candle to evaluate if it closed Green
                    watch_bar_idx += 1

            if in_trade:
                i = watch_bar_idx + 1
            else:
                i += 1
        else:
            i += 1

    return trades

def main():
    print("=== STARTING FIBVOL STRATEGY 1-MINUTE GRANULARITY BACKTEST ===")
    universe = fetch_futures_universe()
    print(f"Total Perpetual Futures Universe: {len(universe)} Coins")

    print("Fetching 1m OHLCV bars across all coins...")
    all_trades = []
    coins_tested = 0

    with ThreadPoolExecutor(max_workers=30) as ex:
        futures_map = {ex.submit(fetch_1m_klines, sym, 14): sym for sym in universe}
        for fut in futures_map:
            sym = futures_map[fut]
            try:
                klines_1m = fut.result()
                if klines_1m:
                    coins_tested += 1
                    t = backtest_single_coin(sym, klines_1m)
                    all_trades.extend(t)
                    if len(t) > 0:
                        print(f"[{coins_tested}/{len(universe)}] {sym:<10}: {len(t)} trades executed")
            except Exception as e:
                pass

    print(f"\n===================================================================")
    print(f"FIBVOL STRATEGY BACKTEST RESULTS ({coins_tested} COINS TESTED):")
    print(f"===================================================================")
    print(f"  • Total Trades Executed:        {len(all_trades)}")

    if len(all_trades) == 0:
        print("No trades triggered. Experiment complete.")
        return

    wins = [t for t in all_trades if t["outcome"] == "TP"]
    losses = [t for t in all_trades if t["outcome"] == "SL"]
    
    win_rate = (len(wins) / len(all_trades)) * 100.0 if len(all_trades) > 0 else 0.0
    total_r = sum(t["pnl_r"] for t in all_trades)
    
    # Calculate balance equity growth starting at $200 (1% risk per trade)
    equity = START_BAL_USDT
    equity_curve = [equity]
    for t in all_trades:
        risk_amt = equity * RISK_FRAC
        pnl_dollars = risk_amt * t["pnl_r"]
        equity += pnl_dollars
        equity_curve.append(equity)
        
    net_profit_pct = (equity - START_BAL_USDT) / START_BAL_USDT * 100.0

    print(f"  • Winning Trades (TP):          {len(wins)} ({win_rate:.1f}% Win Rate)")
    print(f"  • Losing Trades (SL):           {len(losses)} ({100.0 - win_rate:.1f}% Loss Rate)")
    print(f"  • Net R-Multiple Profit:        +{total_r:.2f} R")
    print(f"  • Initial Starting Equity:      ${START_BAL_USDT:.2f}")
    print(f"  • Final Account Balance:        ${equity:.2f} (+{net_profit_pct:.2f}% Net Return)")
    print(f"===================================================================\n")

    # Save detailed JSON output
    out_dir = "/root/go-trader/scratch" if os.path.exists("/root/go-trader") else "scratch"
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, "fibvol_backtest_results.json")
    with open(out_file, "w") as f:
        json.dump(all_trades, f, indent=2)

    print("=== TRADE BY TRADE DETAILED LOG ===")
    print(f"{'Coin':<10} {'Entry Time (IST)':<18} {'Exit Time (IST)':<18} {'Entry Px':<10} {'SL Px':<10} {'TP Px':<10} {'Outcome':<8} {'PnL (R)'}")
    print("-" * 100)
    for t in all_trades[:40]:
        print(f"{t['symbol']:<10} {t['entry_time']:<18} {t['exit_time']:<18} {t['entry_px']:<10.4f} {t['sl_px']:<10.4f} {t['tp_px']:<10.4f} {t['outcome']:<8} {t['pnl_r']:>+5.1f}R")

if __name__ == "__main__":
    main()
