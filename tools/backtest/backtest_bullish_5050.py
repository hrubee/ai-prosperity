import requests, time, datetime
import pandas as pd
import numpy as np

def fetch_klines(symbol, interval="15m", limit=1500):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            df = pd.DataFrame(data, columns=["t","o","h","l","c","v","ct","qav","num","tbbav","tbqav","ignore"])
            for col in ["o","h","l","c","v"]:
                df[col] = df[col].astype(float)
            return df
    except Exception as e:
        print(f"Error {symbol}: {e}")
    return None

print("Fetching universe...")
tickers = requests.get("https://fapi.binance.com/fapi/v1/ticker/24hr").json()
symbols = [t["symbol"] for t in tickers if t["symbol"].endswith("USDT")][:150]

# Strategy Parameters
VOL_MULT = 15.0        
MIN_PUMP_PCT = 0.05    
INITIAL_SL_ATR = 2.0
TP_RR = 2.5
RUNNER_TRAIL_ATR = 5.0

# Simulation params
START_BAL = 5000.0
RISK_PCT = 0.01      
FEE_PCT = 0.0005     

trades = []
print(f"Running backtest for 50/50 Scale Out (2.5R TP / 5x ATR Runner) across 150 coins...")

for i, sym in enumerate(symbols):
    df = fetch_klines(sym, "15m", 1500)
    if df is None or len(df) < 1100: continue
    
    # Calculate True Range and ATR
    df['prev_c'] = df['c'].shift(1)
    df['tr1'] = df['h'] - df['l']
    df['tr2'] = abs(df['h'] - df['prev_c'])
    df['tr3'] = abs(df['l'] - df['prev_c'])
    df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
    df['atr'] = df['tr'].rolling(window=14).mean()
    
    df['avg_vol_1000'] = df['v'].shift(1).rolling(window=1000).mean()
    df['pump_pct'] = (df['c'] - df['o']) / df['o']
    df['mult'] = df['v'] / df['avg_vol_1000']
    
    spikes = df[(df['mult'] >= VOL_MULT) & (df['pump_pct'] >= MIN_PUMP_PCT)].index.tolist()
    
    last_trade_idx = 0
    for idx in spikes:
        if idx <= last_trade_idx: continue
        if idx >= len(df) - 5: continue 
        
        spike_row = df.iloc[idx]
        spike_high = spike_row['h']
        
        future_df = df.iloc[idx+1 : idx+21]
        
        for f_idx, f_row in future_df.iterrows():
            prev_row = df.iloc[f_idx - 1]
            
            if f_row['c'] >= spike_high:
                continue
                
            if f_row['l'] < prev_row['l'] and f_row['c'] > prev_row['l'] and f_row['c'] > f_row['o']:
                
                entry = f_row['c']
                entry_atr = f_row['atr']
                
                trade_future = df.iloc[f_idx+1 :]
                if len(trade_future) == 0: continue
                
                # Trade Setup
                initial_sl = entry - (INITIAL_SL_ATR * entry_atr)
                risk_dist = entry - initial_sl
                tp_price = entry + (TP_RR * risk_dist)
                
                tp_hit = False
                tp_exit_px = 0
                runner_exit_px = 0
                exit_time = None
                reason = "Open"
                highest_px = entry
                
                for t_idx, t_row in trade_future.iterrows():
                    highest_px = max(highest_px, t_row['h'])
                    
                    # 1. Check for Take Profit on 50%
                    if not tp_hit and t_row['h'] >= tp_price:
                        tp_hit = True
                        tp_exit_px = max(tp_price, t_row['o'])
                        # If we hit TP in the same candle as a violent dump, we assume TP hit first if close is higher than open, etc. 
                        # For backtesting simplicity, if high >= TP and low <= SL, assume we hit TP first if it was an up candle.
                    
                    # 2. Check for Trailing Stop Loss on Runner (or 100% if TP not hit)
                    trailing_dist = RUNNER_TRAIL_ATR * t_row['atr']
                    trailing_sl = highest_px - trailing_dist
                    # Enforce the initial SL as a hard floor
                    trailing_sl = max(trailing_sl, initial_sl)
                    
                    if t_row['l'] <= trailing_sl:
                        exit_px = min(t_row['o'], trailing_sl)
                        if not tp_hit:
                            # Stopped out on 100% of position
                            tp_exit_px = exit_px
                            runner_exit_px = exit_px
                            reason = "Full Stop Loss"
                        else:
                            # Stopped out on the 50% runner
                            runner_exit_px = exit_px
                            reason = f"TP Hit + Runner SL"
                            
                        exit_time = pd.to_datetime(t_row['t'], unit='ms')
                        break
                
                if runner_exit_px == 0:
                    last_c = trade_future.iloc[-1]['c']
                    runner_exit_px = last_c
                    if not tp_hit: tp_exit_px = last_c
                    exit_time = pd.to_datetime(trade_future.iloc[-1]['t'], unit='ms')
                    reason = "End of Data"
                    
                trades.append({
                    "symbol": sym,
                    "spike_time": pd.to_datetime(spike_row['t'], unit='ms'),
                    "entry_time": pd.to_datetime(f_row['t'], unit='ms'),
                    "exit_time": exit_time,
                    "entry": entry,
                    "tp_exit_px": tp_exit_px,
                    "runner_exit_px": runner_exit_px,
                    "reason": reason,
                    "entry_atr": entry_atr
                })
                
                last_trade_idx = f_idx + 20
                break

