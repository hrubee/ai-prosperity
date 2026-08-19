#!/usr/bin/env python3
"""tools/backtest/backtest_heikinashi.py — Comprehensive Multi-Timeframe Heikin-Ashi Backtester (1:4 RR)
Sweeps all 495 CoinDCX Perpetual Futures pairs across 15m, 30m, 1h, 4h timeframes.
"""
import os
import sys
import json
import time
import urllib.request
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd

# Setup SSL context for local/remote fetching
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
PUBLIC_API = "https://public.coindcx.com"
EXCHANGE_API = "https://api.coindcx.com"

def get_active_instruments():
    """Fetch all active futures instruments from CoinDCX."""
    url = f"{EXCHANGE_API}/exchange/v1/derivatives/futures/data/active_instruments"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode())
            return [p for p in data if p.startswith("B-") and p.endswith("_USDT")]
    except Exception as e:
        print(f"Error fetching instruments: {e}")
        return ["B-BTC_USDT", "B-ETH_USDT", "B-SOL_USDT", "B-XRP_USDT", "B-DOGE_USDT"]

def fetch_candles(pair: str, interval: str, limit: int = 500):
    """Fetch OHLCV candles from CoinDCX API."""
    try:
        # CoinDCX public candle API
        if interval in ["1m", "15m", "1h", "1d"]:
            url = f"{PUBLIC_API}/market_data/candles?pair={pair}&interval={interval}&limit={limit}"
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
                rows = json.loads(resp.read().decode())
            if not isinstance(rows, list) or len(rows) < 20:
                return None
            rows = sorted(rows, key=lambda r: r["time"])
            return [[r["time"], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r.get("volume", 0))] for r in rows]

        elif interval == "30m":
            # Fetch 15m and aggregate 2->1
            url = f"{PUBLIC_API}/market_data/candles?pair={pair}&interval=15m&limit={min(1000, limit * 2)}"
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
                rows = json.loads(resp.read().decode())
            if not isinstance(rows, list) or len(rows) < 40:
                return None
            rows = sorted(rows, key=lambda r: r["time"])
            bucket_ms = 30 * 60 * 1000
            buckets = {}
            for r in rows:
                b = (r["time"] // bucket_ms) * bucket_ms
                if b not in buckets:
                    buckets[b] = [b, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r.get("volume", 0))]
                else:
                    cur = buckets[b]
                    cur[2] = max(cur[2], float(r["high"]))
                    cur[3] = min(cur[3], float(r["low"]))
                    cur[4] = float(r["close"])
                    cur[5] += float(r.get("volume", 0))
            return [buckets[k] for k in sorted(buckets.keys())][-limit:]

        elif interval == "4h":
            # Fetch 1h and aggregate 4->1
            url = f"{PUBLIC_API}/market_data/candles?pair={pair}&interval=1h&limit={min(1000, limit * 4)}"
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
                rows = json.loads(resp.read().decode())
            if not isinstance(rows, list) or len(rows) < 40:
                return None
            rows = sorted(rows, key=lambda r: r["time"])
            bucket_ms = 4 * 3600 * 1000
            buckets = {}
            for r in rows:
                b = (r["time"] // bucket_ms) * bucket_ms
                if b not in buckets:
                    buckets[b] = [b, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r.get("volume", 0))]
                else:
                    cur = buckets[b]
                    cur[2] = max(cur[2], float(r["high"]))
                    cur[3] = min(cur[3], float(r["low"]))
                    cur[4] = float(r["close"])
                    cur[5] += float(r.get("volume", 0))
            return [buckets[k] for k in sorted(buckets.keys())][-limit:]
            
    except Exception:
        return None

