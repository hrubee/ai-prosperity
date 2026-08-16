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

VOL_MULT = 7.0       
MIN_DUMP_PCT = -0.01 

START_BAL = 5000.0
RISK_PCT = 0.01      
FEE_PCT = 0.0005     

trades = []
print("Running deep backtest for Vol2b2t with 1.5R Trailing Stop...")

for i, sym in enumerate(symbols):
    df = fetch_klines(sym, "15m", 1500)
    if df is None or len(df) < 1100: continue
    
    df['avg_vol_1000'] = df['v'].shift(1).rolling(window=1000).mean()
    df['pump_pct'] = (df['c'] - df['o']) / df['o']
    df['mult'] = df['v'] / df['avg_vol_1000']
    
    spikes = df[(df['mult'] >= VOL_MULT) & (df['pump_pct'] <= MIN_DUMP_PCT)].index.tolist()
    
    last_trade_idx = 0
    for idx in spikes:
        if idx <= last_trade_idx: continue
        if idx >= len(df) - 5: continue 
        
        spike_row = df.iloc[idx]
        spike_low = spike_row['l']
        
        future_df = df.iloc[idx+1 : idx+13]
        for f_idx, f_row in future_df.iterrows():
            if f_row['l'] < spike_low and f_row['c'] > spike_low:
                entry = f_row['c']
                initial_sl = f_row['l']
                
                if initial_sl >= entry: initial_sl = entry * 0.999
                
                risk_dist = entry - initial_sl
                trail_gap = 1.5 * risk_dist # Trail by 1.5R behind the peak
                
                trade_future = df.iloc[f_idx+1 :]
                if len(trade_future) == 0: continue
                
                exit_px = 0
                exit_time = None
                reason = "Open"
                highest_px = entry
                
                for t_idx, t_row in trade_future.iterrows():
                    highest_px = max(highest_px, t_row['h'])
                    trailing_sl = highest_px - trail_gap
                    
                    actual_sl = max(initial_sl, trailing_sl)
                    
                    if t_row['l'] <= actual_sl:
                        exit_px = min(t_row['o'], actual_sl)
                        exit_time = pd.to_datetime(t_row['t'], unit='ms')
                        reason = "Trailing SL Hit" if trailing_sl > initial_sl else "Hard SL Hit"
                        break
                
                if exit_px == 0:
                    exit_px = trade_future.iloc[-1]['c']
                    exit_time = pd.to_datetime(trade_future.iloc[-1]['t'], unit='ms')
                    reason = "End of Data"
                    
                trades.append({
                    "symbol": sym,
                    "spike_time": pd.to_datetime(spike_row['t'], unit='ms'),
                    "entry_time": pd.to_datetime(f_row['t'], unit='ms'),
                    "exit_time": exit_time,
                    "entry": entry,
                    "exit": exit_px,
                    "sl": initial_sl,
                    "reason": reason
                })
                
                last_trade_idx = f_idx + 12
                break

trades = sorted(trades, key=lambda x: x['entry_time'])
equity = START_BAL
peak_equity = START_BAL
max_dd = 0.0

results_list = []
for t in trades:
    entry = t['entry']
    exit_px = t['exit']
    sl = t['sl']
    
    risk_usd = equity * RISK_PCT
    stop_dist = entry - sl
    qty = risk_usd / stop_dist
    notional = qty * entry
    
    if notional > equity * 10:
        qty = (equity * 10) / entry
        
    pnl_gross = (exit_px - entry) * qty
    fees = (entry * qty * FEE_PCT) + (exit_px * qty * FEE_PCT)
    pnl_net = pnl_gross - fees
    
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
### 📊 Vol2b2t Capitulation Backtest (Trailing Stop)
**Timeframe:** 15m
**Signal:** 7x Vol Spike (1000-bar avg) + Min 1% Red Dump
**Trigger:** Sweep below Spike Low and close above it.
**Exit Logic:** 1.5R Trailing Stop Loss
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

with open("scratch/vol2b2t_trailing_results.md", "w") as f:
    f.write(summary)
    f.write("\n\n#### Trade Log\n")
    f.write("| Entry Time | Symbol | Reason | PnL | Equity |\n")
    f.write("| --- | --- | --- | --- | --- |\n")
    for t in results_list[-30:]:
        f.write(f"| {t['entry_time']} | {t['symbol']} | {t['reason']} | ${t['pnl']:+.2f} | ${t['equity']:.2f} |\n")

print("\nSaved to scratch/vol2b2t_trailing_results.md")
