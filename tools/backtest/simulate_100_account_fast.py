#!/usr/bin/env python3
"""simulate_100_account_fast.py — High-speed chronological $100 account simulation across June 2026.
Simulates:
- Starting Account: $100.00
- Fixed Fractional Compounding (1.0%, 2.0%, 3.0% risk per trade)
- Realistic Max Concurrent Positions limit: 3 concurrent trades max
- Exact 0.05% taker exchange fee & slippage per trade
- Trailing SL execution
"""
import os
import sys
import time
import sqlite3
import numpy as np

DB_PATH = "datasets/june_2026_1m.db"
if not os.path.exists(DB_PATH):
    print(f"Error: Database not found at {DB_PATH}")
    sys.exit(1)

print(f"Loading 1-minute dataset from {DB_PATH}...", flush=True)
t0 = time.time()
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]
raw_candles = {}
for sym in symbols:
    rows = cursor.execute(
        "SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC",
        (sym,)
    ).fetchall()
    if len(rows) >= 500:
        raw_candles[sym] = rows

print(f"Loaded {len(raw_candles)} active symbols ({sum(len(r) for r in raw_candles.values()):,} 1m bars) in {time.time()-t0:.2f}s.\n", flush=True)

def extract_all_trade_candidates(raw_data, require_2_green=True, spike_mult=10.0, entry_fib=0.60, sl_fib=0.70, rr_ratio=5.0):
    tf_ms = 15 * 60 * 1000
    fee_r = 0.05
    all_trades = []
    
    for sym, rows in raw_data.items():
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
            
        if len(bars_15m) < 45:
            continue
            
        vols = [b['volume'] for b in bars_15m]
        for idx, b in enumerate(bars_15m):
            start_i = max(0, idx - 40)
            w = vols[start_i:idx]
            b['vol_ma'] = np.mean(w) if len(w) > 0 else (vols[0] if vols else 1.0)
            b['vol_mult'] = (b['volume'] / b['vol_ma']) if b['vol_ma'] > 0 else 0
            b['is_green'] = b['close'] >= b['open']
            
        bars_map = {b['ts'] + tf_ms: (idx, b) for idx, b in enumerate(bars_15m)}
        
        watching = None
        position = None
        last_spike_t = 0
        
        for c in rows:
            ts, o, h, l, cl, v = c
            
            # Check active position
            if position:
                entry_px = position['entry_px']
                sl_px = position['sl_px']
                initial_sl_px = position['initial_sl_px']
                tp_px = position['tp_px']
                risk_unit = entry_px - initial_sl_px
                
                # Trailing SL update
                if h > position['peak_px']:
                    position['peak_px'] = h
                    if risk_unit > 0:
                        peak_r = (h - entry_px) / risk_unit
                        if peak_r >= 2.0:
                            desired_sl = h - (2.0 * risk_unit)
                            if desired_sl > position['sl_px']:
                                position['sl_px'] = desired_sl
                                sl_px = desired_sl
                                
                # Check SL
                if l <= sl_px:
                    r_pnl = (sl_px - entry_px) / risk_unit if risk_unit > 0 else -1.0
                    all_trades.append({
                        'sym': sym,
                        'entry_ts': position['entry_ts'],
                        'exit_ts': ts,
                        'entry_px': entry_px,
                        'exit_px': sl_px,
                        'r_pnl': r_pnl - fee_r,
                        'type': 'TRAIL_SL' if r_pnl > 0 else 'SL'
                    })
                    position = None
                    watching = None
                    continue
                    
                # Check TP
                elif h >= tp_px:
                    all_trades.append({
                        'sym': sym,
                        'entry_ts': position['entry_ts'],
                        'exit_ts': ts,
                        'entry_px': entry_px,
                        'exit_px': tp_px,
                        'r_pnl': rr_ratio - fee_r,
                        'type': 'TP'
                    })
                    position = None
                    watching = None
                    continue
                    
            # Check Watch entry fill
            if watching and not position:
                if watching.get('status') == 'ARMED':
                    entry_px = watching['entry_px']
                    sl_px = watching['sl_px']
                    tp_px = watching['tp_px']
                    
                    if l <= entry_px:
                        if l <= sl_px * 1.002:
                            watching = None
                        else:
                            position = {
                                'sym': sym,
                                'entry_ts': ts,
                                'entry_px': entry_px,
                                'sl_px': sl_px,
                                'initial_sl_px': sl_px,
                                'tp_px': tp_px,
                                'peak_px': entry_px
                            }
                            watching = None
                            continue
                            
            # 15m Candle close checks
            if ts in bars_map:
                idx, b_closed = bars_map[ts]
                cur_t = b_closed['ts']
                is_green = b_closed['is_green']
                vol_mult = b_closed['vol_mult']
                cur_h, cur_l = b_closed['high'], b_closed['low']
                
                if watching and not position:
                    status = watching.get('status', 'ARMED')
                    if status == 'WAITING_2ND_GREEN':
                        if is_green:
                            tot_high = max(watching.get('high', cur_h), cur_h)
                            tot_low = min(watching.get('low', cur_l), cur_l)
                            rng = tot_high - tot_low
                            if rng > 0:
                                e_px = tot_high - (entry_fib * rng)
                                s_px = tot_high - (sl_fib * rng)
                                if (e_px - s_px) > 0:
                                    t_px = e_px + (rr_ratio * (e_px - s_px))
                                    watching['status'] = 'ARMED'
                                    watching['high'] = tot_high
                                    watching['low'] = tot_low
                                    watching['entry_px'] = e_px
                                    watching['sl_px'] = s_px
                                    watching['tp_px'] = t_px
                                else:
                                    watching = None
                            else:
                                watching = None
                        else:
                            watching = None
                    else:
                        if is_green:
                            tot_high = max(watching.get('high', cur_h), cur_h)
                            tot_low = watching.get('low', cur_l)
                            rng = tot_high - tot_low
                            if rng > 0:
                                e_px = tot_high - (entry_fib * rng)
                                s_px = tot_high - (sl_fib * rng)
                                t_px = e_px + (rr_ratio * (e_px - s_px))
                                watching['high'] = tot_high
                                watching['entry_px'] = e_px
                                watching['sl_px'] = s_px
                                watching['tp_px'] = t_px
                        else:
                            watching = None
                            
                if is_green and vol_mult >= spike_mult and not position and not watching:
                    if cur_t > last_spike_t:
                        last_spike_t = cur_t
                        prev_is_green = bars_15m[idx - 1]['is_green'] if idx >= 1 else False
                        
                        if require_2_green:
                            if prev_is_green:
                                tot_high = max(bars_15m[idx - 1]['high'], cur_h)
                                tot_low = min(bars_15m[idx - 1]['low'], cur_l)
                                rng = tot_high - tot_low
                                if rng > 0:
                                    e_px = tot_high - (entry_fib * rng)
                                    s_px = tot_high - (sl_fib * rng)
                                    t_px = e_px + (rr_ratio * (e_px - s_px))
                                    watching = {
                                        'status': 'ARMED',
                                        'high': tot_high,
                                        'low': tot_low,
                                        'entry_px': e_px,
                                        'sl_px': s_px,
                                        'tp_px': t_px
                                    }
                            else:
                                watching = {
                                    'status': 'WAITING_2ND_GREEN',
                                    'high': cur_h,
                                    'low': cur_l,
                                    'entry_px': 0.0,
                                    'sl_px': 0.0,
                                    'tp_px': 0.0
                                }
                        else:
                            rng = cur_h - cur_l
                            if rng > 0:
                                e_px = cur_h - (entry_fib * rng)
                                s_px = cur_h - (sl_fib * rng)
                                t_px = e_px + (rr_ratio * (e_px - s_px))
                                watching = {
                                    'status': 'ARMED',
                                    'high': cur_h,
                                    'low': cur_l,
                                    'entry_px': e_px,
                                    'sl_px': s_px,
                                    'tp_px': t_px
                                }
                                
    return all_trades

