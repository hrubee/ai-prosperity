#!/usr/bin/env python3
"""Fetch 4H candles for all CoinDCX futures symbols and simulate 4H signals that fired in the last 24 hours.

1. Downloads 4H OHLCV history from CoinDCX for all active USDT futures pairs.
2. Computes the 20/40-period volume baseline on 4H.
3. Tests 4H volume spike shorting for all trades triggered within the last 24 hours.
4. Uses real 1m resolution data from datasets/coindcx_last_24h.db for realistic fills and exits.
"""
import os
import sys
import time
import json
import sqlite3
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd

DB_4H_PATH = "datasets/coindcx_4h_history.db"
DB_1M_PATH = "datasets/coindcx_last_24h.db"

# 1. Fetch all active futures pairs
print("Fetching active USDT futures pairs from CoinDCX...")
try:
    res = requests.get("https://public.coindcx.com/exchange/v1/derivatives/futures/data/active_instruments", timeout=10)
    data = res.json()
    symbols = [item['pair'] for item in data if item.get('status') == 'active' and item.get('quote_currency') in ['USDT', 'INR']]
    if not symbols:
        symbols = [item['pair'] for item in data if item.get('status') == 'active']
except Exception as e:
    print(f"Error fetching symbols: {e}")
    # Fallback to symbols in 24h DB
    conn = sqlite3.connect(DB_1M_PATH)
    symbols = [r[0] for r in conn.cursor().execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]
    conn.close()

print(f"Total symbols to fetch 4H candles for: {len(symbols)}")

# 2. Setup SQLite DB for 4H
conn_4h = sqlite3.connect(DB_4H_PATH)
c_4h = conn_4h.cursor()
c_4h.execute("""
CREATE TABLE IF NOT EXISTS klines_4h (
    symbol TEXT,
    timestamp INTEGER,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    PRIMARY KEY (symbol, timestamp)
)
""")
conn_4h.commit()

def fetch_4h_for_pair(sym):
    url = f"https://public.coindcx.com/market_data/candles?pair={sym}&interval=4h&limit=200"
    try:
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            candles = r.json()
            if isinstance(candles, list) and len(candles) > 0:
                rows = []
                for c in candles:
                    # c format: {'open':..., 'high':..., 'low':..., 'close':..., 'volume':..., 'time':...}
                    t = c.get('time') or c.get('timestamp')
                    rows.append((
                        sym,
                        int(t),
                        float(c['open']),
                        float(c['high']),
                        float(c['low']),
                        float(c['close']),
                        float(c['volume'])
                    ))
                return sym, rows
    except Exception:
        pass
    return sym, []

all_4h_data = {}
with ThreadPoolExecutor(max_workers=30) as executor:
    futures = [executor.submit(fetch_4h_for_pair, sym) for sym in symbols]
    for fut in as_completed(futures):
        sym, rows = fut.result()
        if rows:
            all_4h_data[sym] = rows
            c_4h.executemany("INSERT OR REPLACE INTO klines_4h VALUES (?,?,?,?,?,?,?)", rows)

conn_4h.commit()
conn_4h.close()
print(f"Successfully saved 4H candles for {len(all_4h_data)} coins into {DB_4H_PATH}.")

# 3. Load 1m data for the last 24 hours to simulate exact intrabar exits
conn_1m = sqlite3.connect(DB_1M_PATH)
c_1m = conn_1m.cursor()
raw_1m = {}
for sym in symbols:
    rows = c_1m.execute(
        "SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC",
        (sym,)
    ).fetchall()
    if rows:
        raw_1m[sym] = rows
conn_1m.close()

# 4. Find the timestamp range of the last 24h
all_1m_timestamps = [r[0] for sym, rows in raw_1m.items() for r in rows[:1] + rows[-1:]]
min_24h_ts = min(all_1m_timestamps) if all_1m_timestamps else int(time.time() - 86400)*1000
max_24h_ts = max(all_1m_timestamps) if all_1m_timestamps else int(time.time())*1000

print(f"\nLast 24-Hour Evaluation Window: {time.strftime('%Y-%m-%d %H:%M', time.gmtime(min_24h_ts/1000))} to {time.strftime('%Y-%m-%d %H:%M', time.gmtime(max_24h_ts/1000))} UTC")

# 5. Run simulation for 4H volume spike short strategy
fee_pct = 0.0010
slip_pct = 0.0010
max_concurrent = 20

print("\n====================================================================================================")
print("📊 4-HOUR TIMEFRAME SIMULATION: ALL SIGNALS TRIGGERED IN THE LAST 24 HOURS")
print("   Includes: Multi-day 4H Volume Baseline + 0.10% CoinDCX Taker Fees + 0.10% Slippage + 1m Intrabar Exits")
print("====================================================================================================\n")

for spk_mult in [5.0, 10.0, 20.0, 30.0]:
    for rr in [2.0, 3.0]:
        candidate_signals = []
        for sym, candles in all_4h_data.items():
            # Sort ascending
            sorted_c = sorted(candles, key=lambda x: x[1])
            n = len(sorted_c)
            if n < 25: continue
            
            times = [c[1] for c in sorted_c]
            opens = np.array([c[2] for c in sorted_c])
            highs = np.array([c[3] for c in sorted_c])
            lows = np.array([c[4] for c in sorted_c])
            closes = np.array([c[5] for c in sorted_c])
            vols = np.array([c[6] for c in sorted_c])
            
            tr = np.zeros(n)
            tr[0] = highs[0] - lows[0]
            for i in range(1, n):
                tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            atr14 = pd.Series(tr).rolling(14).mean().fillna(highs[0] - lows[0]).values
            
            for i in range(20, n):
                # Candle close time
                candle_close_t = times[i] + 4 * 3600 * 1000
                
                # Check if signal fell within the last 24h dataset window
                if not (min_24h_ts <= candle_close_t <= max_24h_ts):
                    continue
                    
                if closes[i] < opens[i]:
                    continue # only bullish spikes
                    
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
                    "signal_t": candle_close_t,
                    "entry_px": real_entry_px,
                    "sl_px": real_entry_px + risk_dist,
                    "tp_px": real_entry_px - rr * risk_dist,
                    "risk_dist": risk_dist,
                    "spike_mult": mult
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
                # If 1m rows didn't cover forward in time, use current market mark
                exit_r = 0.0
                exit_t = sig_t
                
            if exit_r is not None:
                fee_r = (2.0 * fee_pct) / (sig['risk_dist'] / sig['entry_px'])
                net_r = exit_r - fee_r
                active_positions.append({
                    "symbol": sig['symbol'],
                    "net_r": net_r,
                    "is_win": net_r > 0,
                    "exit_t": exit_t
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
        
        print(f"4H Spike: >={int(spk_mult):<2}x | RR: 1:{int(rr)} | Signals in 24h: {len(candidate_signals):<2} | Taken: {n:<2} | Win: {wr:>5.1f}% | PF: {pf:<5.2f} | Net R: {tot_r:>+6.2f} R | 24h ROI: {roi:>+6.2f}% | Max DD: {dd_pct:>4.1f}%")
