#!/usr/bin/env python3
"""tools/backtest/backtest_btc_rr_comparison.py — Compare 1:2 RR vs 1:4 RR on 60-Day BTC 15M/1M Dataset.
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
    target_start_ms = end_time_ms - (60 * 24 * 3600 * 1000)
    
    all_candles = []
    cur_end = end_time_ms
    
    print(f"Fetching 60 days of 1m BTC data...")
    while cur_end > target_start_ms:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1m&limit=1500&endTime={cur_end}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
                batch = json.loads(r.read().decode())
        except Exception:
            time.sleep(1)
            continue
            
        if not batch: break
        all_candles.extend(batch)
        oldest_ts = int(batch[0][0])
        cur_end = oldest_ts - 1
        print(f"   Fetched {len(all_candles):,} candles...", end="\r")
        if oldest_ts <= target_start_ms: break
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

def simulate_rr(df_15m, df_1m, rr_ratio=2.0):
    """Simulate 15M signals at 1M execution resolution for a specific RR target."""
    trades = []
    in_trade = False
    current_trade = None
    
    for i in range(1, len(df_15m)):
        t_15m_start = df_15m.index[i]
        t_15m_end = t_15m_start + pd.Timedelta(minutes=15)
        
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
                        t["net_r"] = rr_ratio
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
                        t["net_r"] = rr_ratio
                        trades.append(t)
                        in_trade = False
                        current_trade = None
                        break
                        
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
                    tp_px = entry_px + (rr_ratio * risk_dist)
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
                    tp_px = entry_px - (rr_ratio * risk_dist)
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

def eval_metrics(trades, rr_ratio):
    wins = [t for t in trades if t["outcome"] == "WIN"]
    losses = [t for t in trades if t["outcome"] == "LOSS"]
    win_rate = (len(wins) / len(trades)) * 100.0 if trades else 0.0
    total_r = sum(t["net_r"] for t in trades)
    exp = total_r / len(trades) if trades else 0.0
    gross_win_r = len(wins) * rr_ratio
    gross_loss_r = len(losses) * 1.0
    pf = (gross_win_r / gross_loss_r) if gross_loss_r > 0 else float("inf")
    
    cur_r = 0.0
    peak_r = 0.0
    max_dd_r = 0.0
    for t in trades:
        cur_r += t["net_r"]
        if cur_r > peak_r: peak_r = cur_r
        dd = peak_r - cur_r
        if dd > max_dd_r: max_dd_r = dd
        
    durations = [(t["exit_dt"] - t["entry_dt"]).total_seconds() / 60.0 for t in trades]
    median_dur = np.median(durations) if durations else 0.0
    
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "net_r": total_r,
        "expectancy": exp,
        "pf": pf,
        "max_dd": max_dd_r,
        "median_dur": median_dur
    }

def main():
    df_1m = fetch_btc_1m_60days()
    df_15m = build_15m_and_ha(df_1m)
    
    mid_dt = df_1m.index[-1] - pd.Timedelta(days=30)
    
    print("\n=========================================================================================")
    print("🔥 60-DAY BTC RISK-TO-REWARD RATIO SWEEP (15M STRATEGY / 1M EXECUTION RESOLUTION) 🔥")
    print("=========================================================================================\n")
    
    for rr in [1.5, 2.0, 2.5, 3.0, 4.0]:
        trades_all = simulate_rr(df_15m, df_1m, rr_ratio=rr)
        trades_m1 = [t for t in trades_all if t["entry_dt"] < mid_dt]
        trades_m2 = [t for t in trades_all if t["entry_dt"] >= mid_dt]
        
        m_all = eval_metrics(trades_all, rr)
        m_m1 = eval_metrics(trades_m1, rr)
        m_m2 = eval_metrics(trades_m2, rr)
        
        print(f"=== TARGET 1:{rr:.1f} RISK-TO-REWARD ===")
        print(f"• 60-Day Combined : {m_all['trades']} Trades | Win Rate: {m_all['win_rate']:.2f}% | Net: {m_all['net_r']:+.1f} R | Exp: {m_all['expectancy']:+.3f} R | PF: {m_all['pf']:.2f} | Max DD: -{m_all['max_dd']:.1f} R | Med Dur: {m_all['median_dur']:.0f}m")
        print(f"   ├─ Month 1 (Earlier 30d): {m_m1['trades']} Trades | Win Rate: {m_m1['win_rate']:.2f}% | Net: {m_m1['net_r']:+.1f} R | PF: {m_m1['pf']:.2f} | Max DD: -{m_m1['max_dd']:.1f} R")
        print(f"   └─ Month 2 (Recent 30d) : {m_m2['trades']} Trades | Win Rate: {m_m2['win_rate']:.2f}% | Net: {m_m2['net_r']:+.1f} R | PF: {m_m2['pf']:.2f} | Max DD: -{m_m2['max_dd']:.1f} R\n")

if __name__ == "__main__":
    main()