def compute_heikin_ashi(klines):
    """
    Computes Heikin-Ashi arrays from raw klines.
    Returns:
    times, opens, highs, lows, closes, vols, ha_opens, ha_highs, ha_lows, ha_closes
    """
    n = len(klines)
    times = np.array([k[0] for k in klines])
    opens = np.array([k[1] for k in klines])
    highs = np.array([k[2] for k in klines])
    lows = np.array([k[3] for k in klines])
    closes = np.array([k[4] for k in klines])
    vols = np.array([k[5] for k in klines])

    ha_closes = (opens + highs + lows + closes) / 4.0
    ha_opens = np.zeros(n)
    ha_opens[0] = (opens[0] + closes[0]) / 2.0

    for i in range(1, n):
        ha_opens[i] = (ha_opens[i-1] + ha_closes[i-1]) / 2.0

    ha_highs = np.maximum(highs, np.maximum(ha_opens, ha_closes))
    ha_lows = np.minimum(lows, np.minimum(ha_opens, ha_closes))

    return times, opens, highs, lows, closes, vols, ha_opens, ha_highs, ha_lows, ha_closes

def simulate_heikinashi_pair(pair: str, interval: str, klines, rr_ratio: float = 4.0):
    """
    Simulates the Heikin-Ashi 1:4 RR strategy on a single coin and timeframe.
    Rules:
    - Long Setup: Green HA Candle with NO bottom wick (ha_low == ha_open).
      SL = ha_low (or normal low of trigger candle)
      Entry = normal Close of next candle (i+1)
      TP = Entry + 4 * (Entry - SL)
    - Short Setup: Red HA Candle with NO upper wick (ha_high == ha_open).
      SL = ha_high (or normal high of trigger candle)
      Entry = normal Close of next candle (i+1)
      TP = Entry - 4 * (SL - Entry)
    """
    if not klines or len(klines) < 30:
        return []

    times, opens, highs, lows, closes, vols, ha_opens, ha_highs, ha_lows, ha_closes = compute_heikin_ashi(klines)
    n = len(klines)
    trades = []
    
    in_trade_until = -1

    for i in range(1, n - 2):
        if i <= in_trade_until:
            continue

        # Check Trigger on candle i
        is_green = ha_closes[i] > ha_opens[i]
        is_red = ha_closes[i] < ha_opens[i]
        
        # Flat bottom: ha_low == ha_open (no lower wick)
        has_no_lower_wick = is_green and np.isclose(ha_lows[i], ha_opens[i], rtol=0.0005)
        # Flat top: ha_high == ha_open (no upper wick)
        has_no_upper_wick = is_red and np.isclose(ha_highs[i], ha_opens[i], rtol=0.0005)

        # ── 1. LONG SIGNAL ──
        if has_no_lower_wick:
            sl_px = lows[i] # or ha_lows[i]
            entry_idx = i + 1
            entry_px = closes[entry_idx]
            
            risk = entry_px - sl_px
            if risk > 0 and (risk / entry_px) >= 0.002: # Min 0.2% risk distance
                tp_px = entry_px + (rr_ratio * risk)
                
                # Forward simulate from entry_idx + 1
                result = None
                exit_px = None
                exit_idx = None
                
                for j in range(entry_idx + 1, min(n, entry_idx + 100)):
                    # Check SL hit
                    sl_hit = (lows[j] <= sl_px)
                    # Check TP hit
                    tp_hit = (highs[j] >= tp_px)
                    
                    if sl_hit and tp_hit:
                        # Ambiguous bar: conservative assumption is loss
                        result = "LOSS"
                        exit_px = sl_px
                        exit_idx = j
                        break
                    elif tp_hit:
                        result = "WIN"
                        exit_px = tp_px
                        exit_idx = j
                        break
                    elif sl_hit:
                        result = "LOSS"
                        exit_px = sl_px
                        exit_idx = j
                        break
                
                if result:
                    trades.append({
                        "pair": pair,
                        "interval": interval,
                        "side": "LONG",
                        "trigger_time": int(times[i]),
                        "entry_time": int(times[entry_idx]),
                        "exit_time": int(times[exit_idx]),
                        "entry_px": entry_px,
                        "sl_px": sl_px,
                        "tp_px": tp_px,
                        "exit_px": exit_px,
                        "risk_dist": risk,
                        "risk_pct": round((risk / entry_px) * 100, 2),
                        "result": result,
                        "r_pnl": rr_ratio if result == "WIN" else -1.0,
                        "holding_bars": exit_idx - entry_idx
                    })
                    in_trade_until = exit_idx

        # ── 2. SHORT SIGNAL ──
        elif has_no_upper_wick:
            sl_px = highs[i] # or ha_highs[i]
            entry_idx = i + 1
            entry_px = closes[entry_idx]
            
            risk = sl_px - entry_px
            if risk > 0 and (risk / entry_px) >= 0.002: # Min 0.2% risk distance
                tp_px = entry_px - (rr_ratio * risk)
                
                # Forward simulate
                result = None
                exit_px = None
                exit_idx = None
                
                for j in range(entry_idx + 1, min(n, entry_idx + 100)):
                    # Check SL hit
                    sl_hit = (highs[j] >= sl_px)
                    # Check TP hit
                    tp_hit = (lows[j] <= tp_px)
                    
                    if sl_hit and tp_hit:
                        result = "LOSS"
                        exit_px = sl_px
                        exit_idx = j
                        break
                    elif tp_hit:
                        result = "WIN"
                        exit_px = tp_px
                        exit_idx = j
                        break
                    elif sl_hit:
                        result = "LOSS"
                        exit_px = sl_px
                        exit_idx = j
                        break
                
                if result:
                    trades.append({
                        "pair": pair,
                        "interval": interval,
                        "side": "SHORT",
                        "trigger_time": int(times[i]),
                        "entry_time": int(times[entry_idx]),
                        "exit_time": int(times[exit_idx]),
                        "entry_px": entry_px,
                        "sl_px": sl_px,
                        "tp_px": tp_px,
                        "exit_px": exit_px,
                        "risk_dist": risk,
                        "risk_pct": round((risk / entry_px) * 100, 2),
                        "result": result,
                        "r_pnl": rr_ratio if result == "WIN" else -1.0,
                        "holding_bars": exit_idx - entry_idx
                    })
                    in_trade_until = exit_idx

    return trades

