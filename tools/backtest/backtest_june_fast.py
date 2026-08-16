#!/usr/bin/env python3
"""backtest_june_fast.py — Ultra-Fast In-Memory 1m Granularity June Backtest with New Trailing SL Setup.
Database: /root/data/june_2026_1m.db
"""
import os, sys, sqlite3, time
import pandas as pd
import numpy as np
from concurrent.futures import ProcessPoolExecutor

DB_PATH = "/root/data/june_2026_1m.db"

def process_symbol_data(args):
    sym, df_raw = args
    try:
        if len(df_raw) < 500: return None
        
        df = df_raw.copy()
        df['ts'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('ts', inplace=True)
        
        df_15m = df.resample('15min').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
        }).dropna()
        
        if len(df_15m) < 40: return None
        
        df_15m['vol_ma'] = df_15m['volume'].rolling(40, min_periods=20).mean()
        df_15m['is_green'] = df_15m['close'] >= df_15m['open']
        df_15m['vol_mult'] = df_15m['volume'] / df_15m['vol_ma']
        
        spikes = df_15m[df_15m['is_green'] & (df_15m['vol_mult'] >= 30.0)]
        if len(spikes) == 0: return None
        
        trades_by_cfg = {0: [], 1: [], 2: [], 3: []}
        configs = [
            {"tp_rr": 5.0, "act_r": 2.0, "trail_r": 1.0},
            {"tp_rr": 99.0, "act_r": 1.0, "trail_r": 2.0},
            {"tp_rr": 99.0, "act_r": 1.0, "trail_r": 3.0},
            {"tp_rr": 99.0, "act_r": 1.0, "trail_r": 4.0},
        ]
        
        for idx, srow in spikes.iterrows():
            high_px, low_px = srow['high'], srow['low']
            rng = high_px - low_px
            if rng <= 0: continue
            
            entry_px = high_px - (0.700 * rng)
            sl_px = high_px - (0.800 * rng)
            risk = entry_px - sl_px
            if risk <= 0: continue
            
            spike_end_ts = idx + pd.Timedelta(minutes=15)
            sub = df[(df.index >= spike_end_ts) & (df.index <= spike_end_ts + pd.Timedelta(hours=12))]
            if len(sub) == 0: continue
            
            for cfg_idx, cfg in enumerate(configs):
                act_r = cfg["act_r"]
                trail_r = cfg["trail_r"]
                tp_rr = cfg["tp_rr"]
                tp_px = entry_px + (tp_rr * risk)
                
                filled = False
                trade_r = 0.0
                peak_px = entry_px
                cur_sl = sl_px
                
                for m_idx, m_row in sub.iterrows():
                    m_high, m_low = m_row['high'], m_row['low']
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
                            if peak_r >= act_r:
                                cur_sl = max(cur_sl, peak_px - (trail_r * risk))
                                
                        if m_low <= cur_sl:
                            trade_r = (cur_sl - entry_px) / risk
                            break
                        elif m_high >= tp_px:
                            trade_r = tp_rr
                            break
                            
                if filled and trade_r != 0.0:
                    net_r = trade_r - 0.05
                    trades_by_cfg[cfg_idx].append(net_r)
                    
        return trades_by_cfg
    except Exception as e:
        return None

def main():
    t0 = time.time()
    print("Loading full June 1m SQLite database into RAM...", flush=True)
    conn = sqlite3.connect(DB_PATH)
    df_all = pd.read_sql_query("SELECT symbol, timestamp, open, high, low, close, volume FROM klines_1m", conn)
    conn.close()
    
    print(f"Loaded {len(df_all):,} 1m candles in {time.time()-t0:.2f}s! Grouping by symbol...", flush=True)
    grouped = list(df_all.groupby("symbol"))
    
    print(f"Parallelizing backtest across {len(grouped)} symbols...", flush=True)
    st = time.time()
    
    combined = {0: [], 1: [], 2: [], 3: []}
    
    with ProcessPoolExecutor(max_workers=8) as executor:
        results = executor.map(process_symbol_data, grouped)
        for res in results:
            if res:
                for cfg_idx in range(4):
                    combined[cfg_idx].extend(res[cfg_idx])
                    
    configs_meta = [
        "Old Setup (Static 5R TP, +2.0R Act / 1.0R Trail)",
        "NEW SETUP (Uncapped TP, +1.0R Act / 2.0R Trail)",
        "NEW SETUP (Uncapped TP, +1.0R Act / 3.0R Trail)",
        "NEW SETUP (Uncapped TP, +1.0R Act / 4.0R Trail)",
    ]
    
    RISK_USD = 10.00
    
    print("\n==================================================================", flush=True)
    print("📊 INDEPENDENT JUNE 2026 CONTINUOUS 1M BACKTEST RESULTS SUMMARY", flush=True)
    print("==================================================================", flush=True)
    print(f"{'Setup / Configuration':<46} | {'Trades':<7} | {'Win Rate':<9} | {'Avg R':<8} | {'PF':<6} | {'Net PnL ($)':<12} | {'Net PnL (₹)':<14}")
    print("-" * 115)
    
    for cfg_idx in range(4):
        r_list = combined[cfg_idx]
        tot_trades = len(r_list)
        if tot_trades == 0: continue
        
        wins = [r for r in r_list if r > 0]
        losses = [r for r in r_list if r <= 0]
        wr = len(wins) / tot_trades * 100
        avg_r = float(np.mean(r_list))
        tot_pnl_usd = sum(r_list) * RISK_USD
        tot_pnl_inr = tot_pnl_usd * 88.5
        win_sum = sum(wins)
        loss_sum = abs(sum(losses))
        pf = (win_sum / loss_sum) if loss_sum > 0 else 99.9
        
        name = configs_meta[cfg_idx]
        print(f"{name:<46} | {tot_trades:>7} | {wr:>7.1f}% | {avg_r:>+6.2f}R | {pf:>6.2f} | ${tot_pnl_usd:>+10.2f} | ₹{tot_pnl_inr:>+12.2f}")
        
    print("==================================================================", flush=True)
    print(f"Completed full June backtest in {time.time()-t0:.2f} seconds!", flush=True)

if __name__ == "__main__":
    main()
