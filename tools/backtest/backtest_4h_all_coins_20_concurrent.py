#!/usr/bin/env python3
"""tools/backtest/backtest_4h_all_coins_20_concurrent.py

Tests the 4-Hour Timeframe across ALL 357 coins with:
- Max 20 concurrent positions (also compared with 5, 10, 20, Unlimited)
- Real-world CoinDCX taker fees (0.10% round-trip)
- Adverse market slippage (0.10% entry + 0.10% SL)
- Tested across volume spike thresholds (>=10x, >=20x, >=30x)
- 1.0x ATR Stop Loss with 1:2 and 1:3 RR targets
- Intrabar 1-minute execution simulation across 15.3M candles
"""
import sqlite3
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

def resample_bars(m1_rows, tf_min=240): # 240 min = 4H
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

fee_pct = 0.0010
slip_pct = 0.0010
tf_min = 240 # 4H

configs = [
    (">=10x Volume Spike", 10.0, 1.0, 2.0),
    (">=10x Volume Spike", 10.0, 1.0, 3.0),
    (">=20x Volume Spike", 20.0, 1.0, 2.0),
    (">=20x Volume Spike", 20.0, 1.0, 3.0),
    (">=30x Volume Spike", 30.0, 1.0, 2.0),
    (">=30x Volume Spike", 30.0, 1.0, 3.0),
]

concurrency_levels = [5, 10, 20, 999]

print("====================================================================================================")
print(f"📊 4-HOUR TIMEFRAME BACKTEST ACROSS ALL {len(symbols)} COINS WITH 20 CONCURRENT POSITIONS")
print("   Includes: 0.10% CoinDCX Taker Fees + 0.10% Slippage + Intrabar 1m Precision")
print("====================================================================================================\n")

for label, spk_mult, atr_mult, rr in configs:
    candidate_signals = []
    for sym, m1_rows in raw_m1.items():
        tf_bars = resample_bars(m1_rows, tf_min)
        n = len(tf_bars)
        if n < 45: continue
        highs = np.array([b['high'] for b in tf_bars])
        lows = np.array([b['low'] for b in tf_bars])
        closes = np.array([b['close'] for b in tf_bars])
        opens = np.array([b['open'] for b in tf_bars])
        vols = np.array([b['volume'] for b in tf_bars])
        times = [b['ts'] for b in tf_bars]
        
        tr = np.zeros(n)
        tr[0] = highs[0] - lows[0]
        for i in range(1, n):
            tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        atr14 = pd.Series(tr).rolling(14).mean().fillna(highs[0] - lows[0]).values
        
        for i in range(40, n - 1):
            if closes[i] < opens[i]: continue
            base_v = np.mean(vols[i - 40 : i])
            if base_v <= 0 or (vols[i] / base_v) < spk_mult: continue
            
            raw_entry = closes[i]
            real_entry_px = raw_entry * (1.0 - slip_pct)
            risk_dist = atr_mult * atr14[i]
            if risk_dist <= 0 or (risk_dist / real_entry_px) < 0.005: continue
            
            candidate_signals.append({
                "symbol": sym,
                "signal_t": times[i] + tf_min * 60 * 1000,
                "entry_px": real_entry_px,
                "sl_px": real_entry_px + risk_dist,
                "tp_px": real_entry_px - rr * risk_dist,
                "risk_dist": risk_dist,
                "m1_rows": m1_rows
            })
            
    candidate_signals.sort(key=lambda x: x['signal_t'])
    
    print(f"🔹 Config: 4H | {label} | {atr_mult:.1f}x ATR SL | 1:{int(rr)} RR | Raw Candidate Signals: {len(candidate_signals)}")
    print(f"{'Max Concur':<12} | {'Trades':<8} | {'Win Rate':<10} | {'PF':<6} | {'Net Return (R)':<16} | {'$100 ROI':<12} | {'Max DD %'}")
    print("-" * 80)
    
    for max_concurrent in concurrency_levels:
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
                    pnl = r_mult * (wallet_bal * 0.01) # 1% risk per trade
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
                
            m1_rows = sig['m1_rows']
            m1_times = [r[0] for r in m1_rows]
            m1_start = -1
            for idx in range(len(m1_times)):
                if m1_times[idx] >= sig_t:
                    m1_start = idx
                    break
            if m1_start == -1: continue
            
            exit_r = None
            exit_t = None
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
                
            if exit_r is not None:
                fee_r = (2.0 * fee_pct) / (sig['risk_dist'] / sig['entry_px'])
                net_r = exit_r - fee_r
                active_positions.append({
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
        pf = (gross_win / gross_loss) if gross_loss > 0 else 999.0
        dd_pct = (max_dd / peak_bal) * 100 if peak_bal > 0 else 0
        roi = ((wallet_bal - 100.0) / 100.0) * 100.0
        
        cap_str = "Unlimited" if max_concurrent >= 999 else str(max_concurrent)
        print(f"{cap_str:<12} | {n:<8} | {wr:>5.1f}%    | {pf:<6.2f} | {tot_r:>+10.2f} R   | {roi:>+9.2f}%  | {dd_pct:>5.1f}%")
    print()
