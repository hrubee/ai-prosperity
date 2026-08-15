#!/usr/bin/env python3
"""backtest_champion_exact.py — Dedicated Standalone Backtest for #1 Champion Strategy Configuration.
Config: Entry Fib = 0.600 | SL Fib = 0.700 | Vol Spike = 10.0x | Act = 1.0R | Trail = 1.0R | Uncapped TP (10.0R).
Database: /root/data/june_2026_1m.db (15,313,290 1m candles, 357 symbols).
"""
import os, sys, sqlite3, time
import pandas as pd
import numpy as np

DB_PATH = "/root/data/june_2026_1m.db"

print("===================================================================", flush=True)
print("🚀 STANDALONE BACKTEST: #1 CHAMPION STRATEGY CONFIGURATION", flush=True)
print("   Config: Entry Fib=0.600 | SL Fib=0.700 | Vol=10.0x | Act=1.0R | Trail=1.0R", flush=True)
print(f"   Database: {DB_PATH}", flush=True)
print("===================================================================", flush=True)

if not os.path.exists(DB_PATH):
    print(f"Error: Database not found at {DB_PATH}")
    sys.exit(1)

t0 = time.time()
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]
print(f"Found {len(symbols)} total symbols in June continuous database.", flush=True)

ENTRY_FIB = 0.600
SL_FIB = 0.700
VOL_MULT = 10.0
ACT_R = 1.0
TRAIL_R = 1.0
TP_RR = 10.0
RISK_USD = 10.00  # $10 Risk per trade (~1% of $1,000 account)