def run_backtest_for_timeframe(instruments, interval: str, max_workers: int = 25):
    print(f"\n==========================================================================")
    print(f"🚀 RUNNING BACKTEST: {interval.upper()} TIMEFRAME ACROSS {len(instruments)} INSTRUMENTS (1:4 RR)")
    print(f"==========================================================================")
    
    all_trades = []
    fetched_count = 0
    start_t = time.time()
    
    def process_coin(p):
        kl = fetch_candles(p, interval, limit=350)
        if not kl:
            return []
        return simulate_heikinashi_pair(p, interval, kl, rr_ratio=4.0)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_coin, p): p for p in instruments}
        for f in as_completed(futures):
            res = f.result()
            if res:
                all_trades.extend(res)
            fetched_count += 1
            if fetched_count % 100 == 0:
                print(f"  Processed {fetched_count}/{len(instruments)} coins... ({len(all_trades)} trades recorded)")

    dur = time.time() - start_t
    print(f"✅ Finished {interval.upper()} sweep in {dur:.1f}s. Total trades generated: {len(all_trades)}")
    return all_trades

def print_performance_metrics(trades, label=""):
    if not trades:
        print(f"No trades found for {label}")
        return {}

    df = pd.DataFrame(trades)
    total_trades = len(df)
    wins = len(df[df["result"] == "WIN"])
    losses = len(df[df["result"] == "LOSS"])
    win_rate = (wins / total_trades) * 100.0
    
    total_r = df["r_pnl"].sum()
    long_trades = df[df["side"] == "LONG"]
    short_trades = df[df["side"] == "SHORT"]
    
    long_win_rate = (len(long_trades[long_trades["result"] == "WIN"]) / len(long_trades) * 100.0) if len(long_trades) > 0 else 0
    short_win_rate = (len(short_trades[short_trades["result"] == "WIN"]) / len(short_trades) * 100.0) if len(short_trades) > 0 else 0
    
    # Expectancy in R: (WinRate * 4R) - (LossRate * 1R)
    expectancy = ((win_rate / 100.0) * 4.0) - (((100.0 - win_rate) / 100.0) * 1.0)
    
    # Cumulative R & Max Drawdown
    df["cum_r"] = df["r_pnl"].cumsum()
    cummax = df["cum_r"].cummax()
    dd = cummax - df["cum_r"]
    max_dd = dd.max()
    
    # Profit factor: (Total Win R) / (Total Loss R)
    total_win_r = wins * 4.0
    total_loss_r = losses * 1.0
    profit_factor = (total_win_r / total_loss_r) if total_loss_r > 0 else float("inf")
    
    # Avg holding bars
    avg_bars = df["holding_bars"].mean()

    print(f"\n📈 ── PERFORMANCE SUMMARY: {label.upper()} ──")
    print(f"  • Total Trades       : {total_trades:,} (Longs: {len(long_trades):,}, Shorts: {len(short_trades):,})")
    print(f"  • Win Rate           : {win_rate:.2f}% (Wins: {wins:,}, Losses: {losses:,})")
    print(f"  • Long Win Rate      : {long_win_rate:.2f}% | Short Win Rate: {short_win_rate:.2f}%")
    print(f"  • Risk-to-Reward     : 1:4 (Win = +4.0R, Loss = -1.0R)")
    print(f"  • Net Return (R)     : {total_r:+,.1f} R")
    print(f"  • Expectancy (per trade): {expectancy:+.3f} R")
    print(f"  • Profit Factor      : {profit_factor:.2f}")
    print(f"  • Max Drawdown       : {max_dd:,.1f} R")
    print(f"  • Avg Holding Time   : {avg_bars:.1f} bars")
    
    return {
        "interval": label,
        "total_trades": total_trades,
        "win_rate": round(win_rate, 2),
        "total_r": round(total_r, 1),
        "expectancy": round(expectancy, 3),
        "profit_factor": round(profit_factor, 2),
        "max_dd": round(max_dd, 1),
    }

