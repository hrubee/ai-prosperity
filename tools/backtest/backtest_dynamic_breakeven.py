#!/usr/bin/env python3
"""tools/backtest/backtest_dynamic_breakeven.py — 60-Day BTC Backtest with Dynamic Break-Even & Profit Lock.

Mechanics:
- Initial Stop Loss: Exact Heikin-Ashi Flat Wick (ha_low for Long / ha_high for Short).
- Dynamic Break-Even Trigger: When MFE reaches >= +BE_TRIGGER_R, Stop Loss is moved to Entry (0.0R).
- Optional Profit Lock: When MFE reaches >= +LOCK_TRIGGER_R, Stop Loss is moved to +LOCK_SL_R (e.g. +1.0R).
- Full 1M intra-bar resolution simulation across 87,000 candles over 60 days.
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
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    end_time_ms = int(time.time() * 1000)
    target_start_ms = end_time_ms - (60 * 24 * 3600 * 1000)
    all_candles = []
    cur_end = end_time_ms
    
    print("Fetching 60 days of 1m BTC data...")
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
        time.sleep(0.03)
        
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

def simulate_with_dynamic_be(df_15m, df_1m, rr_ratio=2.25, be_trigger_r=1.5, lock_trigger_r=None, lock_sl_r=1.0):
    """Simulate trades with dynamic break-even and optional profit lock at 1m intra-bar resolution."""
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
                    
                    # 1. Check Take Profit Hit
                    if high >= t["tp_px"]:
                        t["exit_px"] = t["tp_px"]
                        t["exit_dt"] = t_1m
                        t["outcome"] = "WIN"
                        t["net_r"] = rr_ratio
                        trades.append(t)
                        in_trade = False
                        current_trade = None
                        break
                        
                    # 2. Dynamic BE / Profit Lock Adjustments
                    if lock_trigger_r and t["mfe"] >= lock_trigger_r:
                        # Lock profit at +lock_sl_r
                        new_sl = t["entry_px"] + (lock_sl_r * t["risk_dist"])
                        t["current_sl_px"] = max(t["current_sl_px"], new_sl)
                        t["sl_status"] = f"LOCKED_{lock_sl_r}R"
                    elif be_trigger_r and t["mfe"] >= be_trigger_r:
                        # Move to Break-Even (Entry Price)
                        t["current_sl_px"] = max(t["current_sl_px"], t["entry_px"])
                        t["sl_status"] = "BREAK_EVEN"
                        
                    # 3. Check Stop Loss Hit (Dynamic SL)
                    if low <= t["current_sl_px"]:
                        t["exit_px"] = t["current_sl_px"]
                        t["exit_dt"] = t_1m
                        if t.get("sl_status") == "BREAK_EVEN":
                            t["outcome"] = "BREAK_EVEN"
                            t["net_r"] = 0.0
                        elif t.get("sl_status") and "LOCKED" in t["sl_status"]:
                            t["outcome"] = "PROFIT_LOCK"
                            t["net_r"] = lock_sl_r
                        else:
                            t["outcome"] = "LOSS"
                            t["net_r"] = -1.0
                        trades.append(t)
                        in_trade = False
                        current_trade = None
                        break
                        
                else: # SHORT
                    fav = (t["entry_px"] - low) / t["risk_dist"]
                    adv = (high - t["entry_px"]) / t["risk_dist"]
                    t["mfe"] = max(t["mfe"], fav)
                    t["mae"] = max(t["mae"], adv)
                    
                    # 1. Check Take Profit Hit
                    if low <= t["tp_px"]:
                        t["exit_px"] = t["tp_px"]
                        t["exit_dt"] = t_1m
                        t["outcome"] = "WIN"
                        t["net_r"] = rr_ratio
                        trades.append(t)
                        in_trade = False
                        current_trade = None
                        break
                        
                    # 2. Dynamic BE / Profit Lock Adjustments
                    if lock_trigger_r and t["mfe"] >= lock_trigger_r:
                        new_sl = t["entry_px"] - (lock_sl_r * t["risk_dist"])
                        t["current_sl_px"] = min(t["current_sl_px"], new_sl)
                        t["sl_status"] = f"LOCKED_{lock_sl_r}R"
                    elif be_trigger_r and t["mfe"] >= be_trigger_r:
                        t["current_sl_px"] = min(t["current_sl_px"], t["entry_px"])
                        t["sl_status"] = "BREAK_EVEN"
                        
                    # 3. Check Stop Loss Hit
                    if high >= t["current_sl_px"]:
                        t["exit_px"] = t["current_sl_px"]
                        t["exit_dt"] = t_1m
                        if t.get("sl_status") == "BREAK_EVEN":
                            t["outcome"] = "BREAK_EVEN"
                            t["net_r"] = 0.0
                        elif t.get("sl_status") and "LOCKED" in t["sl_status"]:
                            t["outcome"] = "PROFIT_LOCK"
                            t["net_r"] = lock_sl_r
                        else:
                            t["outcome"] = "LOSS"
                            t["net_r"] = -1.0
                        trades.append(t)
                        in_trade = False
                        current_trade = None
                        break

        # Check new signal entry at 15m candle close
        if not in_trade and i < len(df_15m) - 2:
            ha_o = df_15m["ha_open"].iloc[i]
            ha_h = df_15m["ha_high"].iloc[i]
            ha_l = df_15m["ha_low"].iloc[i]
            ha_c = df_15m["ha_close"].iloc[i]
            close_px = df_15m["close"].iloc[i]
            
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
                entry_px = close_px
                sl_px = df_15m["ha_low"].iloc[i]
                risk_dist = entry_px - sl_px
                if risk_dist > 0 and (risk_dist / entry_px) >= 0.0003:
                    tp_px = entry_px + (rr_ratio * risk_dist)
                    current_trade = {
                        "trade_id": len(trades) + 1,
                        "side": "LONG",
                        "entry_dt": df_15m.index[i] + pd.Timedelta(minutes=15),
                        "entry_px": entry_px,
                        "initial_sl_px": sl_px,
                        "current_sl_px": sl_px,
                        "tp_px": tp_px,
                        "risk_dist": risk_dist,
                        "risk_pct": (risk_dist / entry_px) * 100.0,
                        "mfe": 0.0,
                        "mae": 0.0,
                        "sl_status": "INITIAL"
                    }
                    in_trade = True
                    
            elif is_red and is_flat_top and prev_not_flat_red:
                entry_px = close_px
                sl_px = df_15m["ha_high"].iloc[i]
                risk_dist = sl_px - entry_px
                if risk_dist > 0 and (risk_dist / entry_px) >= 0.0003:
                    tp_px = entry_px - (rr_ratio * risk_dist)
                    current_trade = {
                        "trade_id": len(trades) + 1,
                        "side": "SHORT",
                        "entry_dt": df_15m.index[i] + pd.Timedelta(minutes=15),
                        "entry_px": entry_px,
                        "initial_sl_px": sl_px,
                        "current_sl_px": sl_px,
                        "tp_px": tp_px,
                        "risk_dist": risk_dist,
                        "risk_pct": (risk_dist / entry_px) * 100.0,
                        "mfe": 0.0,
                        "mae": 0.0,
                        "sl_status": "INITIAL"
                    }
                    in_trade = True

    return trades

def eval_be_metrics(trades, rr_ratio):
    if not trades:
        return {"trades": 0, "wins": 0, "be": 0, "losses": 0, "win_rate": 0, "be_rate": 0, "net_r": 0, "expectancy": 0, "pf": 0, "max_dd": 0}
    
    wins = [t for t in trades if t["outcome"] == "WIN"]
    be_trades = [t for t in trades if t["outcome"] == "BREAK_EVEN"]
    locked_wins = [t for t in trades if t["outcome"] == "PROFIT_LOCK"]
    losses = [t for t in trades if t["outcome"] == "LOSS"]
    
    win_rate = ((len(wins) + len(locked_wins)) / len(trades)) * 100.0
    be_rate = (len(be_trades) / len(trades)) * 100.0
    total_r = sum(t["net_r"] for t in trades)
    exp = total_r / len(trades)
    
    gross_win_r = sum(t["net_r"] for t in trades if t["net_r"] > 0)
    gross_loss_r = sum(abs(t["net_r"]) for t in trades if t["net_r"] < 0)
    pf = (gross_win_r / gross_loss_r) if gross_loss_r > 0 else float("inf")
    
    cur_r = 0.0
    peak_r = 0.0
    max_dd_r = 0.0
    for t in trades:
        cur_r += t["net_r"]
        if cur_r > peak_r: peak_r = cur_r
        dd = peak_r - cur_r
        if dd > max_dd_r: max_dd_r = dd
        
    return {
        "trades": len(trades),
        "wins": len(wins),
        "locked": len(locked_wins),
        "be": len(be_trades),
        "losses": len(losses),
        "win_rate": win_rate,
        "be_rate": be_rate,
        "net_r": total_r,
        "expectancy": exp,
        "pf": pf,
        "max_dd": max_dd_r
    }

def main():
    df_1m = fetch_btc_1m_60days()
    df_15m = build_15m_and_ha(df_1m)
    mid_dt = df_1m.index[-1] - pd.Timedelta(days=30)
    
    configs = [
        # (label, rr, be_trig, lock_trig, lock_sl)
        ("Baseline (No BE) - 1:2.25 RR", 2.25, None, None, 1.0),
        ("Dynamic BE @ +1.0R - 1:2.25 RR", 2.25, 1.0, None, 1.0),
        ("Dynamic BE @ +1.25R - 1:2.25 RR", 2.25, 1.25, None, 1.0),
        ("Dynamic BE @ +1.5R - 1:2.25 RR", 2.25, 1.5, None, 1.0),
        ("Dynamic BE @ +1.75R - 1:2.25 RR", 2.25, 1.75, None, 1.0),
        ("Dynamic BE @ +1.5R + Profit Lock (+1.0R @ +2.0R) - 1:2.25 RR", 2.25, 1.5, 2.0, 1.0),
        
        # Also test on 1:4.0 RR
        ("Baseline (No BE) - 1:4.0 RR", 4.0, None, None, 1.0),
        ("Dynamic BE @ +1.5R - 1:4.0 RR", 4.0, 1.5, None, 1.0),
        ("Dynamic BE @ +1.5R + Profit Lock (+2.0R @ +2.5R) - 1:4.0 RR", 4.0, 1.5, 2.5, 2.0),
        
        # Test on 1:2.5 RR
        ("Baseline (No BE) - 1:2.5 RR", 2.5, None, None, 1.0),
        ("Dynamic BE @ +1.5R - 1:2.5 RR", 2.5, 1.5, None, 1.0),
        ("Dynamic BE @ +1.5R + Profit Lock (+1.0R @ +2.0R) - 1:2.5 RR", 2.5, 1.5, 2.0, 1.0),
    ]
    
    print("\n=========================================================================================")
    print("🔥 60-DAY BTC DYNAMIC BREAK-EVEN & PROFIT LOCK MATRIX (15M / 1M RESOLUTION) 🔥")
    print("=========================================================================================\n")
    
    for label, rr, be_trig, lock_trig, lock_sl in configs:
        trades_all = simulate_with_dynamic_be(df_15m, df_1m, rr_ratio=rr, be_trigger_r=be_trig, lock_trigger_r=lock_trig, lock_sl_r=lock_sl)
        trades_m1 = [t for t in trades_all if t["entry_dt"] < mid_dt]
        trades_m2 = [t for t in trades_all if t["entry_dt"] >= mid_dt]
        
        m_all = eval_be_metrics(trades_all, rr)
        m_m1 = eval_be_metrics(trades_m1, rr)
        m_m2 = eval_be_metrics(trades_m2, rr)
        
        print(f"=== {label} ===")
        print(f"• 60-Day Combined : {m_all['trades']:3d} Trades | Win: {m_all['win_rate']:5.2f}% | BE: {m_all['be_rate']:5.2f}% | Net: {m_all['net_r']:+6.1f} R | Exp: {m_all['expectancy']:+6.3f} R | PF: {m_all['pf']:4.2f} | Max DD: -{m_all['max_dd']:4.1f} R")
        print(f"   ├─ Month 1 (Chop) : {m_m1['trades']:3d} Trades | Win: {m_m1['win_rate']:5.2f}% | BE: {m_m1['be_rate']:5.2f}% | Net: {m_m1['net_r']:+6.1f} R | PF: {m_m1['pf']:4.2f} | Max DD: -{m_m1['max_dd']:4.1f} R")
        print(f"   └─ Month 2 (Trend): {m_m2['trades']:3d} Trades | Win: {m_m2['win_rate']:5.2f}% | BE: {m_m2['be_rate']:5.2f}% | Net: {m_m2['net_r']:+6.1f} R | PF: {m_m2['pf']:4.2f} | Max DD: -{m_m2['max_dd']:4.1f} R\n")

if __name__ == "__main__":
    main()
