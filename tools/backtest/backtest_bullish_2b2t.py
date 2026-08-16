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
ATR_MULT = 5.0         

# Simulation params
START_BAL = 5000.0
RISK_PCT = 0.01      
FEE_PCT = 0.0005     

trades = []
print(f"Running deep backtest for Bullish Continuation 2b2t (Trailing SL {ATR_MULT}x ATR) across 150 coins...")

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
                
                exit_px = 0
                exit_time = None
                reason = "Open"
                highest_px = entry
                
                for t_idx, t_row in trade_future.iterrows():
                    highest_px = max(highest_px, t_row['h'])
                    
                    # Adaptive 2x ATR Trailing Stop Loss
                    trailing_dist = ATR_MULT * t_row['atr']
                    trailing_sl = highest_px - trailing_dist
                    
                    if t_row['l'] <= trailing_sl:
                        exit_px = min(t_row['o'], trailing_sl)
                        exit_time = pd.to_datetime(t_row['t'], unit='ms')
                        reason = f"Trailing SL Hit ({ATR_MULT}x ATR)"
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
    exit_px = t['exit']
    entry_atr = t['entry_atr']
    
    # Position Sizing: Risk 1% of equity if 2x ATR SL is hit from entry
    sl_dist = entry_atr * ATR_MULT
    if sl_dist <= 0: sl_dist = entry * 0.01
    
    risk_usd = equity * RISK_PCT
    qty = risk_usd / sl_dist
    
    # Cap maximum leverage at 5x to avoid liquidation risk on gap downs
    notional = qty * entry
    if notional > equity * 5:
        qty = (equity * 5) / entry
        
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
### 📈 Bullish Continuation 2b2t Backtest Results
**Timeframe:** 15m
**Signal:** 15x Volume Spike + Min 5% GREEN Pump Candle
**Trigger:** Wait for pullback. Trigger on 2b2t Reversal (Sweep prev low & close green).
**Exit Logic:** Adaptive {ATR_MULT}x ATR Trailing Stop Loss
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

with open("scratch/bullish_2b2t_results.md", "w") as f:
    f.write(summary)
    f.write("\n\n#### Trade Log\n")
    f.write("| Entry Time | Symbol | Reason | PnL | Equity |\n")
    f.write("| --- | --- | --- | --- | --- |\n")
    for t in results_list[-30:]:
        f.write(f"| {t['entry_time']} | {t['symbol']} | {t['reason']} | ${t['pnl']:+.2f} | ${t['equity']:.2f} |\n")

print("\nSaved to scratch/bullish_2b2t_results.md")
