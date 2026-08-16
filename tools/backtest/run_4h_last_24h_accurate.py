#!/usr/bin/env python3
"""Accurate 4H simulation for the last 24-48 hours.

Fetches 200 4H candles (~33 days) for all active pairs to have exact volume baselines and ATRs.
Simulates all 4H spike shorts that closed within the last 24 hours.
"""
import sqlite3
import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd

DB_1M_PATH = "datasets/coindcx_last_24h.db"
conn_1m = sqlite3.connect(DB_1M_PATH)
symbols = [r[0] for r in conn_1m.cursor().execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]

raw_1m = {}
for sym in symbols:
    rows = conn_1m.cursor().execute(
        "SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC",
        (sym,)
    ).fetchall()
    if rows:
        raw_1m[sym] = rows
conn_1m.close()

print(f"Loaded {len(symbols)} coins from local 24h database.")

def fetch_4h_candles(sym):
    # Binance futures endpoint
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}USDT&interval=4h&limit=200"
    try:
        r = requests.get(url, timeout=6)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                bars = []
                for c in data:
                    bars.append({
                        'ts': int(c[0]),
                        'open': float(c[1]),
                        'high': float(c[2]),
                        'low': float(c[3]),
                        'close': float(c[4]),
                        'volume': float(c[5])
                    })
                bars.sort(key=lambda x: x['ts'])
                return sym, bars
    except Exception:
        pass
    return sym, []

all_4h_candles = {}
print("Downloading 4H candle history for all coins...")
with ThreadPoolExecutor(max_workers=35) as ex:
    futs = [ex.submit(fetch_4h_candles, sym) for sym in symbols]
    for f in as_completed(futs):
        sym, bars = f.result()
        if bars and len(bars) >= 25:
            all_4h_candles[sym] = bars

print(f"Successfully fetched 4H histories for {len(all_4h_candles)} coins.")

# Find time window for the last 24h
now_ms = int(time.time() * 1000)
last_24h_start_ms = now_ms - (24 * 3600 * 1000)

fee_pct = 0.0010
slip_pct = 0.0010
max_concurrent = 20

print("\n" + "="*100)
print(f"📊 4-HOUR TIMEFRAME SIMULATION: TRADES TRIGGERED IN THE LAST 24 HOURS ACROSS ALL COINS")
print("   Includes: 20-Period 4H Volume Baseline + 0.10% CoinDCX Taker Fees + 0.10% Slippage + 1m Intrabar Exits")
print("="*100 + "\n")

