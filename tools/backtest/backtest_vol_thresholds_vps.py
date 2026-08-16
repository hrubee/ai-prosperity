#!/usr/bin/env python3
"""backtest_vol_thresholds_vps.py — Fast 1m backtest comparing Volume Spike Multipliers.
Tests 15x, 20x, 25x, 30x, 40x, 50x Volume Multipliers on June 2026 1m dataset.
Setup: 0.70 Entry / 0.80 SL | 1:5.0 RR | Dynamic Trailing SL active at +2R.
"""
import os, sys, sqlite3, time
import pandas as pd
import numpy as np

DB_PATH = "/root/data/june_2026_1m.db"
if not os.path.exists(DB_PATH):
    DB_PATH = "/root/data/june_2026_spikes_1m.db"

print(f"==================================================", flush=True)
print(f"📊 FIBVOL 1M BACKTEST: VOLUME MULTIPLIER OPTIMIZATION", flush=True)
print(f"   Database: {DB_PATH}", flush=True)
print(f"   Setup: Entry Fib = 0.700 | SL Fib = 0.800 | RR = 1:5.0 | Trailing SL = +2R", flush=True)
print(f"==================================================", flush=True)

start_time = time.time()
conn = sqlite3.connect(DB_PATH)

ENTRY_FIB = 0.700
SL_FIB = 0.800
RR_RATIO = 5.0
RISK_USD_PER_TRADE = 1.50 # $1.50 risk per trade (1.0% of $150 equity)
START_EQUITY = 150.0

VOL_MULTIPLIERS = [15.0, 20.0, 25.0, 30.0, 40.0, 50.0]

print("Reading symbols from database...", flush=True)
cursor = conn.cursor()
symbols = [row[0] for row in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]
print(f"Found {len(symbols)} symbols. Pre-loading OHLCV into memory...", flush=True)

sym_dfs = {}
for sym in symbols:
    try:
        df = pd.read_sql_query("SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC", conn, params=(sym,))
        if len(df) < 50: continue
        df['ts'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('ts', inplace=True)
        sym_dfs[sym] = df
    except Exception:
        continue

print(f"Loaded {len(sym_dfs)} valid symbols into memory in {time.time() - start_time:.2f}s", flush=True)

def run_vol_backtest(vol_mult):
    tot_spikes = 0
    tot_trades = 0
    wins = 0
    losses = 0
    r_multiples = []
    pnls_usd = []
    
    for sym, df in sym_dfs.items():
        df_15m = df.resample('15min').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()
        
        if len(df_15m) < 40: continue
        
        df_15m['vol_ma'] = df_15m['volume'].rolling(40, min_periods=20).mean()
        df_15m['is_green'] = df_15m['close'] >= df_15m['open']
        df_15m['spike'] = (df_15m['volume'] >= vol_mult * df_15m['vol_ma']) & df_15m['is_green']
        
        spikes = df_15m[df_15m['spike']].copy()
        if len(spikes) == 0: continue
        
        tot_spikes += len(spikes)
        
        for idx, srow in spikes.iterrows():
            high_px = srow['high']
            low_px = srow['low']
            rng = high_px - low_px
            if rng <= 0: continue
            
            entry_px = high_px - (ENTRY_FIB * rng)
            sl_px = high_px - (SL_FIB * rng)
            risk = entry_px - sl_px
            if risk <= 0: continue
            
            tp_px = entry_px + (RR_RATIO * risk)
            
            spike_end_ts = idx + pd.Timedelta(minutes=15)
            sub_1m = df[(df.index >= spike_end_ts) & (df.index <= spike_end_ts + pd.Timedelta(hours=6))]
            if len(sub_1m) == 0: continue
            
            filled = False
            trade_r = 0.0
            peak_px = entry_px
            cur_sl = sl_px
            
            for m_idx, m_row in sub_1m.iterrows():
                m_high = m_row['high']
                m_low = m_row['low']
                
                if not filled:
                    if m_low <= entry_px:
                        if m_low <= sl_px: break
                        filled = True
                        peak_px = entry_px
                        cur_sl = sl_px
                else:
                    if m_high > peak_px:
                        peak_px = m_high
                        peak_r = (peak_px - entry_px) / risk
                        if peak_r >= 2.0:
                            cur_sl = max(cur_sl, peak_px - (1.0 * risk))
                            
                    if m_low <= cur_sl:
                        trade_r = (cur_sl - entry_px) / risk
                        break
                    elif m_high >= tp_px:
                        trade_r = RR_RATIO
                        break
            
            if filled and trade_r != 0.0:
                fee_r = (0.07 / ((risk / entry_px) * 100)) if (risk / entry_px) > 0 else 0.05
                net_trade_r = trade_r - fee_r
                trade_pnl = net_trade_r * RISK_USD_PER_TRADE
                
                tot_trades += 1
                r_multiples.append(net_trade_r)
                pnls_usd.append(trade_pnl)
                
                if net_trade_r > 0: wins += 1
                else: losses += 1
                
    if tot_trades == 0:
        return {"mult": vol_mult, "spikes": tot_spikes, "trades": 0, "win_rate": 0.0, "avg_r": 0.0, "pf": 0.0, "pnl": 0.0}
        
    win_rate = (wins / tot_trades * 100)
    avg_r = float(np.mean(r_multiples))
    tot_pnl = sum(pnls_usd)
    
    win_r_sum = sum(r for r in r_multiples if r > 0)
    loss_r_sum = abs(sum(r for r in r_multiples if r < 0))
    pf = (win_r_sum / loss_r_sum) if loss_r_sum > 0 else 99.9
    
    return {"mult": vol_mult, "spikes": tot_spikes, "trades": tot_trades, "win_rate": win_rate, "avg_r": avg_r, "pf": pf, "pnl": tot_pnl}

results = []
print("\nRunning backtest across volume multipliers...", flush=True)
for v in VOL_MULTIPLIERS:
    t0 = time.time()
    res = run_vol_backtest(v)
    results.append(res)
    print(f"  [Done] {v:>4.1f}x Vol Spike: {res['spikes']:>4} Spikes -> {res['trades']:>3} Filled Trades | WR: {res['win_rate']:>5.1f}% | Avg R: {res['avg_r']:>+5.2f}R | PF: {res['pf']:>4.2f} | PnL: ${res['pnl']:>+6.2f} ({time.time()-t0:.1f}s)", flush=True)

print("\n==================================================")
print("📊 FINAL VOLUME MULTIPLIER COMPARISON (June 2026 1m Data)")
print("==================================================")
print(f"{'Multiplier':<12} | {'Spikes':<8} | {'Trades':<8} | {'Win Rate':<10} | {'Avg R':<10} | {'PF':<6} | {'Net PnL ($)':<12}")
print("-" * 75)
for r in results:
    print(f"{r['mult']:>5.1f}x Vol   | {r['spikes']:>8} | {r['trades']:>8} | {r['win_rate']:>8.1f}% | {r['avg_r']:>+8.2f}R | {r['pf']:>6.2f} | ${r['pnl']:>+10.2f}")
print("==================================================")
