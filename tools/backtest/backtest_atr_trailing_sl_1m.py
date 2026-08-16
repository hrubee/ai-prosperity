#!/usr/bin/env python3
"""backtest_atr_trailing_sl_1m.py — 1-minute granularity backtest testing ATR-Based Trailing Stop Loss
Compares:
1. Current Fixed R Trailing SL (2.0R activation, 2.0R trailing)
2. Immediate ATR Trailing (SL = max(SL, Peak - k * ATR_15m)) for k = 1.0, 1.5, 2.0, 2.5, 3.0
3. Profit-Activated ATR Trailing (Activate when Profit >= 1.5 ATR, trail k * ATR)
Data: datasets/june_2026_1m.db (15.3M 1m candles across all 357 coins)
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

def run_simulation_atr(raw_data, trail_mode="fixed_r", atr_k=2.0, atr_act_k=0.0, fixed_act_r=2.0, fixed_dist_r=2.0, spike_mult=10.0, entry_fib=0.60, sl_fib=0.70, rr_ratio=5.0):
    tf_ms = 15 * 60 * 1000
    fee_r = 0.05
    trades = []
    
    for sym, rows in raw_data.items():
        # 1. Resample to 15m bars and compute 14-period ATR
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
            
        # Compute 15m Volume MA and 14-period ATR
        vols = [b['volume'] for b in bars_15m]
        tr_list = []
        for idx, b in enumerate(bars_15m):
            start_i = max(0, idx - 40)
            w = vols[start_i:idx]
            b['vol_ma'] = np.mean(w) if len(w) > 0 else (vols[0] if vols else 1.0)
            b['vol_mult'] = (b['volume'] / b['vol_ma']) if b['vol_ma'] > 0 else 0
            b['is_green'] = b['close'] >= b['open']
            
            # True Range
            if idx == 0:
                tr = b['high'] - b['low']
            else:
                prev_c = bars_15m[idx - 1]['close']
                tr = max(b['high'] - b['low'], abs(b['high'] - prev_c), abs(b['low'] - prev_c))
            tr_list.append(tr)
            
            # 14-period ATR SMA
            atr_w = tr_list[max(0, idx - 14):idx + 1]
            b['atr'] = np.mean(atr_w) if atr_w else (b['high'] - b['low'])
            
        bars_map = {b['ts'] + tf_ms: (idx, b) for idx, b in enumerate(bars_15m)}
        
        watching = None
        position = None
        last_spike_t = 0
        
        for c in rows:
            ts, o, h, l, cl, v = c
            
            # A. Manage Active Position
            if position:
                entry_px = position['entry_px']
                sl_px = position['sl_px']
                initial_sl_px = position['initial_sl_px']
                tp_px = position['tp_px']
                pos_atr = position['atr']
                risk_unit = entry_px - initial_sl_px
                
                # Check New High for Trailing SL Ratchet
                if h > position['peak_px']:
                    position['peak_px'] = h
                    
                    if trail_mode == "fixed_r":
                        if risk_unit > 0:
                            peak_r = (h - entry_px) / risk_unit
                            if peak_r >= fixed_act_r:
                                desired_sl = h - (fixed_dist_r * risk_unit)
                                if desired_sl > position['sl_px']:
                                    position['sl_px'] = desired_sl
                                    sl_px = desired_sl
                                    
                    elif trail_mode == "atr_immediate":
                        # Trail directly k * ATR behind peak high
                        desired_sl = h - (atr_k * pos_atr)
                        if desired_sl > position['sl_px']:
                            position['sl_px'] = desired_sl
                            sl_px = desired_sl
                            
                    elif trail_mode == "atr_activated":
                        # Only activate trailing once profit >= atr_act_k * ATR
                        profit_dist = h - entry_px
                        if profit_dist >= (atr_act_k * pos_atr):
                            desired_sl = h - (atr_k * pos_atr)
                            if desired_sl > position['sl_px']:
                                position['sl_px'] = desired_sl
                                sl_px = desired_sl
                
                # Check SL Hit
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
                    
                # Check TP Hit
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
                    
            # B. Check Watch Fill
            if watching and not position:
                if watching.get('status') == 'ARMED':
                    entry_px = watching['entry_px']
                    sl_px = watching['sl_px']
                    tp_px = watching['tp_px']
                    pos_atr = watching['atr']
                    
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
                                'atr': pos_atr,
                                'peak_px': entry_px
                            }
                            watching = None
                            continue
                            
            # C. 15m Candle Close
            if ts in bars_map:
                idx, b_closed = bars_map[ts]
                cur_t = b_closed['ts']
                is_green = b_closed['is_green']
                vol_mult = b_closed['vol_mult']
                cur_h, cur_l = b_closed['high'], b_closed['low']
                cur_atr = b_closed['atr']
                
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
                                    watching['atr'] = cur_atr
                                    watching['last_eval_t'] = cur_t
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
                                watching['atr'] = cur_atr
                                watching['last_eval_t'] = cur_t
                        else:
                            watching = None
                            
                if is_green and vol_mult >= spike_mult and not position and not watching:
                    if cur_t > last_spike_t:
                        last_spike_t = cur_t
                        prev_is_green = bars_15m[idx - 1]['is_green'] if idx >= 1 else False
                        
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
                                    'atr': cur_atr,
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
                                'atr': cur_atr,
                                'last_eval_t': cur_t
                            }
                            
    return trades

def print_metrics(name, trades):
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
print("📊 ATR-BASED TRAILING STOP LOSS BACKTEST (JUNE 2026 - 15.3M 1M CANDLES)")
print("==================================================================================")

# 1. Benchmark: Fixed R Trailing Stop (2.0R Act / 2.0R Trail)
t_fixed = run_simulation_atr(raw_candles, trail_mode="fixed_r", fixed_act_r=2.0, fixed_dist_r=2.0)
print_metrics("1. BENCHMARK: Fixed R Trailing SL (2.0R Act / 2.0R Dist)", t_fixed)

# 2. Immediate ATR Trailing: k = 1.0, 1.5, 2.0, 2.5, 3.0 ATR
for k in [1.0, 1.5, 2.0, 2.5, 3.0]:
    t_atr_imm = run_simulation_atr(raw_candles, trail_mode="atr_immediate", atr_k=k)
    print_metrics(f"2. Immediate ATR Trailing: {k:.1f} × ATR(14)", t_atr_imm)

# 3. Profit-Activated ATR Trailing: Activate at +1.5 ATR profit, trail k * ATR
for k in [1.0, 1.5, 2.0]:
    t_atr_act = run_simulation_atr(raw_candles, trail_mode="atr_activated", atr_k=k, atr_act_k=1.5)
    print_metrics(f"3. Activated ATR Trailing: Act @ +1.5 ATR, Trail {k:.1f} × ATR", t_atr_act)

print("==================================================================================")
print("💰 $100 ACCOUNT CHRONOLOGICAL COMPARISON (1% RISK / TRADE)")
print("==================================================================================")
b_f, _, p_f, dd_f = run_account_sim(t_fixed, start_bal=100.0, risk_frac=0.01)
print(f"• Fixed 2.0R Trailing SL       : Final Balance = ${b_f:.2f} ({b_f-100:+.2f}%), Max DD = -${dd_f:.2f} ({(dd_f/p_f)*100:.1f}%)")

for k in [1.5, 2.0, 2.5, 3.0]:
    t_sim = run_simulation_atr(raw_candles, trail_mode="atr_immediate", atr_k=k)
    b_k, _, p_k, dd_k = run_account_sim(t_sim, start_bal=100.0, risk_frac=0.01)
    print(f"• Immediate {k:.1f}×ATR Trailing SL: Final Balance = ${b_k:.2f} ({b_k-100:+.2f}%), Max DD = -${dd_k:.2f} ({(dd_k/p_k)*100:.1f}%)")
