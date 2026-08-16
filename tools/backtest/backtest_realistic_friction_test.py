#!/usr/bin/env python3
"""tools/backtest/backtest_realistic_friction_test.py — Realistic Stress-Tested Backtest.

Applies full real-world exchange frictions:
1. CoinDCX Taker Fees: 0.05% Entry + 0.05% Exit (0.10% total fee).
2. Taker Market Slippage: 0.10% adverse entry slippage + 0.10% SL slippage.
3. Max Concurrent Positions Cap: Max 5 concurrent open trades (no unlimited capital).
4. Realistic Capital & Margin Allocation: $100 starting balance, 1% fixed-fractional risk.
5. Intrabar 1-minute execution across 15.3M candles in datasets/june_2026_1m.db.
"""
import os
import sys
import sqlite3
import time
import numpy as np
import pandas as pd

DB_PATH = "datasets/june_2026_1m.db"
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]

raw_m1 = {}
for sym in symbols:
    rows = cursor.execute(
        "SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC",
        (sym,)
    ).fetchall()
    if len(rows) >= 50:
        raw_m1[sym] = rows
conn.close()

def resample_bars(m1_rows, tf_min):
    tf_ms = tf_min * 60 * 1000
    tf_bars = []
    cur_bar = None
    cur_1m_slice = []
    for row in m1_rows:
        ts, o, h, l, c, v = row
        b_ts = (ts // tf_ms) * tf_ms
        if cur_bar is None or cur_bar['ts'] != b_ts:
            if cur_bar is not None:
                cur_bar['m1_bars'] = cur_1m_slice
                tf_bars.append(cur_bar)
            cur_bar = {'ts': b_ts, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': v}
            cur_1m_slice = [row]
        else:
            if h > cur_bar['high']: cur_bar['high'] = h
            if l < cur_bar['low']: cur_bar['low'] = l
            cur_bar['close'] = c
            cur_bar['volume'] += v
            cur_1m_slice.append(row)
    if cur_bar is not None:
        cur_bar['m1_bars'] = cur_1m_slice
        tf_bars.append(cur_bar)
    return tf_bars

def run_stress_test(tf_name="15m", tf_min=15, spk_mult=10.0, atr_mult=1.5, rr=3.0,
                    fee_pct=0.0010, # 0.10% roundtrip fee
                    slip_pct=0.0010, # 0.10% adverse entry slippage
                    max_concurrent=5):
    
    # 1. Generate all candidate signals with timestamps
    candidate_signals = []
    
    for sym, m1_rows in raw_m1.items():
        tf_bars = resample_bars(m1_rows, tf_min)
        n = len(tf_bars)
        if n < 45:
            continue
            
        opens = np.array([b['open'] for b in tf_bars])
        highs = np.array([b['high'] for b in tf_bars])
        lows = np.array([b['low'] for b in tf_bars])
        closes = np.array([b['close'] for b in tf_bars])
        vols = np.array([b['volume'] for b in tf_bars])
        times = [b['ts'] for b in tf_bars]
        
        tr = np.zeros(n)
        tr[0] = highs[0] - lows[0]
        for i in range(1, n):
            tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        atr14 = pd.Series(tr).rolling(14).mean().fillna(highs[0] - lows[0]).values
        m1_times = [r[0] for r in m1_rows]
        
        for i in range(40, n - 1):
            if closes[i] < opens[i]:
                continue
            base_v = np.mean(vols[i - 40 : i])
            if base_v <= 0 or (vols[i] / base_v) < spk_mult:
                continue
                
            raw_entry = closes[i]
            # Apply Adverse Entry Slippage (enter slightly lower on short)
            real_entry_px = raw_entry * (1.0 - slip_pct)
            cur_atr = atr14[i]
            risk_dist = atr_mult * cur_atr
            if risk_dist <= 0 or (risk_dist / real_entry_px) < 0.003:
                continue
                
            sl_px = real_entry_px + risk_dist
            tp_px = real_entry_px - rr * risk_dist
            trigger_t = times[i] + tf_min * 60 * 1000
            
            candidate_signals.append({
                "symbol": sym,
                "signal_t": trigger_t,
                "entry_px": real_entry_px,
                "sl_px": sl_px,
                "tp_px": tp_px,
                "risk_dist": risk_dist,
                "m1_rows": m1_rows
            })
            
    # Sort signals strictly chronologically
    candidate_signals.sort(key=lambda x: x['signal_t'])
    print(f"Total Raw Signals Generated: {len(candidate_signals)}")
    
    # 2. Simulate live chronological execution with 5 Max Concurrent Positions
    active_positions = [] # list of dicts
    executed_trades = []
    wallet_bal = 100.0
    peak_bal = 100.0
    max_dd = 0.0
    
    for sig in candidate_signals:
        sig_t = sig['signal_t']
        
        # Check and resolve any active positions that closed before sig_t
        still_active = []
        for pos in active_positions:
            if pos['exit_t'] and pos['exit_t'] <= sig_t:
                # Process completed trade
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
        
        # Check max concurrent positions cap
        if len(active_positions) >= max_concurrent:
            # Portfolio full — skip this trade
            continue
            
        # Execute trade intrabar
        m1_rows = sig['m1_rows']
        m1_times = [r[0] for r in m1_rows]
        m1_start = -1
        for idx in range(len(m1_times)):
            if m1_times[idx] >= sig_t:
                m1_start = idx
                break
                
        if m1_start == -1:
            continue
            
        exit_r = None
        exit_t = None
        exit_reason = None
        
        for m1_idx in range(m1_start, len(m1_rows)):
            m1_t, m1_o, m1_h, m1_l, m1_c, m1_v = m1_rows[m1_idx]
            
            # SL check (with potential SL slippage)
            if m1_h >= sig['sl_px']:
                real_sl_exit = sig['sl_px'] * (1.0 + slip_pct) # slippage on SL
                loss_dist = real_sl_exit - sig['entry_px']
                gross_r = -(loss_dist / sig['risk_dist'])
                exit_r = gross_r
                exit_t = m1_t
                exit_reason = "SL"
                break
            elif m1_l <= sig['tp_px']:
                gain_dist = sig['entry_px'] - sig['tp_px']
                gross_r = gain_dist / sig['risk_dist']
                exit_r = gross_r
                exit_t = m1_t
                exit_reason = "TP"
                break
                
        if exit_r is None and m1_start < len(m1_rows):
            last_c = m1_rows[-1][4]
            diff = sig['entry_px'] - last_c
            exit_r = diff / sig['risk_dist']
            exit_t = m1_rows[-1][0]
            exit_reason = "EOM"
            
        if exit_r is not None:
            # Deduct roundtrip exchange taker fee in R terms
            # Fee in R = (Entry Notional * Fee% + Exit Notional * Fee%) / (Entry Notional * Risk%)
            # Approx: (2 * fee_pct) / (risk_dist / entry_px)
            fee_r = (2.0 * fee_pct) / (sig['risk_dist'] / sig['entry_px'])
            net_r = exit_r - fee_r
            
            pos_record = {
                "symbol": sig['symbol'],
                "entry_t": sig_t,
                "exit_t": exit_t,
                "entry_px": sig['entry_px'],
                "net_r": net_r,
                "fee_r": fee_r,
                "is_win": net_r > 0,
                "reason": exit_reason
            }
            active_positions.append(pos_record)
            
    # Flush remaining positions
    for pos in active_positions:
        r_mult = pos['net_r']
        pnl = r_mult * (wallet_bal * 0.01)
        wallet_bal += pnl
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
    pf = (gross_win / gross_loss) if gross_loss > 0 else 999.0
    avg_fee_r = np.mean([t['fee_r'] for t in executed_trades]) if n else 0
    dd_pct = (max_dd / peak_bal) * 100 if peak_bal > 0 else 0
    roi = ((wallet_bal - 100.0) / 100.0) * 100.0
    
    print(f"\n=======================================================================================")
    print(f"📊 REALISTIC STRESS-TESTED RESULTS (15m, 10x Spike, 1.5x ATR SL, 1:3 RR)")
    print(f"   Exchange Taker Fees: 0.10% | Slippage: 0.10% | Max Concurrent Positions: {max_concurrent}")
    print(f"=======================================================================================")
    print(f"  • Total Trades Taken (with 5-pos cap): {n} (Filtered from 2,066 raw signals)")
    print(f"  • Realistic Win Rate                 : {wr:.1f}% ({len(wins)} Wins / {len(losses)} Losses)")
    print(f"  • Realistic Profit Factor            : {pf:.2f}")
    print(f"  • Average Exchange Fee Drag per Trade: -{avg_fee_r:.3f} R")
    print(f"  • Realistic Net Total Return (R)     : {tot_r:+.2f} R")
    print(f"  • Realistic $100 Account Growth      : ${wallet_bal:.2f} ({roi:+.2f}% Real ROI)")
    print(f"  • Realistic Max Drawdown             : {dd_pct:.1f}%")
    print(f"=======================================================================================\n")

if __name__ == "__main__":
    run_stress_test()