def run_chronological_account(candidate_trades, start_bal=100.0, risk_frac=0.01, max_concurrent=3):
    # Sort all candidates by entry time
    sorted_candidates = sorted(candidate_trades, key=lambda t: t['entry_ts'])
    
    balance = start_bal
    active_positions = []  # list of (exit_ts, net_dollar_pnl, r_pnl, sym)
    
    executed_trades = []
    peak_balance = start_bal
    max_drawdown_usd = 0.0
    
    for t in sorted_candidates:
        entry_t = t['entry_ts']
        
        # Clear positions that closed before this new trade's entry_t
        remaining = []
        for pos in active_positions:
            exit_t, d_pnl, r_pnl, sym = pos
            if exit_t <= entry_t:
                balance += d_pnl
                if balance > peak_balance:
                    peak_balance = balance
                dd = peak_balance - balance
                if dd > max_drawdown_usd:
                    max_drawdown_usd = dd
                executed_trades.append({
                    'sym': sym,
                    'r_pnl': r_pnl,
                    'dollar_pnl': d_pnl,
                    'balance': balance
                })
            else:
                remaining.append(pos)
        active_positions = remaining
        
        # Check max concurrent positions cap
        if len(active_positions) >= max_concurrent:
            continue  # Skip trade due to position concurrency limit
            
        # Calculate dollar risk based on current balance
        dollar_risk = balance * risk_frac
        r_pnl = t['r_pnl']
        dollar_pnl = dollar_risk * r_pnl
        
        active_positions.append((t['exit_ts'], dollar_pnl, r_pnl, t['sym']))
        
    # Clear any remaining active positions at end of month
    for exit_t, d_pnl, r_pnl, sym in sorted(active_positions, key=lambda x: x[0]):
        balance += d_pnl
        if balance > peak_balance:
            peak_balance = balance
        dd = peak_balance - balance
        if dd > max_drawdown_usd:
            max_drawdown_usd = dd
        executed_trades.append({
            'sym': sym,
            'r_pnl': r_pnl,
            'dollar_pnl': d_pnl,
            'balance': balance
        })
        
    return balance, executed_trades, peak_balance, max_drawdown_usd

