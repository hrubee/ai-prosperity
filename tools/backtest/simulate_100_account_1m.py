#!/usr/bin/env python3
"""simulate_100_account_1m.py — Chronological $100 Account Simulation across June 2026 (15.3M 1m candles)
Simulates exact live-bot behavior:
- Starting Capital: $100.00
- Risk per Trade: 1.0% ($1.00 initial risk) and 2.0% ($2.00 initial risk)
- Max Concurrent Open Positions: 3
- Dynamic Compounding: Position size scales with real-time wallet equity
- Real CoinDCX Taker Fees: 0.05% on entry + 0.05% on exit
- Trailing SL: 2.0R activation, 2.0R trailing distance
- 1-minute resolution execution for entries, SL, TP, and Trailing SL
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

print(f"Loaded {len(raw_candles)} symbols ({sum(len(r) for r in raw_candles.values()):,} bars) in {time.time()-t0:.2f}s.\n", flush=True)

def simulate_chronological_account(raw_data, require_2_green=True, spike_mult=10.0, risk_frac=0.01, start_bal=100.0, max_concurrent=3):
    tf_ms = 15 * 60 * 1000
    fee_rate = 0.0005  # 0.05% CoinDCX taker fee
    
    # 1. Preprocess 15m bars and signals for each coin
    coin_bars_15m = {}
    coin_signals = {}
    
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
            
        coin_bars_15m[sym] = {b['ts'] + tf_ms: (idx, b, bars_15m) for idx, b in enumerate(bars_15m)}

    # 2. Build global timeline of unique minute timestamps across all coins
    all_timestamps = set()
    candles_by_ts = {}
    for sym, rows in raw_data.items():
        for c in rows:
            ts, o, h, l, cl, v = c
            all_timestamps.add(ts)
            candles_by_ts.setdefault(ts, {})[sym] = (o, h, l, cl, v)
            
    sorted_ts = sorted(list(all_timestamps))
    
    # 3. Chronological Global Simulation
    balance = start_bal
    positions = {}  # sym -> pos_dict
    watching = {}   # sym -> watch_dict
    last_spikes = {}
    
    trade_history = []
    equity_curve = []
    peak_balance = start_bal
    max_drawdown_usd = 0.0
    
    for ts in sorted_ts:
        minute_map = candles_by_ts.get(ts, {})
        
        # A. Position Management (Minute resolution across all active positions)
        for sym in list(positions.keys()):
            if sym not in minute_map:
                continue
            o, h, l, cl, v = minute_map[sym]
            pos = positions[sym]
            entry_px = pos['entry_px']
            sl_px = pos['sl_px']
            tp_px = pos['tp_px']
            qty = pos['qty']
            risk_unit = entry_px - pos['initial_sl_px']
            
            # Check Trailing SL
            if h > pos['peak_px']:
                pos['peak_px'] = h
                if risk_unit > 0:
                    peak_r = (h - entry_px) / risk_unit
                    if peak_r >= 2.0:
                        desired_sl = h - (2.0 * risk_unit)
                        if desired_sl > pos['sl_px']:
                            pos['sl_px'] = desired_sl
                            sl_px = desired_sl
                            
            # Check SL / Trailing SL Hit
            if l <= sl_px:
                exit_px = sl_px
                entry_notional = entry_px * qty
                exit_notional = exit_px * qty
                fees = (entry_notional + exit_notional) * fee_rate
                gross_pnl = (exit_px - entry_px) * qty
                net_pnl = gross_pnl - fees
                
                balance += net_pnl
                trade_history.append({
                    'sym': sym,
                    'type': 'TRAIL_SL' if gross_pnl > 0 else 'SL',
                    'entry_px': entry_px,
                    'exit_px': exit_px,
                    'qty': qty,
                    'net_pnl': net_pnl,
                    'r_mult': net_pnl / (risk_unit * qty) if risk_unit > 0 else -1.0,
                    'balance': balance,
                    'exit_ts': ts
                })
                del positions[sym]
                if sym in watching:
                    del watching[sym]
                continue
                
            # Check TP Hit (1:5 RR)
            elif h >= tp_px:
                exit_px = tp_px
                entry_notional = entry_px * qty
                exit_notional = exit_px * qty
                fees = (entry_notional + exit_notional) * fee_rate
                gross_pnl = (exit_px - entry_px) * qty
                net_pnl = gross_pnl - fees
                
                balance += net_pnl
                trade_history.append({
                    'sym': sym,
                    'type': 'TP',
                    'entry_px': entry_px,
                    'exit_px': exit_px,
                    'qty': qty,
                    'net_pnl': net_pnl,
                    'r_mult': 5.0,
                    'balance': balance,
                    'exit_ts': ts
                })
                del positions[sym]
                if sym in watching:
                    del watching[sym]
                continue
                
        # B. Limit Order Entry Checks (enforcing MAX_CONCURRENT cap)
        for sym in list(watching.keys()):
            if sym in positions:
                del watching[sym]
                continue
            if len(positions) >= max_concurrent:
                break
            if sym not in minute_map:
                continue
                
            watch = watching[sym]
            if watch.get('status') != 'ARMED':
                continue
                
            o, h, l, cl, v = minute_map[sym]
            entry_px = watch['entry_px']
            sl_px = watch['sl_px']
            tp_px = watch['tp_px']
            
            if l <= entry_px:
                # Slippage guard
                if l <= sl_px * 1.002:
                    del watching[sym]
                    continue
                    
                risk_dist = entry_px - sl_px
                if risk_dist <= 0:
                    del watching[sym]
                    continue
                    
                # Position Sizing based on current wallet equity & risk fraction
                risk_usd = balance * risk_frac
                qty = risk_usd / risk_dist
                
                # Check min size & margin safety
                notional = entry_px * qty
                if notional < 1.0 or notional > balance * 20.0:  # Max 20x leverage cap
                    qty = (balance * 20.0) / entry_px
                    
                positions[sym] = {
                    'sym': sym,
                    'entry_px': entry_px,
                    'sl_px': sl_px,
                    'initial_sl_px': sl_px,
                    'tp_px': tp_px,
                    'qty': qty,
                    'peak_px': entry_px,
                    'entry_ts': ts
                }
                del watching[sym]
                
        # C. 15m Candle Boundary Updates & New Signal Evaluation
        for sym, minute_data in minute_map.items():
            if sym in positions:
                continue
            if sym not in coin_bars_15m:
                continue
                
            bars_info = coin_bars_15m[sym].get(ts)
            if not bars_info:
                continue
                
            idx, b_closed, all_15m = bars_info
            cur_t = b_closed['ts']
            is_green = b_closed['is_green']
            vol_mult = b_closed['vol_mult']
            cur_h, cur_l = b_closed['high'], b_closed['low']
            cur_o, cur_c = b_closed['open'], b_closed['close']
            
            # 1. Existing watch update
            if sym in watching:
                watch = watching[sym]
                status = watch.get('status', 'ARMED')
                
                if status == 'WAITING_2ND_GREEN':
                    if is_green:
                        # 2nd Green closed -> Arm!
                        tot_high = max(watch.get('high', cur_h), cur_h)
                        tot_low = min(watch.get('low', cur_l), cur_l)
                        rng = tot_high - tot_low
                        if rng > 0:
                            e_px = tot_high - (0.60 * rng)
                            s_px = tot_high - (0.70 * rng)
                            if (e_px - s_px) > 0:
                                t_px = e_px + (5.0 * (e_px - s_px))
                                watch['status'] = 'ARMED'
                                watch['high'] = tot_high
                                watch['low'] = tot_low
                                watch['entry_px'] = e_px
                                watch['sl_px'] = s_px
                                watch['tp_px'] = t_px
                                watch['last_eval_t'] = cur_t
                            else:
                                del watching[sym]
                        else:
                            del watching[sym]
                    else:
                        # Red candle -> Cancel
                        del watching[sym]
                else:
                    # Already ARMED
                    if is_green:
                        tot_high = max(watch.get('high', cur_h), cur_h)
                        tot_low = watch.get('low', cur_l)
                        rng = tot_high - tot_low
                        if rng > 0:
                            e_px = tot_high - (0.60 * rng)
                            s_px = tot_high - (0.70 * rng)
                            t_px = e_px + (5.0 * (e_px - s_px))
                            watch['high'] = tot_high
                            watch['entry_px'] = e_px
                            watch['sl_px'] = s_px
                            watch['tp_px'] = t_px
                            watch['last_eval_t'] = cur_t
                    else:
                        del watching[sym]
                        
            # 2. New Spike Check
            if is_green and vol_mult >= spike_mult and sym not in positions and sym not in watching:
                last_t = last_spikes.get(sym, 0)
                if cur_t > last_t:
                    last_spikes[sym] = cur_t
                    prev_is_green = all_15m[idx - 1]['is_green'] if idx >= 1 else False
                    
                    if require_2_green:
                        if prev_is_green:
                            tot_high = max(all_15m[idx - 1]['high'], cur_h)
                            tot_low = min(all_15m[idx - 1]['low'], cur_l)
                            rng = tot_high - tot_low
                            if rng > 0:
                                e_px = tot_high - (0.60 * rng)
                                s_px = tot_high - (0.70 * rng)
                                t_px = e_px + (5.0 * (e_px - s_px))
                                watching[sym] = {
                                    'status': 'ARMED',
                                    'high': tot_high,
                                    'low': tot_low,
                                    'entry_px': e_px,
                                    'sl_px': s_px,
                                    'tp_px': t_px,
                                    'last_eval_t': cur_t
                                }
                        else:
                            watching[sym] = {
                                'status': 'WAITING_2ND_GREEN',
                                'high': cur_h,
                                'low': cur_l,
                                'entry_px': 0.0,
                                'sl_px': 0.0,
                                'tp_px': 0.0,
                                'last_eval_t': cur_t
                            }
                    else:
                        rng = cur_h - cur_l
                        if rng > 0:
                            e_px = cur_h - (0.60 * rng)
                            s_px = cur_h - (0.70 * rng)
                            t_px = e_px + (5.0 * (e_px - s_px))
                            watching[sym] = {
                                'status': 'ARMED',
                                'high': cur_h,
                                'low': cur_l,
                                'entry_px': e_px,
                                'sl_px': s_px,
                                'tp_px': t_px,
                                'last_eval_t': cur_t
                            }

        # Track Drawdown
        if balance > peak_balance:
            peak_balance = balance
        dd_usd = peak_balance - balance
        if dd_usd > max_drawdown_usd:
            max_drawdown_usd = dd_usd
            
        equity_curve.append(balance)

    return balance, trade_history, peak_balance, max_drawdown_usd

def report_account_results(title, start_bal, final_bal, trades, peak_bal, max_dd_usd):
    net_profit = final_bal - start_bal
    roi = (net_profit / start_bal) * 100
    n = len(trades)
    wins = [t for t in trades if t['net_pnl'] > 0]
    losses = [t for t in trades if t['net_pnl'] <= 0]
    wr = (len(wins) / n * 100) if n > 0 else 0
    gross_profit = sum(t['net_pnl'] for t in wins)
    gross_loss = abs(sum(t['net_pnl'] for t in losses)) if losses else 1e-6
    pf = gross_profit / gross_loss
    
    print("==================================================================================")
    print(f"💰 {title}")
    print("==================================================================================")
    print(f"  • Starting Balance       : ${start_bal:.2f}")
    print(f"  • Final Account Balance  : ${final_bal:.2f} ({'+' if net_profit >= 0 else ''}{net_profit:.2f} USD)")
    print(f"  • Net Return (ROI)       : {roi:+.2f}%")
    print(f"  • Total Trades Executed  : {n}")
    print(f"  • Win Rate               : {wr:.1f}% ({len(wins)} Wins / {len(losses)} Losses)")
    print(f"  • Profit Factor          : {pf:.2f}")
    print(f"  • Peak Balance           : ${peak_bal:.2f}")
    print(f"  • Max Drawdown ($)       : -${max_dd_usd:.2f} ({(max_dd_usd/peak_bal)*100:.1f}% from peak)")
    print(f"  • Avg Win / Avg Loss     : +${gross_profit/max(1,len(wins)):.2f} / -${gross_loss/max(1,len(losses)):.2f}")
    print()

print("Simulating Scenarios on $100 Account...", flush=True)

# 1. 30x Volume Spikes with 2-Green-Candles Filter (1% Risk)
b1, t1, p1, dd1 = simulate_chronological_account(raw_candles, require_2_green=True, spike_mult=30.0, risk_frac=0.01, start_bal=100.0)
report_account_results("SCENARIO A: 30x Spikes + 2 Green Candles Gate (1.0% Risk / Trade)", 100.0, b1, t1, p1, dd1)

# 2. 10x Volume Spikes with 2-Green-Candles Filter (1% Risk)
b2, t2, p2, dd2 = simulate_chronological_account(raw_candles, require_2_green=True, spike_mult=10.0, risk_frac=0.01, start_bal=100.0)
report_account_results("SCENARIO B: 10x Spikes + 2 Green Candles Gate (1.0% Risk / Trade)", 100.0, b2, t2, p2, dd2)

# 3. 10x Volume Spikes with 2-Green-Candles Filter (2% Risk)
b3, t3, p3, dd3 = simulate_chronological_account(raw_candles, require_2_green=True, spike_mult=10.0, risk_frac=0.02, start_bal=100.0)
report_account_results("SCENARIO C: 10x Spikes + 2 Green Candles Gate (2.0% Risk / Trade)", 100.0, b3, t3, p3, dd3)