sym_data = {}
for i, sym in enumerate(symbols):
    rows = cursor.execute("SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC", (sym,)).fetchall()
    if len(rows) < 300: continue
    
    bars_15m = []
    cur_bar = None
    for c in rows:
        ts, o, h, l, cl, v = c
        b_ts = (ts // 900000) * 900000
        if cur_bar is None or cur_bar['ts'] != b_ts:
            if cur_bar is not None: bars_15m.append(cur_bar)
            cur_bar = {'ts': b_ts, 'open': o, 'high': h, 'low': l, 'close': cl, 'volume': v}
        else:
            if h > cur_bar['high']: cur_bar['high'] = h
            if l < cur_bar['low']: cur_bar['low'] = l
            cur_bar['close'] = cl
            cur_bar['volume'] += v
    if cur_bar is not None: bars_15m.append(cur_bar)
    if len(bars_15m) < 40: continue
    
    vols = [b['volume'] for b in bars_15m]
    for idx, b in enumerate(bars_15m):
        start_i = max(0, idx - 39)
        w = vols[start_i:idx+1]
        b['vol_ma'] = sum(w) / len(w)
        b['is_green'] = b['close'] >= b['open']
        b['vol_mult'] = (b['volume'] / b['vol_ma']) if b['vol_ma'] > 0 else 0
        
    sym_data[sym] = (rows, bars_15m)

print(f"Pre-processed {len(sym_data)} symbols in {time.time()-t0:.2f}s! Running Standalone Granular Backtest...", flush=True)

executed_trades = []

for sym, (candles_1m, bars_15m) in sym_data.items():
    spikes = [b for b in bars_15m if b['is_green'] and b['vol_mult'] >= VOL_MULT]
    if not spikes: continue
    
    for s in spikes:
        high_px, low_px = s['high'], s['low']
        rng = high_px - low_px
        if rng <= 0: continue
        
        entry_px = high_px - (ENTRY_FIB * rng)
        sl_px = high_px - (SL_FIB * rng)
        risk = entry_px - sl_px
        if risk <= 0: continue
        tp_px = entry_px + (TP_RR * risk)
        
        spike_end_ts = int(s['ts']) + 900000
        sub = [c for c in candles_1m if spike_end_ts <= c[0] <= (spike_end_ts + 43200000)]
        if not sub: continue
        
        filled = False
        trade_r = 0.0
        peak_px = entry_px
        cur_sl = sl_px
        fill_ts = 0
        exit_ts = 0
        
        for m_row in sub:
            m_ts, m_o, m_h, m_l, m_cl, m_v = m_row
            if not filled:
                if m_l <= entry_px:
                    if m_l <= sl_px: break  # Invalidated before fill
                    filled = True
                    fill_ts = m_ts
                    peak_px = entry_px
                    cur_sl = sl_px
            else:
                if m_h > peak_px:
                    peak_px = m_h
                    peak_r = (peak_px - entry_px) / risk
                    if peak_r >= ACT_R:
                        cur_sl = max(cur_sl, peak_px - (TRAIL_R * risk))
                        
                if m_l <= cur_sl:
                    trade_r = (cur_sl - entry_px) / risk
                    exit_ts = m_ts
                    break
                elif m_h >= tp_px:
                    trade_r = TP_RR
                    exit_ts = m_ts
                    break
                    
        if filled and trade_r != 0.0:
            fee_r = 0.05
            net_r = trade_r - fee_r
            pnl_usd = net_r * RISK_USD
            executed_trades.append({
                "fill_ts": fill_ts,
                "exit_ts": exit_ts,
                "sym": sym,
                "entry_px": entry_px,
                "sl_px": sl_px,
                "exit_px": cur_sl,
                "peak_px": peak_px,
                "r_mult": net_r,
                "pnl_usd": pnl_usd,
                "pnl_inr": pnl_usd * 88.5
            })

# Sort executed trades by fill timestamp
executed_trades.sort(key=lambda x: x["fill_ts"])

tot_trades = len(executed_trades)
wins = [t for t in executed_trades if t["r_mult"] > 0]
losses = [t for t in executed_trades if t["r_mult"] <= 0]
win_rate = (len(wins) / tot_trades * 100) if tot_trades > 0 else 0

win_usd_sum = sum(t["pnl_usd"] for t in wins)
loss_usd_sum = abs(sum(t["pnl_usd"] for t in losses))
pf = (win_usd_sum / loss_usd_sum) if loss_usd_sum > 0 else 99.9

tot_pnl_usd = sum(t["pnl_usd"] for t in executed_trades)
tot_pnl_inr = tot_pnl_usd * 88.5
avg_r = sum(t["r_mult"] for t in executed_trades) / tot_trades if tot_trades > 0 else 0

# Calculate equity curve & max drawdown
start_capital = 1000.0  # $1,000 starting account
equity = [start_capital]
for t in executed_trades:
    equity.append(equity[-1] + t["pnl_usd"])

cum = np.array(equity)
peak = np.maximum.accumulate(cum)
dd = peak - cum
max_dd_usd = float(np.max(dd))
max_dd_pct = (max_dd_usd / start_capital) * 100

print("\n==================================================================", flush=True)
print("📊 DEDICATED BACKTEST RESULTS FOR #1 CHAMPION STRATEGY CONFIGURATION", flush=True)
print("==================================================================", flush=True)
print(f"  - Strategy Configuration: Entry=0.600 Fib | SL=0.700 Fib | Vol=10.0x | Act=1.0R | Trail=1.0R")
print(f"  - Total Executed Trades: {tot_trades}")
print(f"  - Winning Trades: {len(wins)} 🟢 | Losing Trades: {len(losses)} 🔴")
print(f"  - Win Rate: {win_rate:.1f}%")
print(f"  - Profit Factor (PF): {pf:.2f}")
print(f"  - Average Trade R-Multiple: {avg_r:+.2f}R")
print(f"  - Gross Profit: +${win_usd_sum:.2f} USDT")
print(f"  - Gross Loss: -${loss_usd_sum:.2f} USDT")
print(f"  - Maximum Drawdown: -${max_dd_usd:.2f} USDT (-{max_dd_pct:.1f}%)")
print(f"  - Starting Capital: ${start_capital:.2f} USDT (₹{start_capital*88.5:,.2f} INR)")
print(f"  - Final Capital: ${equity[-1]:.2f} USDT (₹{equity[-1]*88.5:,.2f} INR)")
print(f"  - NET REALIZED PNL (USDT): ${tot_pnl_usd:+.2f} USDT")
print(f"  - NET REALIZED PNL (INR @ 88.5): ₹{tot_pnl_inr:+,.2f} INR 🚀")
print("==================================================================", flush=True)
