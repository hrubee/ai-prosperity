#!/usr/bin/env python3
"""tools/backtest/backtest_immediate_spike_short.py — Backtest Immediate Spike-Close Short Strategy.

Strategy Logic (User's Exact Specification):
1. Detect Volume Spike: Green candle closes with Volume >= X times SMA(40) baseline volume (10x, 20x, 30x, 50x).
2. Immediate Entry: Enter SHORT immediately on the close of the spike candle.
3. Stop Loss: Entry + (1.0x, 1.5x, 2.0x) ATR(14) above entry.
4. Take Profit: Entry - (RR * Risk) for RR in [1:2, 1:3, 1:4, 1:5].
5. Intrabar 1-minute resolution on both:
   - 1-Month Historical Dataset (datasets/june_2026_1m.db)
   - Fresh 24-Hour CoinDCX Dataset (datasets/coindcx_last_24h.db)
"""
import os
import sys
import sqlite3
import time
import numpy as np
import pandas as pd

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

def backtest_dataset(db_path, db_name):
    if not os.path.exists(db_path):
        print(f"File not found: {db_path}")
        return []
        
    print(f"\n=======================================================================================")
    print(f"🚀 BACKTESTING IMMEDIATE SPIKE-CLOSE SHORT STRATEGY ON: {db_name}")
    print(f"   Database Path: {db_path}")
    print(f"=======================================================================================")
    
    conn = sqlite3.connect(db_path)
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
    
    all_summary_results = []
    
    # Test combinations
    configurations = [
        # TF, SpikeMult, ATR_Mult, RR
        ("15m", 15, 10.0, 1.0, 2.0),
        ("15m", 15, 10.0, 1.0, 3.0),
        ("15m", 15, 10.0, 1.0, 4.0),
        ("15m", 15, 10.0, 1.5, 2.0),
        ("15m", 15, 10.0, 1.5, 3.0),
        ("15m", 15, 20.0, 1.0, 2.0),
        ("15m", 15, 20.0, 1.0, 3.0),
        ("15m", 15, 30.0, 1.0, 2.0),
        ("15m", 15, 30.0, 1.0, 3.0),
        ("1h",  60, 10.0, 1.0, 2.0),
        ("1h",  60, 10.0, 1.0, 3.0),
        ("1h",  60, 20.0, 1.0, 2.0),
        ("4h", 240, 10.0, 1.0, 2.0),
        ("4h", 240, 10.0, 1.0, 3.0),
        ("4h", 240, 10.0, 1.5, 2.0),
        ("4h", 240, 20.0, 1.0, 2.0),
        ("4h", 240, 30.0, 1.0, 2.0),
    ]
    
    for tf_name, tf_min, spk_mult, atr_mult, rr in configurations:
        trades = []
        
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
            
            # ATR(14)
            tr = np.zeros(n)
            tr[0] = highs[0] - lows[0]
            for i in range(1, n):
                tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            atr14 = pd.Series(tr).rolling(14).mean().fillna(highs[0] - lows[0]).values
            
            m1_times = [r[0] for r in m1_rows]
            
            for i in range(40, n - 1):
                if closes[i] < opens[i]: # only green spike
                    continue
                    
                base_v = np.mean(vols[i - 40 : i])
                if base_v <= 0:
                    continue
                v_mult = vols[i] / base_v
                if v_mult < spk_mult:
                    continue
                    
                # Immediate Short Entry on Spike Candle Close
                entry_px = closes[i]
                cur_atr = atr14[i]
                risk = atr_mult * cur_atr
                if risk <= 0 or (risk / entry_px) < 0.002:
                    continue
                    
                sl_px = entry_px + risk
                tp_px = entry_px - rr * risk
                
                # 1-minute intrabar execution
                trigger_t = times[i] + tf_min * 60 * 1000
                m1_start = -1
                for idx in range(len(m1_times)):
                    if m1_times[idx] >= trigger_t:
                        m1_start = idx
                        break
                        
                if m1_start != -1:
                    exit_r = None
                    exit_t = None
                    exit_reason = None
                    
                    for m1_idx in range(m1_start, len(m1_rows)):
                        m1_t, m1_o, m1_h, m1_l, m1_c, m1_v = m1_rows[m1_idx]
                        
                        hit_sl = m1_h >= sl_px
                        hit_tp = m1_l <= tp_px
                        
                        if hit_sl and hit_tp:
                            exit_r = -1.0 # conservative SL on bar overlap
                            exit_reason = "SL"
                            exit_t = m1_t
                            break
                        elif hit_sl:
                            exit_r = -1.0
                            exit_reason = "SL"
                            exit_t = m1_t
                            break
                        elif hit_tp:
                            exit_r = rr
                            exit_reason = "TP"
                            exit_t = m1_t
                            break
                            
                    if exit_r is None and m1_start < len(m1_rows):
                        last_c = m1_rows[-1][4]
                        exit_r = (entry_px - last_c) / risk
                        exit_reason = "EOM"
                        exit_t = m1_rows[-1][0]
                        
                    if exit_r is not None:
                        trades.append({
                            "symbol": sym,
                            "entry_t": trigger_t,
                            "r_mult": exit_r,
                            "is_win": exit_r > 0,
                            "reason": exit_reason
                        })
                        
        n_trades = len(trades)
        if n_trades == 0:
            continue
            
        wins = [t for t in trades if t['is_win']]
        losses = [t for t in trades if not t['is_win']]
        wr = len(wins) / n_trades * 100.0
        tot_r = sum(t['r_mult'] for t in trades)
        gross_win = sum(t['r_mult'] for t in wins)
        gross_loss = abs(sum(t['r_mult'] for t in losses))
        pf = (gross_win / gross_loss) if gross_loss > 0 else 999.0
        
        # $100 account simulation
        trades.sort(key=lambda x: x['entry_t'])
        bal = 100.0
        peak = 100.0
        max_dd = 0.0
        for t in trades:
            pnl = t['r_mult'] * (bal * 0.01)
            bal += pnl
            if bal > peak: peak = bal
            dd = peak - bal
            if dd > max_dd: max_dd = dd
        dd_pct = (max_dd / peak) * 100 if peak > 0 else 0
        roi = ((bal - 100.0) / 100.0) * 100.0
        
        row_res = {
            "tf": tf_name,
            "spk_mult": spk_mult,
            "atr_mult": atr_mult,
            "rr": rr,
            "trades": n_trades,
            "wr": wr,
            "pf": pf,
            "tot_r": tot_r,
            "roi": roi,
            "max_dd_pct": dd_pct
        }
        all_summary_results.append(row_res)
        
    print(f"{'TF':<5} | {'Spike':<6} | {'SL (ATR)':<8} | {'RR Target':<9} | {'Trades':<7} | {'Win Rate':<9} | {'PF':<6} | {'Total Return':<12} | {'$100 ROI':<10} | {'Max DD %':<8}")
    print("-" * 105)
    for r in sorted(all_summary_results, key=lambda x: x['tot_r'], reverse=True):
        print(f"{r['tf']:<5} | >={r['spk_mult']:<2.0f}x  | {r['atr_mult']:<4.1f}x ATR | 1:{r['rr']:<2.0f} RR   | {r['trades']:<7} | {r['wr']:>7.1f}% | {r['pf']:>6.2f} | {r['tot_r']:>+10.2f} R | {r['roi']:>+8.2f}% | {r['max_dd_pct']:>7.1f}%")
    print("-" * 105)
    return all_summary_results

if __name__ == "__main__":
    backtest_dataset("datasets/june_2026_1m.db", "1-MONTH HISTORICAL DATASET (15.3M CANDLES)")
    backtest_dataset("datasets/coindcx_last_24h.db", "FRESH 24-HOUR COINDCX DATASET (576K CANDLES)")
