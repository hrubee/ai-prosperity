#!/usr/bin/env python3
"""backtest_fibvol_dynamic_1m.py — Realistic minute-granularity backtest of the
dynamic FIBVOL Long strategy (with green candle chasing, red candle watch cancellation,
and safety guards) for June 2026.
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

print(f"Loaded database in {time.time()-t0:.2f}s. Running simulation...", flush=True)

# Strategy parameters
SPIKE_VOL_MULT = 10.0
ENTRY_FIB = 0.600
SL_FIB = 0.700
RR_RATIO = 5.0
FEE_R = 0.05
RISK_USD = 10.00

print("==================================================================================")
print("📊 FIBVOL DYNAMIC 1M GRANULARITY BACKTEST (JUNE 2026)")
print("==================================================================================")

trades = []

for sym, rows in raw_candles.items():
    # Resample to 15m bars to detect signals
    tf_ms = 15 * 60 * 1000
    bars_15m = []
    cur_bar = None
    for c in rows:
        ts, o, h, l, cl, v = c
        b_ts = (ts // tf_ms) * tf_ms
        if cur_bar is None or cur_bar['ts'] != b_ts:
            if cur_bar is not None:
                bars_15m.append(cur_bar)
            cur_bar = {'ts': b_ts, 'open': o, 'high': h, 'low': l, 'close': cl, 'volume': v}
        else:
            if h > cur_bar['high']: cur_bar['high'] = h
            if l < cur_bar['low']: cur_bar['low'] = l
            cur_bar['close'] = cl
            cur_bar['volume'] += v
    if cur_bar is not None:
        bars_15m.append(cur_bar)
        
    if len(bars_15m) < 45: continue
    
    # Calculate volume MA (excluding current spike candle)
    vols = [b['volume'] for b in bars_15m]
    for idx, b in enumerate(bars_15m):
        start_i = max(0, idx - 40)
        w = vols[start_i:idx]
        b['vol_ma'] = np.mean(w) if len(w) > 0 else vols[0]
        b['vol_mult'] = (b['volume'] / b['vol_ma']) if b['vol_ma'] > 0 else 0
        b['is_green'] = b['close'] >= b['open']
        
    # Map 15m bars by their end timestamp (open time + 15m) for quick lookup
    bars_map = {b['ts'] + tf_ms: b for b in bars_15m}
    
    # Simulation state
    watching = None
    position = None
    last_spike_t = 0
    
    # Simulate minute by minute
    for c in rows:
        ts, o, h, l, cl, v = c
        
        # 1. Manage Active Position
        if position:
            # Check Stop Loss
            if l <= position['sl_px']:
                trades.append(-1.0 - FEE_R)
                position = None
                watching = None
                continue
            # Check Take Profit
            elif h >= position['tp_px']:
                trades.append(RR_RATIO - FEE_R)
                position = None
                watching = None
                continue
                
        # 2. Check Watchlist Entry Fill
        elif watching:
            if l <= watching['entry_px']:
                if l <= watching['sl_px'] * 1.002: # Slippage Guard / Invalidated
                    watching = None
                else:
                    risk = watching['entry_px'] - watching['sl_px']
                    position = {
                        'entry_px': watching['entry_px'],
                        'sl_px': watching['sl_px'],
                        'tp_px': watching['entry_px'] + RR_RATIO * risk
                    }
                    watching = None
                    continue
                    
        # 3. Check 15m Bar Close Transitions
        if ts in bars_map:
            closed_bar = bars_map[ts]
            cur_t = closed_bar['ts']
            cur_h = closed_bar['high']
            cur_l = closed_bar['low']
            cur_o = closed_bar['open']
            cur_c = closed_bar['close']
            vol_mult = closed_bar['vol_mult']
            is_green = closed_bar['is_green']
            
            if position:
                # We are already in a position, ignore signal updates
                pass
            elif watching:
                # Dynamic update logic on closed 15m bar
                if is_green:
                    rng = cur_h - cur_l
                    if rng > 0:
                        entry_px = cur_h - (ENTRY_FIB * rng)
                        sl_px = cur_h - (SL_FIB * rng)
                        
                        # Risk Distance check
                        min_risk = entry_px * 0.001
                        if (entry_px - sl_px) >= min_risk:
                            watching['entry_px'] = entry_px
                            watching['sl_px'] = sl_px
                            watching['tp_px'] = entry_px + RR_RATIO * (entry_px - sl_px)
                else:
                    # Red close cancels watch
                    watching = None
            else:
                # Check for new spike
                if is_green and vol_mult >= SPIKE_VOL_MULT:
                    if cur_t > last_spike_t:
                        rng = cur_h - cur_l
                        if rng > 0:
                            entry_px = cur_h - (ENTRY_FIB * rng)
                            sl_px = cur_h - (SL_FIB * rng)
                            
                            min_risk = entry_px * 0.001
                            if (entry_px - sl_px) >= min_risk:
                                watching = {
                                    'entry_px': entry_px,
                                    'sl_px': sl_px,
                                    'ts': cur_t
                                }
                                last_spike_t = cur_t

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
    
    print(f"📊 DYNAMIC FIBVOL BACKTEST SUMMARY:")
    print(f"- Total Trades: {tot}")
    print(f"- Win Rate: {win_rate:.1f}%")
    print(f"- Profit Factor: {pf:.2f}")
    print(f"- Average Return: {avg_r:+.3f}R")
    print(f"- Net Profit (1% risk/trade): ₹{net_inr:,.2f}")
else:
    print("No trades executed during the backtest period.")

print("==================================================================================")
conn.close()
