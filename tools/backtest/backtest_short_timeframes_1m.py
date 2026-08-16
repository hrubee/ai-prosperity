#!/usr/bin/env python3
"""tools/backtest/backtest_short_timeframes_1m.py — Multi-Timeframe Backtest for Crypto Mean Reversion Shorting.

Evaluates the exact production shorting rules across multiple timeframe resolutions:
15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h.
Uses 1-minute execution resolution across all 15.3M candles in datasets/june_2026_1m.db.
"""
import os
import sys
import sqlite3
import time
import numpy as np

DB_PATH = os.path.expanduser("datasets/june_2026_1m.db")
if not os.path.exists(DB_PATH):
    # Try alternate location
    DB_PATH = "/root/data/june_2026_1m.db"

print(f"Loading 1-minute dataset from {DB_PATH}...")
t0 = time.time()
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]

raw_candles = {}
total_1m_bars = 0
for sym in symbols:
    rows = cursor.execute(
        "SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC",
        (sym,)
    ).fetchall()
    if len(rows) >= 500:
        raw_candles[sym] = rows
        total_1m_bars += len(rows)
conn.close()

print(f"Loaded {len(raw_candles)} active symbols ({total_1m_bars:,} 1m bars) in {time.time()-t0:.2f}s.\n")

TIMEFRAMES = {
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "6h": 360,
    "8h": 480,
    "12h": 720,
}

SPIKE_VOL_MULT = 10.0
PUMP_MIN_PCT = 5.0
VOL_SUSTAIN_MULT = 2.0
EMA_PERIOD = 9

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
            cur_bar = {
                'ts': b_ts, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': v
            }
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

def run_short_backtest_tf(tf_name, tf_min):
    all_trades = []
    
    for sym, m1_rows in raw_candles.items():
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
        
        # Precompute 9 EMA
        k = 2.0 / (EMA_PERIOD + 1)
        ema = np.zeros(n)
        ema[0] = closes[0]
        for i in range(1, n):
            ema[i] = closes[i] * k + ema[i - 1] * (1.0 - k)
            
        # Map timestamp to 1m index
        m1_times = [r[0] for r in m1_rows]
        
        watching = None
        
        for i in range(40, n):
            cur_t = times[i]
            cur_o, cur_h, cur_l, cur_c, cur_v = opens[i], highs[i], lows[i], closes[i], vols[i]
            is_green = cur_c >= cur_o
            is_red = cur_c < cur_o
            
            baseline_vol = np.mean(vols[i - 40 : i])
            vol_mult = cur_v / baseline_vol if baseline_vol > 0 else 0
            
            if watching is not None:
                # Update peak high
                if cur_h > watching['pump_peak_px']:
                    watching['pump_peak_px'] = cur_h
                    
                # Vol sustain gate on first candle after spike
                if not watching['confirmed']:
                    sustain_mult = cur_v / watching['spike_avg_vol'] if watching['spike_avg_vol'] > 0 else 0
                    if sustain_mult >= VOL_SUSTAIN_MULT:
                        watching['confirmed'] = True
                    else:
                        watching = None
                        continue
                        
                if watching is not None and watching['confirmed']:
                    # Trigger entry: red close below 9 EMA
                    if is_red and cur_c < ema[i]:
                        entry_px = cur_c
                        sl_px = watching['pump_peak_px']
                        risk = sl_px - entry_px
                        
                        if risk >= entry_px * 0.001:
                            tp_px = sl_px - 0.70 * (sl_px - watching['pump_start_px'])
                            if tp_px >= entry_px * 0.998:
                                tp_px = entry_px - 2.0 * risk # Fallback 1:2 RR
                                
                            # Simulate 1m execution starting from next 1m bar
                            trigger_close_t = cur_t + tf_min * 60 * 1000
                            # Binary search for 1m start
                            m1_start_idx = -1
                            for idx in range(len(m1_times)):
                                if m1_times[idx] >= trigger_close_t:
                                    m1_start_idx = idx
                                    break
                                    
                            if m1_start_idx != -1:
                                exit_r = None
                                exit_t = None
                                exit_reason = None
                                exit_px = None
                                
                                for m1_idx in range(m1_start_idx, len(m1_rows)):
                                    m1_t, m1_o, m1_h, m1_l, m1_c, m1_v = m1_rows[m1_idx]
                                    
                                    hit_sl = (m1_h >= sl_px)
                                    hit_tp = (m1_l <= tp_px)
                                    
                                    if hit_sl and hit_tp:
                                        # Conservative: SL hit
                                        exit_r = -1.0
                                        exit_px = sl_px
                                        exit_t = m1_t
                                        exit_reason = "SL"
                                        break
                                    elif hit_sl:
                                        exit_r = -1.0
                                        exit_px = sl_px
                                        exit_t = m1_t
                                        exit_reason = "SL"
                                        break
                                    elif hit_tp:
                                        r_win = (entry_px - tp_px) / risk
                                        exit_r = r_win
                                        exit_px = tp_px
                                        exit_t = m1_t
                                        exit_reason = "TP"
                                        break
                                        
                                if exit_r is None and m1_start_idx < len(m1_rows):
                                    # End of month close
                                    last_c = m1_rows[-1][4]
                                    exit_r = (entry_px - last_c) / risk
                                    exit_px = last_c
                                    exit_t = m1_rows[-1][0]
                                    exit_reason = "EOM"
                                    
                                if exit_r is not None:
                                    all_trades.append({
                                        "symbol": sym,
                                        "entry_t": trigger_close_t,
                                        "exit_t": exit_t,
                                        "entry_px": entry_px,
                                        "sl_px": sl_px,
                                        "tp_px": tp_px,
                                        "exit_px": exit_px,
                                        "risk": risk,
                                        "r_multiple": exit_r,
                                        "is_win": exit_r > 0,
                                        "exit_reason": exit_reason,
                                        "tf": tf_name
                                    })
                            watching = None
                            continue
                            
                    # Price retraced below pump start -> invalid
                    if cur_c < watching['pump_start_px']:
                        watching = None
                        continue
                        
            # Check for new spike
            if is_green and vol_mult >= SPIKE_VOL_MULT:
                pump_pct = (cur_c - cur_o) / cur_o * 100 if cur_o > 0 else 0
                if pump_pct >= PUMP_MIN_PCT:
                    watching = {
                        "symbol": sym,
                        "spike_t": cur_t,
                        "spike_avg_vol": baseline_vol,
                        "pump_start_px": cur_l,
                        "pump_peak_px": cur_h,
                        "confirmed": False
                    }
                    
    return all_trades