def main():
    print("=" * 80)
    print("💎 HEIKIN-ASHI WICKLESS (1:4 RR) FULL MARKET BACKTESTER — COINDCX FUTURES 💎")
    print("=" * 80)
    
    instruments = get_active_instruments()
    print(f"Discovered {len(instruments)} active CoinDCX futures pairs.")
    
    timeframes = ["15m", "30m", "1h", "4h"]
    all_timeframe_trades = {}
    summary_results = []
    
    for tf in timeframes:
        tf_trades = run_backtest_for_timeframe(instruments, tf, max_workers=30)
        all_timeframe_trades[tf] = tf_trades
        metrics = print_performance_metrics(tf_trades, label=f"Heikin-Ashi {tf}")
        summary_results.append(metrics)
        
        # Save CSV for each timeframe
        if tf_trades:
            csv_path = f"/root/trading-bot/heikinashi_{tf}_trades.csv" if os.path.exists("/root/trading-bot") else f"heikinashi_{tf}_trades.csv"
            pd.DataFrame(tf_trades).to_csv(csv_path, index=False)
            print(f"  -> Saved {len(tf_trades)} trades to {csv_path}")

    # Comparative Table
    print("\n" + "=" * 80)
    print("🏆 MULTI-TIMEFRAME HEIKIN-ASHI STRATEGY COMPARISON (1:4 RR)")
    print("=" * 80)
    print(f"{'Timeframe':<12} | {'Total Trades':<14} | {'Win Rate %':<12} | {'Expectancy (R)':<16} | {'Total Net (R)':<14} | {'Profit Factor':<12} | {'Max DD (R)':<10}")
    print("-" * 92)
    for s in summary_results:
        print(f"{s.get('interval', ''):<12} | {s.get('total_trades', 0):<14,d} | {s.get('win_rate', 0):<12.2f}% | {s.get('expectancy', 0):<+16.3f} | {s.get('total_r', 0):<+14.1f} | {s.get('profit_factor', 0):<12.2f} | {s.get('max_dd', 0):<10.1f}")
    print("=" * 80)

if __name__ == "__main__":
    main()
