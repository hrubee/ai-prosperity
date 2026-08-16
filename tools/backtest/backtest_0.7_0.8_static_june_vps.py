#!/usr/bin/env python3
"""backtest_0.7_0.8_static_june_vps.py — Fast 1m backtest for FibVOL (0.7 Entry / 0.8 Static SL, NO Trailing SL).
Dataset: June 2026 Targeted Spikes Database (/root/data/june_2026_spikes_1m.db - 1,937,437 1m candles).
"""
import os, sys, sqlite3, time
import pandas as pd
import numpy as np

DB_PATH = "/root/data/june_2026_spikes_1m.db"
if not os.path.exists(DB_PATH):
    DB_PATH = "/root/data/june_2026_1m.db"

print(f"==================================================", flush=True)
print(f"📊 FIBVOL 1M BACKTEST: STATIC SL (NO TRAILING) - JUNE 2026", flush=True)
print(f"   Database: {DB_PATH}", flush=True)
print(f"   Parameters: Entry Fib = 0.700 | Static SL Fib = 0.800 | TP = 1:5.0 RR | Trailing SL = DISABLED", flush=True)
print(f"==================================================", flush=True)

start_time = time.time()
conn = sqlite3.connect(DB_PATH)

ENTRY_FIB = 0.700
SL_FIB = 0.800
RR_RATIO = 5.0
RISK_USD_PER_TRADE = 1.50 # $1.50 risk per trade (1.0% of $150 equity)
START_EQUITY = 150.0

total_spikes = 0
total_trades = 0
wins = 0
losses = 0
breakevens = 0

r_multiples = []
pnls_usd = []

equity_curve = [START_EQUITY]
current_equity = START_EQUITY

cursor = conn.cursor()
symbols = [row[0] for row in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]
print(f"Found {len(symbols)} symbols. Processing 1m candle replays with STATIC SL...", flush=True)

for i, sym in enumerate(symbols):
    try:
        df = pd.read_sql_query("SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC", conn, params=(sym,))
        if len(df) < 30: continue
        
        df['ts'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('ts', inplace=True)
        
        # Resample to 15m to detect volume spikes
        df_15m = df.resample('15min').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()
        
        if len(df_15m) < 15: continue
        
        df_15m['vol_ma'] = df_15m['volume'].rolling(20, min_periods=5).mean()
        df_15m['is_green'] = df_15m['close'] >= df_15m['open']
        df_15m['spike'] = (df_15m['volume'] >= 15.0 * df_15m['vol_ma']) & df_15m['is_green']
        
        spikes = df_15m[df_15m['spike']].copy()
        if len(spikes) == 0: continue
        
        total_spikes += len(spikes)
        
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
            
            # Slice 1m candles for 6 hours after spike
            spike_end_ts = idx + pd.Timedelta(minutes=15)
            sub_1m = df[(df.index >= spike_end_ts) & (df.index <= spike_end_ts + pd.Timedelta(hours=6))]
            if len(sub_1m) == 0: continue
            
            filled = False
            trade_r = 0.0
            
            for m_idx, m_row in sub_1m.iterrows():
                m_high = m_row['high']
                m_low = m_row['low']
                
                if not filled:
                    # Limit order check
                    if m_low <= entry_px:
                        if m_low <= sl_px:
                            break
                        filled = True
                else:
                    # ACTIVE POSITION: NO TRAILING SL! Pure Static SL at sl_px vs Take Profit at tp_px
                    if m_low <= sl_px:
                        # Static Stop Loss hit
                        trade_r = -1.0
                        break
                    elif m_high >= tp_px:
                        # Take Profit hit
                        trade_r = RR_RATIO
                        break
            
            if filled and trade_r != 0.0:
                fee_r = (0.07 / ((risk / entry_px) * 100)) if (risk / entry_px) > 0 else 0.05
                net_trade_r = trade_r - fee_r
                trade_pnl = net_trade_r * RISK_USD_PER_TRADE
                
                total_trades += 1
                r_multiples.append(net_trade_r)
                pnls_usd.append(trade_pnl)
                current_equity += trade_pnl
                equity_curve.append(current_equity)
                
                if net_trade_r > 0.05: wins += 1
                elif net_trade_r < -0.05: losses += 1
                else: breakevens += 1

    except Exception as e:
        continue

elapsed = time.time() - start_time

if total_trades == 0:
    print("No filled trades executed in backtest dataset.")
    sys.exit(0)

win_rate = (wins / total_trades * 100)
loss_rate = (losses / total_trades * 100)
be_rate = (breakevens / total_trades * 100)

avg_r = float(np.mean(r_multiples))
tot_pnl_usd = sum(pnls_usd)
tot_pnl_inr = tot_pnl_usd * 86.0

win_r_list = [r for r in r_multiples if r > 0.05]
loss_r_list = [r for r in r_multiples if r < -0.05]

avg_win_r = float(np.mean(win_r_list)) if len(win_r_list) > 0 else 0.0
avg_loss_r = float(np.mean(loss_r_list)) if len(loss_r_list) > 0 else 0.0

gross_win_r = sum(win_r_list)
gross_loss_r = abs(sum(loss_r_list))

profit_factor = (gross_win_r / gross_loss_r) if gross_loss_r > 0 else 99.9
payoff_ratio = (abs(avg_win_r / avg_loss_r)) if abs(avg_loss_r) > 0 else 0.0

eq_arr = np.array(equity_curve)
peaks = np.maximum.accumulate(eq_arr)
drawdowns = (eq_arr - peaks) / peaks * 100.0
max_drawdown = float(np.min(drawdowns))

print("\n==================================================")
print("📊 1M GRANULAR BACKTEST RESULTS (0.7 ENTRY / 0.8 STATIC SL - NO TRAILING)")
print("==================================================")
print(f"Backtest Compute Duration:    {elapsed:.2f} seconds")
print(f"Total 15m Volume Spikes:      {total_spikes:,}")
print(f"Total Limit Orders Filled:    {total_trades:,} (Fill Rate: {total_trades/total_spikes*100:.1f}%)")
print("--------------------------------------------------")
print(f"🏆 Win Rate:                  {win_rate:.1f}% ({wins} Wins)")
print(f"📉 Loss Rate:                 {loss_rate:.1f}% ({losses} Losses)")
print(f"⚖️ Breakeven Rate:            {be_rate:.1f}% ({breakevens} Breakeven)")
print("--------------------------------------------------")
print(f"🎯 AVERAGE R PER TRADE:       {avg_r:+.2f} R")
print(f"📈 AVERAGE WINNER:            {avg_win_r:+.2f} R (+${avg_win_r * RISK_USD_PER_TRADE:.2f} USD)")
print(f"📉 AVERAGE LOSER:             {avg_loss_r:+.2f} R (-${abs(avg_loss_r * RISK_USD_PER_TRADE):.2f} USD)")
print(f"⚖️ PAYOFF RATIO:              {payoff_ratio:.2f}x")
print(f"🔥 PROFIT FACTOR:             {profit_factor:.2f}")
print(f"📉 MAX DRAWDOWN:              {max_drawdown:.2f}%")
print("--------------------------------------------------")
print(f"🚀 TOTAL REALIZED NET PNL:    ${tot_pnl_usd:+.2f} USD | Rs.{tot_pnl_inr:+.2f} INR")
print(f"💰 FINAL ACCOUNT BAL:         ${current_equity:.2f} USD ({(current_equity-START_EQUITY)/START_EQUITY*100:+.1f}%)")
print("==================================================")