def print_account_summary(title, start_bal, final_bal, trades, peak_bal, max_dd):
    net_profit = final_bal - start_bal
    roi = (net_profit / start_bal) * 100
    n = len(trades)
    wins = [t for t in trades if t['dollar_pnl'] > 0]
    losses = [t for t in trades if t['dollar_pnl'] <= 0]
    wr = (len(wins) / n * 100) if n > 0 else 0
    gp = sum(t['dollar_pnl'] for t in wins)
    gl = abs(sum(t['dollar_pnl'] for t in losses)) if losses else 1e-6
    pf = gp / gl
    
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"💵 {title}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  • Starting Capital       : ${start_bal:.2f}")
    print(f"  • Final Balance          : ${final_bal:.2f} ({'+' if net_profit >= 0 else ''}${net_profit:.2f})")
    print(f"  • Net Return (ROI)       : {roi:+.2f}%")
    print(f"  • Total Trades Taken     : {n}")
    print(f"  • Win Rate               : {wr:.1f}% ({len(wins)} Wins / {len(losses)} Losses)")
    print(f"  • Profit Factor          : {pf:.2f}")
    print(f"  • Peak Equity            : ${peak_bal:.2f}")
    print(f"  • Max Drawdown           : -${max_dd:.2f} ({(max_dd/peak_bal)*100:.1f}%)")
    print(f"  • Gross Profit / Loss    : +${gp:.2f} / -${gl:.2f}")
    print()

# Generate Candidate Trades for 30x and 10x
print("Simulating 30x Spikes with 2-Green-Candles...", flush=True)
c_30x_2g = extract_all_trade_candidates(raw_candles, require_2_green=True, spike_mult=30.0)

print("Simulating 10x Spikes with 2-Green-Candles...", flush=True)
c_10x_2g = extract_all_trade_candidates(raw_candles, require_2_green=True, spike_mult=10.0)

print("Simulating Baseline (30x Single Candle)...", flush=True)
c_30x_base = extract_all_trade_candidates(raw_candles, require_2_green=False, spike_mult=30.0)

print("==================================================================================")
print("💰 $100 ACCOUNT SIMULATION (CHRONOLOGICAL 1-MIN EXECUTION — JUNE 2026)")
print("==================================================================================")

# 1. 30x with 2 Green Candles (1% Risk)
b, tr, p, dd = run_chronological_account(c_30x_2g, start_bal=100.0, risk_frac=0.01, max_concurrent=3)
print_account_summary("1. 30x Spikes + 2-Green Gate (1.0% Risk / Trade)", 100.0, b, tr, p, dd)

# 2. 10x with 2 Green Candles (1% Risk)
b, tr, p, dd = run_chronological_account(c_10x_2g, start_bal=100.0, risk_frac=0.01, max_concurrent=3)
print_account_summary("2. 10x Spikes + 2-Green Gate (1.0% Risk / Trade)", 100.0, b, tr, p, dd)

# 3. 10x with 2 Green Candles (2% Risk)
b, tr, p, dd = run_chronological_account(c_10x_2g, start_bal=100.0, risk_frac=0.02, max_concurrent=3)
print_account_summary("3. 10x Spikes + 2-Green Gate (2.0% Risk / Trade)", 100.0, b, tr, p, dd)

# 4. 10x with 2 Green Candles (3% Risk)
b, tr, p, dd = run_chronological_account(c_10x_2g, start_bal=100.0, risk_frac=0.03, max_concurrent=3)
print_account_summary("4. 10x Spikes + 2-Green Gate (3.0% Risk / Trade)", 100.0, b, tr, p, dd)

# 5. Baseline (30x Single Candle, 1% Risk)
b, tr, p, dd = run_chronological_account(c_30x_base, start_bal=100.0, risk_frac=0.01, max_concurrent=3)
print_account_summary("5. Baseline 30x Single Candle (1.0% Risk / Trade)", 100.0, b, tr, p, dd)