for spk_mult in [3.0, 5.0, 10.0, 20.0, 30.0]:
    for rr in [2.0, 3.0]:
        candidate_signals = []
        for sym, bars in all_4h_candles.items():
            n = len(bars)
            if n < 25: continue
            
            times = [b['ts'] for b in bars]
            opens = np.array([b['open'] for b in bars])
            highs = np.array([b['high'] for b in bars])
            lows = np.array([b['low'] for b in bars])
            closes = np.array([b['close'] for b in bars])
            vols = np.array([b['volume'] for b in bars])
            
            tr = np.zeros(n)
            tr[0] = highs[0] - lows[0]
            for i in range(1, n):
                tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            atr14 = pd.Series(tr).rolling(14).mean().fillna(highs[0] - lows[0]).values
            
            for i in range(20, n):
                # 4H bar close time is open time + 4 hours
                bar_close_ms = times[i] + (4 * 3600 * 1000)
                
                # Check if bar closed in the last 24 hours
                if bar_close_ms < last_24h_start_ms or bar_close_ms > now_ms:
                    continue
                    
                if closes[i] < opens[i]:
                    continue # Only short green spike closes
                    
                base_v = np.mean(vols[max(0, i-20) : i])
                if base_v <= 0: continue
                mult = vols[i] / base_v
                if mult < spk_mult: continue
                
                raw_entry = closes[i]
                real_entry_px = raw_entry * (1.0 - slip_pct)
                risk_dist = 1.0 * atr14[i]
                if risk_dist <= 0 or (risk_dist / real_entry_px) < 0.005: continue
                
                candidate_signals.append({
                    "symbol": sym,
                    "signal_t": bar_close_ms,
                    "entry_px": real_entry_px,
                    "sl_px": real_entry_px + risk_dist,
                    "tp_px": real_entry_px - rr * risk_dist,
                    "risk_dist": risk_dist,
                    "mult": mult,
                    "close_dt": time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(bar_close_ms / 1000))
                })
                
        candidate_signals.sort(key=lambda x: x['signal_t'])
        
        active_positions = []
        executed_trades = []
        wallet_bal = 100.0
        peak_bal = 100.0
        max_dd = 0.0
        
        for sig in candidate_signals:
            sig_t = sig['signal_t']
            still_active = []
            for pos in active_positions:
                if pos['exit_t'] and pos['exit_t'] <= sig_t:
                    r_mult = pos['net_r']
                    pnl = r_mult * (wallet_bal * 0.01)
                    wallet_bal += pnl
                    if wallet_bal > peak_bal: peak_bal = wallet_bal
                    dd = peak_bal - wallet_bal
                    if dd > max_dd: max_dd = dd
                    executed_trades.append(pos)
                else:
                    still_active.append(pos)
            active_positions = still_active
            
            if len(active_positions) >= max_concurrent:
                continue
                
            m1_rows = raw_1m.get(sig['symbol'], [])
            m1_times = [r[0] for r in m1_rows]
            m1_start = -1
            for idx in range(len(m1_times)):
                if m1_times[idx] >= sig_t:
                    m1_start = idx
                    break
                    
            exit_r = None
            exit_t = None
            if m1_start != -1:
                for m1_idx in range(m1_start, len(m1_rows)):
                    m1_t, m1_o, m1_h, m1_l, m1_c, m1_v = m1_rows[m1_idx]
                    if m1_h >= sig['sl_px']:
                        real_sl_exit = sig['sl_px'] * (1.0 + slip_pct)
                        exit_r = -((real_sl_exit - sig['entry_px']) / sig['risk_dist'])
                        exit_t = m1_t
                        break
                    elif m1_l <= sig['tp_px']:
                        exit_r = (sig['entry_px'] - sig['tp_px']) / sig['risk_dist']
                        exit_t = m1_t
                        break
                if exit_r is None and m1_start < len(m1_rows):
                    exit_r = (sig['entry_px'] - m1_rows[-1][4]) / sig['risk_dist']
                    exit_t = m1_rows[-1][0]
            else:
                last_bars = all_4h_candles.get(sig['symbol'], [])
                if last_bars:
                    cur_px = last_bars[-1]['close']
                    exit_r = (sig['entry_px'] - cur_px) / sig['risk_dist']
                    exit_t = now_ms
                    
            if exit_r is not None:
                fee_r = (2.0 * fee_pct) / (sig['risk_dist'] / sig['entry_px'])
                net_r = exit_r - fee_r
                active_positions.append({
                    "symbol": sig['symbol'],
                    "net_r": net_r,
                    "is_win": net_r > 0,
                    "exit_t": exit_t,
                    "mult": sig['mult'],
                    "close_dt": sig['close_dt']
                })
                
        for pos in active_positions:
            r_mult = pos['net_r']
            wallet_bal += r_mult * (wallet_bal * 0.01)
            if wallet_bal > peak_bal: peak_bal = wallet_bal
            dd = peak_bal - wallet_bal
            if dd > max_dd: max_dd = dd
            executed_trades.append(pos)
            
        n = len(executed_trades)
        wins = [t for t in executed_trades if t['is_win']]
        losses = [t for t in executed_trades if not t['is_win']]
        wr = len(wins) / n * 100.0 if n else 0
        tot_r = sum(t['net_r'] for t in executed_trades)
        gross_win = sum(t['net_r'] for t in wins)
        gross_loss = abs(sum(t['net_r'] for t in losses))
        pf = (gross_win / gross_loss) if gross_loss > 0 else (1.0 if n==0 else 999.0)
        dd_pct = (max_dd / peak_bal) * 100 if peak_bal > 0 else 0
        roi = ((wallet_bal - 100.0) / 100.0) * 100.0
        
        print(f"4H Spike: >={int(spk_mult):<2}x | RR: 1:{int(rr)} | 24h Signals: {len(candidate_signals):<2} | Taken: {n:<2} | Win: {wr:>5.1f}% | PF: {pf:<5.2f} | Net R: {tot_r:>+6.2f} R | 24h ROI: {roi:>+6.2f}% | Max DD: {dd_pct:>4.1f}%")
        if spk_mult >= 5.0 and rr == 2.0 and candidate_signals:
            for t in executed_trades:
                print(f"    -> Trade: {t['symbol']:<15} | Spike: {t['mult']:.1f}x | Close: {t['close_dt']} | Result: {t['net_r']:+.2f} R")
