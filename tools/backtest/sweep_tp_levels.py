#!/usr/bin/env python3
"""sweep_tp_levels.py — Parameter sweep of Take Profit levels (50% to 100% retracement)
for the Mean Reversion Shorting strategy on the June 2026 dataset at 1m granularity.
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

# Strategy parameters
PUMP_WINDOW = 24
PUMP_THRESHOLD = 0.40
EMA_PERIOD = 9
RISK_USD = 10.00
FEE_R = 0.05

tp_levels = [0.50, 0.60, 0.70, 0.80, 0.90, 1.00]

print("==================================================================================")
print("📊 TAKE PROFIT LEVEL PARAMETER SWEEP RESULTS (JUNE 2026)")
print("==================================================================================")

for tp_mult in tp_levels:
    trades = []
    
    for sym, rows in raw_candles.items():
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
                
        for idx, b in enumerate(bars_4h):
            b['ema_9'] = ema[idx]
            b['is_red'] = b['close'] < b['open']
            start_idx = max(0, idx - PUMP_WINDOW)
            prev_b = bars_4h[start_idx]
            b['pump_pct'] = (b['close'] - prev_b['open']) / prev_b['open'] if prev_b['open'] > 0 else 0
            
        in_watchlist = False
        pump_start_px = 0.0
        pump_peak_px = 0.0
        pump_peak_idx = 0
        
        idx = PUMP_WINDOW
        while idx < len(bars_4h):
            b = bars_4h[idx]
            
            if not in_watchlist:
                if b['pump_pct'] >= PUMP_THRESHOLD:
                    in_watchlist = True
                    pump_start_px = bars_4h[idx - PUMP_WINDOW]['open']
                    search_end = min(len(bars_4h), idx + 36)
                    pump_peak_idx = idx
                    for search_i in range(idx, search_end):
                        if bars_4h[search_i]['high'] > bars_4h[pump_peak_idx]['high']:
                            pump_peak_idx = search_i
                    pump_peak_px = bars_4h[pump_peak_idx]['high']
                    idx = max(idx + 1, pump_peak_idx)
                    continue
            else:
                if b['is_red'] and b['close'] < b['ema_9']:
                    entry_px = b['close']
                    sl_px = pump_peak_px * 1.02
                    risk = sl_px - entry_px
                    
                    # Take Profit at different retracement levels
                    tp_px = pump_peak_px - tp_mult * (pump_peak_px - pump_start_px)
                    
                    if risk > 0 and tp_px < entry_px:
                        trigger_end_ts = int(b['ts']) + tf_ms
                        sub = [c for c in rows if trigger_end_ts <= c[0] <= (trigger_end_ts + 1296000000)]
                        
                        if sub:
                            trade_r = 0.0
                            for m_row in sub:
                                m_ts, m_o, m_h, m_l, m_cl, m_v = m_row
                                if m_h >= sl_px:
                                    trade_r = -1.0
                                    break
                                elif m_l <= tp_px:
                                    trade_r = (entry_px - tp_px) / risk
                                    break
                                    
                            if trade_r != 0.0:
                                net_r = trade_r - FEE_R
                                trades.append(net_r)
                                
                    in_watchlist = False
                    idx = idx + 10
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
        
        print(f"- TP Retrace: {int(tp_mult*100):>3}% | Trades: {tot:<3} | Win Rate: {win_rate:.1f}% | Profit Factor: {pf:.2f} | Avg R: {avg_r:+.2f}R | Net Profit: ₹{net_inr:,.2f}")
    else:
        print(f"- TP Retrace: {int(tp_mult*100)}% | No trades executed")

print("==================================================================================")
conn.close()
