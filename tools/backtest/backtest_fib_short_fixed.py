#!/usr/bin/env python3
"""backtest_fib_short_fixed.py — Backtests the Mean Reversion Shorting strategy
using a hard Limit Entry at 23.6% retracement, SL at Peak High, and TP at 78.6% retracement.
Runs at 1m granularity for June 2026.
"""
import os, sys, sqlite3, time
import numpy as np

DB_JUNE = "/root/data/june_2026_1m.db"

if not os.path.exists(DB_JUNE):
    print(f"Error: June database not found at {DB_JUNE}")
    sys.exit(1)

t0 = time.time()
conn = sqlite3.connect(DB_JUNE)
cursor = conn.cursor()
symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]

raw_candles = {}
for sym in symbols:
    rows = cursor.execute("SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC", (sym,)).fetchall()
    if len(rows) < 300: continue
    raw_candles[sym] = rows

print(f"Loaded database in {time.time()-t0:.2f}s. Resampling to 4h bars...", flush=True)

# Strategy parameters
RISK_USD = 10.00
FEE_R = 0.05
vol_spikes = [15.0, 20.0, 25.0, 30.0, 40.0]

print("==================================================================================")
print("📊 BACKTEST: HARD LIMIT ENTRY AT 23.6% RETRACEMENT | TP 78.6% (JUNE 2026)")
print("==================================================================================")

for vol_threshold in vol_spikes:
    trades = []
    
    for sym, rows in raw_candles.items():
        # Resample 1m rows to 4h candles
        bars_4h = []
        cur_bar = None
        tf_ms = 4 * 60 * 60 * 1000
        for c in rows:
            ts, o, h, l, cl, v = c
            b_ts = (ts // tf_ms) * tf_ms
            if cur_bar is None or cur_bar['ts'] != b_ts:
                if cur_bar is not None:
                    bars_4h.append(cur_bar)
                cur_bar = {'ts': b_ts, 'open': o, 'high': h, 'low': l, 'close': cl, 'volume': v}
            else:
                if h > cur_bar['high']: cur_bar['high'] = h
                if l < cur_bar['low']: cur_bar['low'] = l
                cur_bar['close'] = cl
                cur_bar['volume'] += v
        if cur_bar is not None:
            bars_4h.append(cur_bar)
            
        if len(bars_4h) < 45: continue
        
        # Calculate volume SMA
        vols = [b['volume'] for b in bars_4h]
        for idx, b in enumerate(bars_4h):
            start_vol_i = max(0, idx - 40)
            vol_sma = np.mean(vols[start_vol_i:idx]) if idx > 0 else vols[0]
            b['vol_mult'] = (b['volume'] / vol_sma) if vol_sma > 0 else 0
            
        # Process trigger and trade simulation
        in_watchlist = False
        pump_start_px = 0.0
        pump_peak_px = 0.0
        pump_peak_idx = 0
        
        idx = 40
        while idx < len(bars_4h):
            b = bars_4h[idx]
            
            if not in_watchlist:
                if (b['close'] > b['open']) and b['vol_mult'] >= vol_threshold:
                    in_watchlist = True
                    pump_start_px = b['low']
                    search_end = min(len(bars_4h), idx + 48)
                    pump_peak_idx = idx
                    for search_i in range(idx, search_end):
                        if bars_4h[search_i]['high'] > bars_4h[pump_peak_idx]['high']:
                            pump_peak_idx = search_i
                    pump_peak_px = bars_4h[pump_peak_idx]['high']
                    idx = max(idx + 1, pump_peak_idx)
                    continue
            else:
                # Calculate hard levels:
                # 23.6% Retracement Entry, Peak SL, 78.6% Retracement TP
                entry_px = pump_peak_px - 0.236 * (pump_peak_px - pump_start_px)
                sl_px = pump_peak_px
                risk = sl_px - entry_px
                tp_px = pump_peak_px - 0.786 * (pump_peak_px - pump_start_px)
                
                if risk > 0 and tp_px < entry_px:
                    # Retrieve 1m candles following the peak to see if limit order is filled
                    peak_end_ts = int(bars_4h[pump_peak_idx]['ts']) + tf_ms
                    sub = [c for c in rows if peak_end_ts <= c[0] <= (peak_end_ts + 1296000000)] # 15 days
                    
                    if sub:
                        filled = False
                        trade_r = 0.0
                        
                        for m_row in sub:
                            m_ts, m_o, m_h, m_l, m_cl, m_v = m_row
                            if not filled:
                                if m_h >= entry_px:
                                    if m_h >= sl_px:
                                        # Triggered SL before fill is completed -> skip
                                        break
                                    filled = True
                            else:
                                if m_h >= sl_px:
                                    # Hit SL
                                    trade_r = -1.0
                                    break
                                elif m_l <= tp_px:
                                    # Hit TP
                                    trade_r = (entry_px - tp_px) / risk
                                    break
                                    
                        if filled and trade_r != 0.0:
                            net_r = trade_r - FEE_R
                            trades.append(net_r)
                            
                in_watchlist = False
                idx = idx + 10 # cool off
                continue
            idx += 1
            
    if trades:
        tot = len(trades)
        wins = [t for t in trades if t > 0]
        losses = [t for t in trades if t <= 0]
        win_rate = (len(wins) / tot) * 100
        g_prof = sum(wins)
        g_loss = abs(sum(losses))
        pf = g_prof / g_loss if g_loss > 0 else 99.9
        avg_r = np.mean(trades)
        net_inr = sum(trades) * RISK_USD * 88.5
        
        print(f"- Spike Mult: {int(vol_threshold):>2}x | Trades: {tot:<3} | Win Rate: {win_rate:.1f}% | Profit Factor: {pf:.2f} | Avg R: {avg_r:+.2f}R | Net Profit: ₹{net_inr:,.2f}")
    else:
        print(f"- Spike Mult: {int(vol_threshold)}x | No trades executed")

print("==================================================================================")
conn.close()
