#!/usr/bin/env python3
"""backtest_fibvol_2green_1m.py — Rigorous 1-minute granularity backtest of FibVol Strategy:
Baseline (Single Green Spike) vs Improved (2 Consecutive Green Candles Confirmation Gate).

Data source: datasets/june_2026_1m.db (15.3M 1m candles across all coins)
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
print(f"Found {len(symbols)} symbols. Fetching minute candles...", flush=True)

raw_candles = {}
for sym in symbols:
    rows = cursor.execute(
        "SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC",
        (sym,)
    ).fetchall()
    if len(rows) >= 500:
        raw_candles[sym] = rows

print(f"Loaded {len(raw_candles)} active symbols ({sum(len(r) for r in raw_candles.values()):,} 1m bars) in {time.time()-t0:.2f}s.\n", flush=True)

def run_simulation(raw_data, require_2_green=False, spike_mult=30.0, entry_fib=0.60, sl_fib=0.70, rr_ratio=5.0, trail_act_r=2.0, trail_dist_r=2.0):
    tf_ms = 15 * 60 * 1000  # 15m
    fee_r = 0.05  # Realistic 0.05R exchange fee & slippage per trade
    
    trades = []
    skipped_by_filter = 0
    total_spikes_detected = 0
    
    for sym, rows in raw_data.items():
        # 1. Resample to 15m bars
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
        
        # State tracking for minute simulation
        watching = None
        position = None
        last_spike_t = 0
        
        for c in rows:
            ts, o, h, l, cl, v = c
            
            # A. Manage Position with 1-minute Granularity
            if position:
                entry_px = position['entry_px']
                initial_sl_px = position['initial_sl_px']
                sl_px = position['sl_px']
                tp_px = position['tp_px']
                risk_unit = entry_px - initial_sl_px
                
                # Check Trailing SL step-up
                if h > position['peak_px']:
                    position['peak_px'] = h
                    if risk_unit > 0:
                        peak_r = (h - entry_px) / risk_unit
                        if peak_r >= trail_act_r:
                            desired_sl = h - (trail_dist_r * risk_unit)
                            if desired_sl > position['sl_px']:
                                position['sl_px'] = desired_sl
                                sl_px = desired_sl
                
                # Check Stop Loss (or Trailing SL) Hit
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
                    
                # Check Take Profit Hit (1:5 RR)
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
                    
            # B. Check Watchlist Entry Fill (at 1-minute granularity)
            if watching and not position:
                if watching.get('status') == 'ARMED':
                    entry_px = watching['entry_px']
                    sl_px = watching['sl_px']
                    tp_px = watching['tp_px']
                    
                    # If minute price retraced to entry price
                    if l <= entry_px:
                        # Slippage guard: if already below SL * 1.002, skip
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
                            
            # C. 15m Candle Boundary Check
            if ts in bars_map:
                idx, b_closed = bars_map[ts]
                cur_t = b_closed['ts']
                is_green = b_closed['is_green']
                vol_mult = b_closed['vol_mult']
                cur_h, cur_l = b_closed['high'], b_closed['low']
                cur_o, cur_c = b_closed['open'], b_closed['close']
                
                # Check existing watch updates
                if watching and not position:
                    status = watching.get('status', 'ARMED')
                    
                    if status == 'WAITING_2ND_GREEN':
                        if is_green:
                            # 2nd green candle completed!
                            tot_high = max(watching.get('high', cur_h), cur_h)
                            tot_low = min(watching.get('low', cur_l), cur_l)
                            rng = tot_high - tot_low
                            if rng > 0:
                                entry_px = tot_high - (entry_fib * rng)
                                sl_px = tot_high - (sl_fib * rng)
                                if (entry_px - sl_px) > 0:
                                    risk = entry_px - sl_px
                                    tp_px = entry_px + (rr_ratio * risk)
                                    watching['status'] = 'ARMED'
                                    watching['entry_px'] = entry_px
                                    watching['sl_px'] = sl_px
                                    watching['tp_px'] = tp_px
                                    watching['high'] = tot_high
                                    watching['low'] = tot_low
                                    watching['last_eval_t'] = cur_t
                                else:
                                    watching = None
                            else:
                                watching = None
                        else:
                            # 2nd candle closed RED -> filter failed!
                            skipped_by_filter += 1
                            watching = None
                    else:
                        # Already ARMED
                        if is_green:
                            # Update to higher high
                            tot_high = max(watching.get('high', cur_h), cur_h)
                            tot_low = watching.get('low', cur_l)
                            rng = tot_high - tot_low
                            if rng > 0:
                                entry_px = tot_high - (entry_fib * rng)
                                sl_px = tot_high - (sl_fib * rng)
                                risk = entry_px - sl_px
                                tp_px = entry_px + (rr_ratio * risk)
                                watching['high'] = tot_high
                                watching['entry_px'] = entry_px
                                watching['sl_px'] = sl_px
                                watching['tp_px'] = tp_px
                                watching['last_eval_t'] = cur_t
                        else:
                            # Red candle closed -> cancel watch
                            watching = None
                            
                # Check for NEW Spike
                if is_green and vol_mult >= spike_mult and not position and not watching:
                    if cur_t > last_spike_t:
                        total_spikes_detected += 1
                        last_spike_t = cur_t
                        prev_is_green = bars_15m[idx - 1]['is_green'] if idx >= 1 else False
                        
                        if require_2_green:
                            if prev_is_green:
                                # Both prior and spike are green!
                                tot_high = max(bars_15m[idx - 1]['high'], cur_h)
                                tot_low = min(bars_15m[idx - 1]['low'], cur_l)
                                rng = tot_high - tot_low
                                if rng > 0:
                                    entry_px = tot_high - (entry_fib * rng)
                                    sl_px = tot_high - (sl_fib * rng)
                                    risk = entry_px - sl_px
                                    tp_px = entry_px + (rr_ratio * risk)
                                    watching = {
                                        'status': 'ARMED',
                                        'high': tot_high,
                                        'low': tot_low,
                                        'entry_px': entry_px,
                                        'sl_px': sl_px,
                                        'tp_px': tp_px,
                                        'last_eval_t': cur_t
                                    }
                            else:
                                # Spike is Green 1, wait for Green 2
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
                            # Baseline (single green spike candle)
                            rng = cur_h - cur_l
                            if rng > 0:
                                entry_px = cur_h - (entry_fib * rng)
                                sl_px = cur_h - (sl_fib * rng)
                                risk = entry_px - sl_px
                                tp_px = entry_px + (rr_ratio * risk)
                                watching = {
                                    'status': 'ARMED',
                                    'high': cur_h,
                                    'low': cur_l,
                                    'entry_px': entry_px,
                                    'sl_px': sl_px,
                                    'tp_px': tp_px,
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
    
    # Calculate Max Drawdown in R
    equity_curve = np.cumsum([0] + pnl_r)
    peak = np.maximum.accumulate(equity_curve)
    drawdown = peak - equity_curve
    max_dd_r = np.max(drawdown)
    
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"📌 {name}")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  • Total Spikes Detected   : {total_spikes:,}")
    if skipped_filter > 0:
        print(f"  • Filtered Out (Bad Spikes): {skipped_filter:,} ({skipped_filter/total_spikes*100:.1f}%)")
    print(f"  • Trades Executed (Filled): {n:,}")
    print(f"  • Win Rate                : {wr:.1f}% ({len(wins)} Wins / {len(losses)} Losses)")
    print(f"  • Total Return            : {tot_r:+.2f} R")
    print(f"  • Profit Factor           : {pf:.2f}")
    print(f"  • Max Drawdown            : {max_dd_r:.2f} R")
    print(f"  • Avg Win / Avg Loss      : +{avg_win_r:.2f} R / {avg_loss_r:.2f} R (Expectancy: {tot_r/n:+.2f} R/trade)")
    print()

print("Running Baseline Backtest (Single Green Spike)...", flush=True)
t1 = time.time()
base_trades, base_spikes, _ = run_simulation(raw_candles, require_2_green=False, spike_mult=30.0)
print(f"Baseline finished in {time.time()-t1:.2f}s.", flush=True)

print("Running Improved Backtest (2 Consecutive Green Candles Gate)...", flush=True)
t2 = time.time()
imp_trades, imp_spikes, imp_skipped = run_simulation(raw_candles, require_2_green=True, spike_mult=30.0)
print(f"Improved finished in {time.time()-t2:.2f}s.\n", flush=True)

print("==================================================================================")
print("📊 1-MINUTE GRANULARITY BACKTEST RESULTS (JUNE 2026 DATASET - 15.3M BARS)")
print("==================================================================================")
print_metrics("1. BASELINE FIBVOL (Single Spike Candle)", base_trades, base_spikes)
print_metrics("2. IMPROVED FIBVOL (2 Consecutive Green Candles Gate)", imp_trades, imp_spikes, imp_skipped)

# Also test 15x, 20x, 30x volume thresholds with 2 green candles
print("==================================================================================")
print("🔬 MULTI-THRESHOLD ANALYSIS (WITH 2 CONSECUTIVE GREEN CANDLES)")
print("==================================================================================")
for vol_th in [10.0, 15.0, 20.0, 30.0]:
    t_list, s_cnt, sk_cnt = run_simulation(raw_candles, require_2_green=True, spike_mult=vol_th)
    print_metrics(f"2-Green-Candles with Volume Spike >= {vol_th:.0f}x", t_list, s_cnt, sk_cnt)
