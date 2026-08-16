#!/usr/bin/env python3
"""backtest_short_fixed_rr.py — Realistic backtest of the Shorting strategy
using a fixed 1:4 Risk-to-Reward ratio (TP = Entry - 4.0 * Risk).
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
EMA_PERIOD = 9
RISK_USD = 10.00
FEE_R = 0.05
vol_spikes = [15.0, 20.0, 25.0, 30.0, 40.0]

print("==================================================================================")
print("📊 REALISTIC BACKTEST: NO LOOKAHEAD BIAS | FIXED 1:4 RR TARGET (JUNE 2026)")
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
        
        closes = [b['close'] for b in bars_4h]
        k = 2.0 / (EMA_PERIOD + 1)
        ema = []
        for idx, cl in enumerate(closes):
            if idx == 0:
                ema.append(cl)
            else:
                ema.append(cl * k + ema[-1] * (1.0 - k))
                
        vols = [b['volume'] for b in bars_4h]
        for idx, b in enumerate(bars_4h):
            b['ema_9'] = ema[idx]
            b['is_red'] = b['close'] < b['open']
            start_vol_i = max(0, idx - 40)
            vol_sma = np.mean(vols[start_vol_i:idx]) if idx > 0 else vols[0]
            b['vol_mult'] = (b['volume'] / vol_sma) if vol_sma > 0 else 0
            
        in_watchlist = False
        pump_start_px = 0.0
        pump_peak_px = 0.0
        
        idx = 40
        while idx < len(bars_4h):
            b = bars_4h[idx]
            
            if not in_watchlist:
                if (b['close'] > b['open']) and b['vol_mult'] >= vol_threshold:
                    in_watchlist = True
                    pump_start_px = b['low']
                    pump_peak_px = b['high']
                    idx += 1
                    continue
            else:
                if b['high'] > pump_peak_px:
                    pump_peak_px = b['high']
                    
                # Confirmation: first red close below 9 EMA
                if b['is_red'] and b['close'] < b['ema_9']:
                    entry_px = b['close']
                    sl_px = pump_peak_px
                    risk = sl_px - entry_px
                    
                    # FIXED 1:4 RR Target
                    tp_px = entry_px - 4.0 * risk
                    
                    if risk > 0 and tp_px < entry_px:
                        trigger_end_ts = int(b['ts']) + tf_ms
                        sub = [c for c in rows if trigger_end_ts <= c[0] <= (trigger_end_ts + 1296000000)] # 15 days
                        
                        if sub:
                            trade_r = 0.0
                            for m_row in sub:
                                m_ts, m_o, m_h, m_l, m_cl, m_v = m_row
                                if m_h >= sl_px:
                                    trade_r = -1.0
                                    break
                                elif m_l <= tp_px:
                                    trade_r = 4.0
                                    break
                                    
                            if trade_r != 0.0:
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
