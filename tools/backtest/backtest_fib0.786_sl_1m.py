#!/usr/bin/env python3
"""backtest_fib0.786_sl_1m.py — 1-minute granularity backtest testing 0.786 Fib SL level
Compares SL = 0.700 vs SL = 0.786 (Golden Ratio Retracement Extension)
Data: datasets/june_2026_1m.db (15.3M 1m candles)
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

def run_simulation(raw_data, require_2_green=True, spike_mult=10.0, entry_fib=0.600, sl_fib=0.786, rr_ratio=5.0, trail_act_r=2.0, trail_dist_r=2.0):
    tf_ms = 15 * 60 * 1000
    fee_r = 0.05
    
    trades = []
    skipped_by_filter = 0
    total_spikes_detected = 0
    
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
            
            # Position management
            if position:
                entry_px = position['entry_px']
                sl_px = position['sl_px']
                initial_sl_px = position['initial_sl_px']
                tp_px = position['tp_px']
                risk_unit = entry_px - initial_sl_px
                
                # Trailing SL
                if h > position['peak_px']:
                    position['peak_px'] = h
                    if risk_unit > 0:
                        peak_r = (h - entry_px) / risk_unit
                        if peak_r >= trail_act_r:
                            desired_sl = h - (trail_dist_r * risk_unit)
                            if desired_sl > position['sl_px']:
                                position['sl_px'] = desired_sl
                                sl_px = desired_sl
                                
                # SL hit
                if l <= sl_px:
                    r_pnl = (sl_px - entry_px) / risk_unit if risk_unit > 0 else -1.0
                    trades.append({
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
                    
                # TP hit
                elif h >= tp_px:
                    trades.append({
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
                    
            # Watch fill
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
                            
            # 15m candle close
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
                                    watching['last_eval_t'] = cur_t
                                else:
                                    watching = None
                            else:
                                watching = None
                        else:
                            skipped_by_filter += 1
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
                                watching['last_eval_t'] = cur_t
                        else:
                            watching = None
                            
                if is_green and vol_mult >= spike_mult and not position and not watching:
                    if cur_t > last_spike_t:
                        total_spikes_detected += 1
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
                                        'tp_px': t_px,
                                        'last_eval_t': cur_t
                                    }
                            else:
                                watching = {
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
                                e_px = cur_h - (entry_fib * rng)
                                s_px = cur_h - (sl_fib * rng)
                                t_px = e_px + (rr_ratio * (e_px - s_px))
                                watching = {
                                    'status': 'ARMED',
                                    'high': cur_h,
                                    'low': cur_l,
                                    'entry_px': e_px,
                                    'sl_px': s_px,
                                    'tp_px': t_px,
                                    'last_eval_t': cur_t
                                }

    return trades, total_spikes_detected, skipped_by_filter

def print_metrics(name, trades, total_spikes, skipped_filter=0):
    n = len(trades)
    if n == 0:
        print(f"{name}: No trades triggered.")
        return
        
    pnl_r = [t['r_pnl'] for t in trades]
    wins = [r for r in pnl_r if r > 0]
    losses = [r for r in pnl_r if r <= 0]
    wr = len(wins) / n * 100
    tot_r = sum(pnl_r)
    gross_win_r = sum(wins)
    gross_loss_r = abs(sum(losses)) if sum(losses) != 0 else 1e-6
    pf = gross_win_r / gross_loss_r
    avg_win_r = np.mean(wins) if wins else 0
    avg_loss_r = np.mean(losses) if losses else 0
    
    equity_curve = np.cumsum([0] + pnl_r)
    peak = np.maximum.accumulate(equity_curve)
    drawdown = peak - equity_curve
    max_dd_r = np.max(drawdown)
    
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"📌 {name}")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  • Trades Executed (Filled): {n:,}")
    print(f"  • Win Rate                : {wr:.1f}% ({len(wins)} Wins / {len(losses)} Losses)")
    print(f"  • Total Return            : {tot_r:+.2f} R")
    print(f"  • Profit Factor           : {pf:.2f}")
    print(f"  • Max Drawdown            : {max_dd_r:.2f} R")
    print(f"  • Avg Win / Avg Loss      : +{avg_win_r:.2f} R / {avg_loss_r:.2f} R (Expectancy: {tot_r/n:+.2f} R/trade)")
    print()

def run_account_sim(trades, start_bal=100.0, risk_frac=0.01, max_concurrent=3):
    sorted_candidates = sorted(trades, key=lambda t: t['entry_ts'])
    balance = start_bal
    active_positions = []
    executed_trades = []
    peak_balance = start_bal
    max_drawdown_usd = 0.0
    
    for t in sorted_candidates:
        entry_t = t['entry_ts']
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
                executed_trades.append({'sym': sym, 'r_pnl': r_pnl, 'dollar_pnl': d_pnl, 'balance': balance})
            else:
                remaining.append(pos)
        active_positions = remaining
        
        if len(active_positions) >= max_concurrent:
            continue
            
        dollar_risk = balance * risk_frac
        r_pnl = t['r_pnl']
        dollar_pnl = dollar_risk * r_pnl
        active_positions.append((t['exit_ts'], dollar_pnl, r_pnl, t['sym']))
        
    for exit_t, d_pnl, r_pnl, sym in sorted(active_positions, key=lambda x: x[0]):
        balance += d_pnl
        if balance > peak_balance:
            peak_balance = balance
        dd = peak_balance - balance
        if dd > max_drawdown_usd:
            max_drawdown_usd = dd
        executed_trades.append({'sym': sym, 'r_pnl': r_pnl, 'dollar_pnl': d_pnl, 'balance': balance})
        
    return balance, executed_trades, peak_balance, max_drawdown_usd

print("==================================================================================")
print("🎯 STOP LOSS FIBONACCI LEVEL COMPARISON (0.700 vs 0.786)")
print("==================================================================================")

# 1. 10x Spikes + 2 Green Candles: SL 0.700 vs SL 0.786 (1:5 RR)
print("1. Testing 10x Spikes (2 Green Candles): SL 0.700 vs SL 0.786 (1:5 RR)...", flush=True)
t_10x_sl70, s1, sk1 = run_simulation(raw_candles, require_2_green=True, spike_mult=10.0, entry_fib=0.600, sl_fib=0.700, rr_ratio=5.0)
print_metrics("10x Spikes + 2-Green: Entry=0.600 | SL=0.700 | TP=1:5 RR", t_10x_sl70, s1, sk1)

t_10x_sl786, s2, sk2 = run_simulation(raw_candles, require_2_green=True, spike_mult=10.0, entry_fib=0.600, sl_fib=0.786, rr_ratio=5.0)
print_metrics("10x Spikes + 2-Green: Entry=0.600 | SL=0.786 | TP=1:5 RR", t_10x_sl786, s2, sk2)

# 2. 10x Spikes with 1:4 and 1:3 RR for 0.786 SL
t_10x_sl786_rr3, _, _ = run_simulation(raw_candles, require_2_green=True, spike_mult=10.0, entry_fib=0.600, sl_fib=0.786, rr_ratio=3.0)
print_metrics("10x Spikes + 2-Green: Entry=0.600 | SL=0.786 | TP=1:3 RR", t_10x_sl786_rr3, s2, sk2)

# 3. 30x Spikes: SL 0.700 vs SL 0.786
print("2. Testing 30x Spikes (2 Green Candles): SL 0.700 vs SL 0.786...", flush=True)
t_30x_sl70, s3, sk3 = run_simulation(raw_candles, require_2_green=True, spike_mult=30.0, entry_fib=0.600, sl_fib=0.700, rr_ratio=5.0)
print_metrics("30x Spikes + 2-Green: Entry=0.600 | SL=0.700 | TP=1:5 RR", t_30x_sl70, s3, sk3)

t_30x_sl786, s4, sk4 = run_simulation(raw_candles, require_2_green=True, spike_mult=30.0, entry_fib=0.600, sl_fib=0.786, rr_ratio=5.0)
print_metrics("30x Spikes + 2-Green: Entry=0.600 | SL=0.786 | TP=1:5 RR", t_30x_sl786, s4, sk4)

print("==================================================================================")
print("💰 $100 ACCOUNT CHRONOLOGICAL COMPARISON (1% RISK / TRADE)")
print("==================================================================================")
b_70, tr_70, p_70, dd_70 = run_account_sim(t_10x_sl70, start_bal=100.0, risk_frac=0.01)
print(f"• 10x Spikes with SL=0.700: Final Balance = ${b_70:.2f} (Net Return: {b_70-100:+.2f}%), Max DD = -${dd_70:.2f} ({(dd_70/p_70)*100:.1f}%)")

b_786, tr_786, p_786, dd_786 = run_account_sim(t_10x_sl786, start_bal=100.0, risk_frac=0.01)
print(f"• 10x Spikes with SL=0.786 (1:5 RR): Final Balance = ${b_786:.2f} (Net Return: {b_786-100:+.2f}%), Max DD = -${dd_786:.2f} ({(dd_786/p_786)*100:.1f}%)")

b_786_3, tr_786_3, p_786_3, dd_786_3 = run_account_sim(t_10x_sl786_rr3, start_bal=100.0, risk_frac=0.01)
print(f"• 10x Spikes with SL=0.786 (1:3 RR): Final Balance = ${b_786_3:.2f} (Net Return: {b_786_3-100:+.2f}%), Max DD = -${dd_786_3:.2f} ({(dd_786_3/p_786_3)*100:.1f}%)")
