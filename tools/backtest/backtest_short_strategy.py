#!/usr/bin/env python3
"""backtest_short_strategy.py — Backtests the Mean Reversion Shorting Strategy
across April, June, and July 2026 datasets.
"""
import os, sys, sqlite3, time, json
import pandas as pd
import numpy as np

DB_APRIL_JULY = "/root/data/candles_4h_april_july.db"
DB_JUNE = "/root/data/june_2026_1m.db"

# 1. Load April & July 4h Candles
print("Loading April and July 4h candles...", flush=True)
conn_aj = sqlite3.connect(DB_APRIL_JULY)
df_aj = pd.read_sql_query("SELECT symbol, timestamp, open, high, low, close, volume, month FROM candles_4h ORDER BY symbol, timestamp ASC", conn_aj)
conn_aj.close()

# 2. Load and Resample June 1m Database into 4h Candles
print("Loading and resampling June 1m database...", flush=True)
conn_j = sqlite3.connect(DB_JUNE)
cursor = conn_j.cursor()
symbols_j = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]

june_bars = []
for sym in symbols_j:
    rows = cursor.execute("SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC", (sym,)).fetchall()
    if len(rows) < 100: continue
    
    cur_bar = None
    tf_ms = 4 * 60 * 60 * 1000 # 4h in ms
    for c in rows:
        ts, o, h, l, cl, v = c
        b_ts = (ts // tf_ms) * tf_ms
        if cur_bar is None or cur_bar['ts'] != b_ts:
            if cur_bar is not None:
                june_bars.append({
                    "symbol": sym, "timestamp": cur_bar['ts'], "open": cur_bar['open'],
                    "high": cur_bar['high'], "low": cur_bar['low'], "close": cur_bar['close'],
                    "volume": cur_bar['volume'], "month": "june"
                })
            cur_bar = {'ts': b_ts, 'open': o, 'high': h, 'low': l, 'close': cl, 'volume': v}
        else:
            if h > cur_bar['high']: cur_bar['high'] = h
            if l < cur_bar['low']: cur_bar['low'] = l
            cur_bar['close'] = cl
            cur_bar['volume'] += v
    if cur_bar is not None:
        june_bars.append({
            "symbol": sym, "timestamp": cur_bar['ts'], "open": cur_bar['open'],
            "high": cur_bar['high'], "low": cur_bar['low'], "close": cur_bar['close'],
            "volume": cur_bar['volume'], "month": "june"
        })
conn_j.close()

df_j = pd.DataFrame(june_bars)
print(f"Resampled {len(df_j)} June 4h candles.", flush=True)

# Combine datasets
df = pd.concat([df_aj, df_j], ignore_index=True)
df.sort_values(by=['symbol', 'timestamp'], inplace=True)
df.reset_index(drop=True, inplace=True)

print(f"Total dataset: {len(df)} 4h candles across April, June, and July.", flush=True)

# Strategy Parameters
PUMP_WINDOW = 24  # 4 days (24 * 4h = 96h)
PUMP_THRESHOLD = 0.40  # 40% pump
EMA_PERIOD = 9
RISK_USD = 10.00
FEE_R = 0.05

trades = []

for symbol, group in df.groupby('symbol'):
    group = group.copy().reset_index(drop=True)
    if len(group) < 50: continue
    
    # Calculate 9 EMA
    group['ema_9'] = group['close'].ewm(span=EMA_PERIOD, adjust=False).mean()
    group['prev_close'] = group['close'].shift(PUMP_WINDOW)
    group['pump_pct'] = (group['close'] - group['prev_close']) / group['prev_close']
    group['is_red'] = group['close'] < group['open']
    
    in_watchlist = False
    pump_start_px = 0.0
    pump_peak_px = 0.0
    pump_peak_idx = 0
    
    idx = PUMP_WINDOW
    while idx < len(group):
        row = group.iloc[idx]
        
        if not in_watchlist:
            # Check for pump trigger
            if row['pump_pct'] >= PUMP_THRESHOLD:
                in_watchlist = True
                # Find the start of the pump
                pump_start_px = group.loc[idx - PUMP_WINDOW, 'open']
                # Search for local peak high over next 6 days (36 candles)
                search_end = min(len(group), idx + 36)
                pump_peak_idx = group.iloc[idx:search_end]['high'].idxmax()
                pump_peak_px = group.loc[pump_peak_idx, 'high']
                # Move index to peak to start scanning for entry
                idx = max(idx + 1, pump_peak_idx)
                continue
        else:
            # We are watching the coin. Wait for entry trigger:
            # First red 4h close AND close crosses below the 9 EMA
            if row['is_red'] and row['close'] < row['ema_9']:
                entry_px = row['close']
                
                # Stop Loss = Peak high + 2% buffer
                sl_px = pump_peak_px * 1.02
                risk = sl_px - entry_px
                
                # Take Profit = 50% retracement of the pump height
                tp_px = pump_peak_px - 0.50 * (pump_peak_px - pump_start_px)
                
                if risk > 0 and tp_px < entry_px:
                    # Simulate trade path (up to 15 days / 90 candles)
                    trade_sub = group.loc[idx + 1: idx + 90]
                    filled = False
                    trade_r = 0.0
                    
                    for _, t_row in trade_sub.iterrows():
                        if t_row['high'] >= sl_px:
                            # Hit SL (loss)
                            trade_r = -1.0
                            break
                        elif t_row['low'] <= tp_px:
                            # Hit TP (win)
                            trade_r = (entry_px - tp_px) / risk
                            break
                            
                    if trade_r != 0.0:
                        net_r = trade_r - FEE_R
                        pnl_usd = net_r * RISK_USD
                        trades.append({
                            "symbol": symbol,
                            "month": row['month'],
                            "entry_px": entry_px,
                            "sl_px": sl_px,
                            "tp_px": tp_px,
                            "pnl_usd": pnl_usd,
                            "r_mult": net_r
                        })
                        
                # Reset watchlist state
                in_watchlist = False
                idx = idx + 10 # cooling off period
                continue
                
        idx += 1

t_df = pd.DataFrame(trades)

print("\n==================================================================")
print("📊 MEAN REVERSION SHORTING STRATEGY BACKTEST RESULTS")
print("   Months: April, June, July 2026")
print("==================================================================")
if len(t_df) == 0:
    print("No trades executed.")
    sys.exit(0)

# Calculate summary metrics
tot = len(t_df)
wins = t_df[t_df['r_mult'] > 0]
losses = t_df[t_df['r_mult'] <= 0]
win_rate = (len(wins) / tot) * 100

g_prof = wins['pnl_usd'].sum()
g_loss = abs(losses['pnl_usd'].sum())
pf = g_prof / g_loss if g_loss > 0 else 99.9

net_pnl_usd = t_df['pnl_usd'].sum()
net_pnl_inr = net_pnl_usd * 88.5

print(f"Total Trades Executed: {tot}")
print(f"Win Rate: {win_rate:.1f}% 🟢")
print(f"Profit Factor: {pf:.2f}")
print(f"Average Return per Trade: {t_df['r_mult'].mean():+.2f}R")
print(f"Net Profit (USD): ${net_pnl_usd:,.2f}")
print(f"Net Profit (INR): ₹{net_pnl_inr:,.2f} 🚀")

print("\n📈 PERFORMANCE BY MONTH:")
for month, m_group in t_df.groupby('month'):
    m_tot = len(m_group)
    m_wins = len(m_group[m_group['r_mult'] > 0])
    m_win_rate = (m_wins / m_tot) * 100
    m_pnl_inr = m_group['pnl_usd'].sum() * 88.5
    print(f"- {month.upper():<6}: {m_tot:<4} trades | Win Rate: {m_win_rate:<5.1f}% | Net Profit: ₹{m_pnl_inr:+,.2f}")
print("==================================================================")