print("=" * 82)
print("📊 CRYPTO SHORTING STRATEGY: MULTI-TIMEFRAME BACKTEST (JUNE 2026 - 15.3M 1M BARS)")
print("=" * 82)

results_summary = []

for tf_name, tf_min in TIMEFRAMES.items():
    t_start = time.time()
    trades = run_short_backtest_tf(tf_name, tf_min)
    dur = time.time() - t_start
    
    n_trades = len(trades)
    if n_trades == 0:
        print(f"[{tf_name:>4}] Trades: 0")
        continue
        
    wins = [t for t in trades if t['is_win']]
    losses = [t for t in trades if not t['is_win']]
    
    wr = len(wins) / n_trades * 100.0
    tot_r = sum(t['r_multiple'] for t in trades)
    gross_win = sum(t['r_multiple'] for t in wins)
    gross_loss = abs(sum(t['r_multiple'] for t in losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else 999.0
    avg_win = (gross_win / len(wins)) if wins else 0.0
    avg_loss = (gross_loss / len(losses)) if losses else 0.0
    expectancy = tot_r / n_trades
    
    # Chronological account simulation
    trades.sort(key=lambda x: x['entry_t'])
    bal = 100.0
    peak_bal = 100.0
    max_dd = 0.0
    
    for t in trades:
        # 1% risk per trade
        r_usd = bal * 0.01
        pnl = t['r_multiple'] * r_usd
        # fees approx 0.10% notional
        bal += pnl
        if bal > peak_bal:
            peak_bal = bal
        dd = peak_bal - bal
        if dd > max_dd:
            max_dd = dd
            
    dd_pct = (max_dd / peak_bal) * 100 if peak_bal > 0 else 0
    roi = ((bal - 100.0) / 100.0) * 100.0
    
    res = {
        "tf": tf_name,
        "tf_min": tf_min,
        "trades": n_trades,
        "wr": wr,
        "tot_r": tot_r,
        "pf": pf,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
        "final_bal": bal,
        "roi": roi,
        "max_dd_usd": max_dd,
        "max_dd_pct": dd_pct,
        "dur": dur
    }
    results_summary.append(res)
    
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"⏱️ TIMEFRAME: {tf_name:>4} (Evaluated in {dur:.2f}s)")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  • Total Trades Executed : {n_trades}")
    print(f"  • Win Rate              : {wr:.1f}% ({len(wins)} Wins / {len(losses)} Losses)")
    print(f"  • Total Return (R)      : {tot_r:+.2f} R")
    print(f"  • Profit Factor         : {pf:.2f}")
    print(f"  • Avg Win / Avg Loss    : +{avg_win:.2f} R / -{avg_loss:.2f} R (Expectancy: {expectancy:+.2f} R/trade)")
    print(f"  • $100 Account (1% Risk): ${bal:.2f} ({roi:+.2f}% ROI) | Max DD: -${max_dd:.2f} ({dd_pct:.1f}%)\n")

print("=" * 82)
print("🏆 MULTI-TIMEFRAME COMPARISON TABLE")
print("=" * 82)
print(f"{'Timeframe':<10} | {'Trades':<8} | {'Win Rate':<10} | {'Profit Factor':<14} | {'Total Return':<14} | {'$100 ROI':<12} | {'Max DD %':<10}")
print("-" * 82)
for r in sorted(results_summary, key=lambda x: x['tot_r'], reverse=True):
    print(f"{r['tf']:<10} | {r['trades']:<8} | {r['wr']:>8.1f}% | {r['pf']:>14.2f} | {r['tot_r']:>+12.2f} R | {r['roi']:>+10.2f}% | {r['max_dd_pct']:>9.1f}%")
print("=" * 82)
