#!/usr/bin/env python3
"""tools/backtest/backtest_btc_2months_15m_1m.py — 2-Month (60-Day) BTC Backtest (15M Strategy / 1M Granularity).

- 60 Days of continuous 1-minute historical data (~86,400 1m candles).
- Split and analyzed by Month 1 (Days 31-60), Month 2 (Days 1-30), and Combined (60 Days).
- 15M Heikin-Ashi Flat Wick (1:4 RR) simulated at 1M intra-bar execution resolution.
"""
import os
import sys
import json
import time
import ssl
import urllib.request
import datetime
import numpy as np
import pandas as pd

def fetch_btc_1m_60days():
    """Fetch 60 days of 1m BTC historical candles (~86,400 candles) via Binance Futures API."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    end_time_ms = int(time.time() * 1000)
    # 60 days = 60 * 24 * 3600 * 1000 ms
    target_start_ms = end_time_ms - (60 * 24 * 3600 * 1000)
    
    all_candles = []
    cur_end = end_time_ms
    
    print(f"Fetching 60 days of 1m BTC data from {datetime.datetime.fromtimestamp(target_start_ms/1000, tz=datetime.timezone.utc)} to {datetime.datetime.fromtimestamp(end_time_ms/1000, tz=datetime.timezone.utc)}...")
    
    while cur_end > target_start_ms:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1m&limit=1500&endTime={cur_end}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
                batch = json.loads(r.read().decode())
        except Exception as e:
            print(f"Network retry: {e}")
            time.sleep(1)
            continue
            
        if not batch:
            break
            
        all_candles.extend(batch)
        oldest_ts = int(batch[0][0])
        cur_end = oldest_ts - 1
        print(f"   Fetched {len(all_candles):,} candles (Oldest: {datetime.datetime.fromtimestamp(oldest_ts/1000, tz=datetime.timezone.utc).strftime('%Y-%m-%d %H:%M')})...", end="\r")
        if oldest_ts <= target_start_ms:
            break
        time.sleep(0.04)
        
    print(f"\nTotal 1m candles fetched: {len(all_candles):,}")
    
    df = pd.DataFrame(all_candles, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["open_time"] = df["open_time"].astype(int)
    df.drop_duplicates(subset=["open_time"], inplace=True)
    df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.sort_values("dt", inplace=True)
    df.set_index("dt", inplace=True)
    return df

def build_15m_and_ha(df_1m):
    """Resample 1m candles into 15m candles and compute 15M Heikin-Ashi series."""
    df_15m = df_1m.resample("15min", closed="left", label="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "open_time": "first"
    }).dropna()
    
    # 15M Heikin-Ashi
    ha_close = (df_15m["open"] + df_15m["high"] + df_15m["low"] + df_15m["close"]) / 4.0
    ha_open = np.zeros(len(df_15m))
    ha_open[0] = (df_15m["open"].iloc[0] + df_15m["close"].iloc[0]) / 2.0
    
    for i in range(1, len(df_15m)):
        ha_open[i] = (ha_open[i - 1] + ha_close.iloc[i - 1]) / 2.0
        
    ha_high = np.maximum.reduce([df_15m["high"].values, ha_open, ha_close.values])
    ha_low = np.minimum.reduce([df_15m["low"].values, ha_open, ha_close.values])
    
    df_15m["ha_open"] = ha_open
    df_15m["ha_high"] = ha_high
    df_15m["ha_low"] = ha_low
    df_15m["ha_close"] = ha_close.values
    return df_15m

def simulate_trades(df_15m, df_1m):
    """Simulate 15M signals at 1M intra-bar execution resolution with exact HA SL."""
    trades = []
    in_trade = False
    current_trade = None
    
    for i in range(1, len(df_15m)):
        t_15m_start = df_15m.index[i]
        t_15m_end = t_15m_start + pd.Timedelta(minutes=15)
        
        # 1. Manage active trade intra-bar minute-by-minute
        if in_trade:
            bars_1m = df_1m.loc[t_15m_start:t_15m_end - pd.Timedelta(milliseconds=1)]
            for t_1m, row in bars_1m.iterrows():
                high = row["high"]
                low = row["low"]
                t = current_trade
                
                if t["side"] == "LONG":
                    fav = (high - t["entry_px"]) / t["risk_dist"]
                    adv = (t["entry_px"] - low) / t["risk_dist"]
                    t["mfe"] = max(t["mfe"], fav)
                    t["mae"] = max(t["mae"], adv)
                    
                    if low <= t["sl_px"]:
                        t["exit_px"] = t["sl_px"]
                        t["exit_dt"] = t_1m
                        t["outcome"] = "LOSS"
                        t["net_r"] = -1.0
                        trades.append(t)
                        in_trade = False
                        current_trade = None
                        break
                    elif high >= t["tp_px"]:
                        t["exit_px"] = t["tp_px"]
                        t["exit_dt"] = t_1m
                        t["outcome"] = "WIN"
                        t["net_r"] = 4.0
                        trades.append(t)
                        in_trade = False
                        current_trade = None
                        break
                else: # SHORT
                    fav = (t["entry_px"] - low) / t["risk_dist"]
                    adv = (high - t["entry_px"]) / t["risk_dist"]
                    t["mfe"] = max(t["mfe"], fav)
                    t["mae"] = max(t["mae"], adv)
                    
                    if high >= t["sl_px"]:
                        t["exit_px"] = t["sl_px"]
                        t["exit_dt"] = t_1m
                        t["outcome"] = "LOSS"
                        t["net_r"] = -1.0
                        trades.append(t)
                        in_trade = False
                        current_trade = None
                        break
                    elif low <= t["tp_px"]:
                        t["exit_px"] = t["tp_px"]
                        t["exit_dt"] = t_1m
                        t["outcome"] = "WIN"
                        t["net_r"] = 4.0
                        trades.append(t)
                        in_trade = False
                        current_trade = None
                        break
                        
        # 2. Check new signal entry at 15m candle close
        if not in_trade and i < len(df_15m) - 2:
            ha_o = df_15m["ha_open"].iloc[i]
            ha_h = df_15m["ha_high"].iloc[i]
            ha_l = df_15m["ha_low"].iloc[i]
            ha_c = df_15m["ha_close"].iloc[i]
            
            prev_ha_o = df_15m["ha_open"].iloc[i - 1]
            prev_ha_c = df_15m["ha_close"].iloc[i - 1]
            prev_ha_l = df_15m["ha_low"].iloc[i - 1]
            prev_ha_h = df_15m["ha_high"].iloc[i - 1]
            
            is_flat_bottom = abs(ha_l - ha_o) / ha_o < 1e-5
            is_green = ha_c > ha_o
            prev_not_flat_green = not (prev_ha_c > prev_ha_o and abs(prev_ha_l - prev_ha_o) / prev_ha_o < 1e-5)
            
            is_flat_top = abs(ha_h - ha_o) / ha_o < 1e-5
            is_red = ha_c < ha_o
            prev_not_flat_red = not (prev_ha_c < prev_ha_o and abs(prev_ha_h - prev_ha_o) / prev_ha_o < 1e-5)
            
            if is_green and is_flat_bottom and prev_not_flat_green:
                entry_px = df_15m["close"].iloc[i]
                sl_px = df_15m["ha_low"].iloc[i]
                risk_dist = entry_px - sl_px
                if risk_dist > 0 and (risk_dist / entry_px) >= 0.0003:
                    tp_px = entry_px + (4.0 * risk_dist)
                    current_trade = {
                        "trade_id": len(trades) + 1,
                        "side": "LONG",
                        "entry_dt": df_15m.index[i] + pd.Timedelta(minutes=15),
                        "entry_px": entry_px,
                        "sl_px": sl_px,
                        "tp_px": tp_px,
                        "risk_dist": risk_dist,
                        "risk_pct": (risk_dist / entry_px) * 100.0,
                        "mfe": 0.0,
                        "mae": 0.0
                    }
                    in_trade = True
                    
            elif is_red and is_flat_top and prev_not_flat_red:
                entry_px = df_15m["close"].iloc[i]
                sl_px = df_15m["ha_high"].iloc[i]
                risk_dist = sl_px - entry_px
                if risk_dist > 0 and (risk_dist / entry_px) >= 0.0003:
                    tp_px = entry_px - (4.0 * risk_dist)
                    current_trade = {
                        "trade_id": len(trades) + 1,
                        "side": "SHORT",
                        "entry_dt": df_15m.index[i] + pd.Timedelta(minutes=15),
                        "entry_px": entry_px,
                        "sl_px": sl_px,
                        "tp_px": tp_px,
                        "risk_dist": risk_dist,
                        "risk_pct": (risk_dist / entry_px) * 100.0,
                        "mfe": 0.0,
                        "mae": 0.0
                    }
                    in_trade = True

    return trades

def print_period_stats(trades, label):
    if not trades:
        print(f"[{label}] No trades.")
        return
        
    wins = [t for t in trades if t["outcome"] == "WIN"]
    losses = [t for t in trades if t["outcome"] == "LOSS"]
    
    long_trades = [t for t in trades if t["side"] == "LONG"]
    short_trades = [t for t in trades if t["side"] == "SHORT"]
    
    long_wins = [t for t in long_trades if t["outcome"] == "WIN"]
    short_wins = [t for t in short_trades if t["outcome"] == "WIN"]
    
    total_r = sum(t["net_r"] for t in trades)
    long_r = sum(t["net_r"] for t in long_trades)
    short_r = sum(t["net_r"] for t in short_trades)
    
    win_rate = (len(wins) / len(trades)) * 100.0
    long_wr = (len(long_wins) / len(long_trades) * 100.0) if long_trades else 0.0
    short_wr = (len(short_wins) / len(short_trades) * 100.0) if short_trades else 0.0
    
    gross_win_r = len(wins) * 4.0
    gross_loss_r = len(losses) * 1.0
    pf = (gross_win_r / gross_loss_r) if gross_loss_r > 0 else float("inf")
    
    # Max DD
    cur_r = 0.0
    peak_r = 0.0
    max_dd_r = 0.0
    for t in trades:
        cur_r += t["net_r"]
        if cur_r > peak_r: peak_r = cur_r
        dd = peak_r - cur_r
        if dd > max_dd_r: max_dd_r = dd
        
    loss_reach_1r = sum(1 for t in losses if t["mfe"] >= 1.0)
    loss_reach_2r = sum(1 for t in losses if t["mfe"] >= 2.0)
    
    print(f"\n=========================================================================================")
    print(f"📊 {label}")
    print(f"=========================================================================================")
    print(f"• Total Trades : {len(trades)}")
    print(f"• Wins (+4.0R) : {len(wins)} ({win_rate:.2f}%)")
    print(f"• Losses (-1.0R): {len(losses)} ({len(losses)/len(trades)*100:.2f}%)")
    print(f"• Net PnL (R)  : {total_r:+.1f} R")
    print(f"• Expectancy   : {total_r / len(trades):+.3f} R per trade")
    print(f"• Profit Factor: {pf:.2f}")
    print(f"• Max DD (R)   : -{max_dd_r:.1f} R")
    print(f"• Longs        : {len(long_trades)} trades | {long_wr:.1f}% WR | {long_r:+.1f} R")
    print(f"• Shorts       : {len(short_trades)} trades | {short_wr:.1f}% WR | {short_r:+.1f} R")
    print(f"• Losses reaching >= +1.0R : {loss_reach_1r} ({loss_reach_1r/len(losses)*100:.1f}% of losses)")
    print(f"• Losses reaching >= +2.0R : {loss_reach_2r} ({loss_reach_2r/len(losses)*100:.1f}% of losses)")

def main():
    df_1m = fetch_btc_1m_60days()
    df_15m = build_15m_and_ha(df_1m)
    
    print("\nSimulating 60 days of trades minute-by-minute...")
    trades = simulate_trades(df_15m, df_1m)
    
    # Split by midpoint (30 days ago)
    mid_dt = df_1m.index[-1] - pd.Timedelta(days=30)
    
    m1_trades = [t for t in trades if t["entry_dt"] < mid_dt]
    m2_trades = [t for t in trades if t["entry_dt"] >= mid_dt]
    
    print_period_stats(m1_trades, f"MONTH 1 (Earlier 30 Days: {df_1m.index[0].strftime('%Y-%m-%d')} to {mid_dt.strftime('%Y-%m-%d')})")
    print_period_stats(m2_trades, f"MONTH 2 (Recent 30 Days: {mid_dt.strftime('%Y-%m-%d')} to {df_1m.index[-1].strftime('%Y-%m-%d')})")
    print_period_stats(trades, f"COMBINED 2-MONTH TOTAL (60 Days: {df_1m.index[0].strftime('%Y-%m-%d')} to {df_1m.index[-1].strftime('%Y-%m-%d')})")

if __name__ == "__main__":
    main()