trades = sorted(trades, key=lambda x: x['entry_time'])
equity = START_BAL
peak_equity = START_BAL
max_dd = 0.0

results_list = []
for t in trades:
    entry = t['entry']
    tp_px = t['tp_exit_px']
    runner_px = t['runner_exit_px']
    entry_atr = t['entry_atr']
    
    # Position Sizing: Risk 1% of equity on the initial SL
    sl_dist = entry_atr * INITIAL_SL_ATR
    if sl_dist <= 0: sl_dist = entry * 0.01
    
    risk_usd = equity * RISK_PCT
    qty = risk_usd / sl_dist
    
    if (qty * entry) > equity * 5:
        qty = (equity * 5) / entry
        
    half_qty = qty / 2.0
    
    # PnL for Bag Securer (50%)
    pnl1 = (tp_px - entry) * half_qty
    fees1 = (entry * half_qty * FEE_PCT) + (tp_px * half_qty * FEE_PCT)
    
    # PnL for Runner (50%)
    pnl2 = (runner_px - entry) * half_qty
    fees2 = (entry * half_qty * FEE_PCT) + (runner_px * half_qty * FEE_PCT)
    
    pnl_net = (pnl1 - fees1) + (pnl2 - fees2)
    
    equity += pnl_net
    peak_equity = max(peak_equity, equity)
    dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
    max_dd = max(max_dd, dd)
    
    t['qty'] = qty
    t['pnl'] = pnl_net
    t['equity'] = equity
    results_list.append(t)

win_trades = [t for t in results_list if t['pnl'] > 0]
lose_trades = [t for t in results_list if t['pnl'] <= 0]
win_rate = len(win_trades) / len(results_list) if results_list else 0
total_return = ((equity / START_BAL) - 1) * 100

summary = f"""
### 📈 50/50 Scale-Out Backtest Results
**Timeframe:** 15m
**Signal:** 15x Volume Spike + Min 5% GREEN Pump Candle
**Trigger:** Wait for pullback. Trigger on 2b2t Reversal.
**Exit Logic:** 50% TP at +2.5R | 50% Runner on 5.0x ATR Trailing SL
**Wallet:** $5000 Start | 1% Risk | 0.05% Fees

#### Performance Metrics
* **Total Trades:** {len(results_list)}
* **Win Rate:** {win_rate*100:.1f}% ({len(win_trades)}W / {len(lose_trades)}L)
* **Starting Balance:** ${START_BAL:.2f}
* **Final Balance:** ${equity:.2f}
* **Net Profit:** ${equity - START_BAL:.2f} ({total_return:+.1f}%)
* **Max Drawdown:** -{max_dd*100:.1f}%
* **Avg Winner:** ${np.mean([t['pnl'] for t in win_trades]) if win_trades else 0:.2f}
* **Avg Loser:** ${np.mean([t['pnl'] for t in lose_trades]) if lose_trades else 0:.2f}
"""

with open("scratch/bullish_5050_results.md", "w") as f:
    f.write(summary)
    f.write("\n\n#### Trade Log\n")
    f.write("| Entry Time | Symbol | Reason | PnL | Equity |\n")
    f.write("| --- | --- | --- | --- | --- |\n")
    for t in results_list[-30:]:
        f.write(f"| {t['entry_time']} | {t['symbol']} | {t['reason']} | ${t['pnl']:+.2f} | ${t['equity']:.2f} |\n")

print("\nSaved to scratch/bullish_5050_results.md")
