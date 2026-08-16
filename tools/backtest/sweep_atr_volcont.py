import requests, time
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

VOL_MULT = 10.0        
MIN_PUMP_PCT = 0.015    
SUSTAIN_VOL_PCT = 0.25

START_BAL = 5000.0
RISK_PCT = 0.01      
FEE_PCT = 0.0005     

print("Pre-calculating signals for 150 coins...")
all_signals = []

for i, sym in enumerate(symbols):
    df = fetch_klines(sym, "15m", 1500)
    if df is None or len(df) < 1100: continue
    
    df['prev_c'] = df['c'].shift(1)
    df['tr1'] = df['h'] - df['l']
    df['tr2'] = abs(df['h'] - df['prev_c'])
    df['tr3'] = abs(df['l'] - df['prev_c'])
    df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
    df['atr'] = df['tr'].rolling(window=14).mean()
    
    # Calculate avg vol of previous 20 candles
    df['avg_vol_20'] = df['v'].shift(1).rolling(window=20).mean()
    df['pump_pct'] = (df['c'] - df['o']) / df['o']
    df['mult'] = df['v'] / df['avg_vol_20']
    
    # Condition 1: Spike
    spikes = df[(df['mult'] >= VOL_MULT) & (df['pump_pct'] >= MIN_PUMP_PCT)].index.tolist()
    
    last_trade_idx = 0
    for idx in spikes:
        if idx <= last_trade_idx: continue
        if idx >= len(df) - 2: continue 
        
        spike_vol = df.iloc[idx]['v']
        
        # Condition 2: Continuation (Next Candle)
        next_idx = idx + 1
        cont_row = df.iloc[next_idx]
        cont_vol = cont_row['v']
        
        sustain_ratio = cont_vol / spike_vol
        
        if sustain_ratio >= SUSTAIN_VOL_PCT:
            trade_future = df.iloc[next_idx+1 :]
            if len(trade_future) > 0:
                all_signals.append({
                    "symbol": sym,
                    "entry_time": pd.to_datetime(cont_row['t'], unit='ms'),
                    "entry": cont_row['c'],
                    "entry_atr": cont_row['atr'],
                    "future_df": trade_future
                })
            last_trade_idx = next_idx + 20

print(f"Found {len(all_signals)} valid Volume Continuation entry signals across the universe. Sweeping ATR parameters...")

results = []
atr_multipliers = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 7.0, 8.0, 10.0]

for atr_mult in atr_multipliers:
    trades = []
    
    for sig in all_signals:
        entry = sig['entry']
        entry_atr = sig['entry_atr']
        trade_future = sig['future_df']
        
        highest_px = entry
        exit_px = 0
        
        for t_idx, t_row in trade_future.iterrows():
            highest_px = max(highest_px, t_row['h'])
            trailing_sl = highest_px - (atr_mult * t_row['atr'])
            
            if t_row['l'] <= trailing_sl:
                exit_px = min(t_row['o'], trailing_sl)
                break
                
        if exit_px == 0:
            exit_px = trade_future.iloc[-1]['c']
            
        trades.append({
            "entry_time": sig['entry_time'],
            "entry": entry,
            "exit_px": exit_px,
            "entry_atr": entry_atr
        })
        
    trades = sorted(trades, key=lambda x: x['entry_time'])
    equity = START_BAL
    peak_equity = START_BAL
    max_dd = 0.0
    
    for t in trades:
        entry = t['entry']
        exit_px = t['exit_px']
        
        sl_dist = t['entry_atr'] * atr_mult
        if sl_dist <= 0: sl_dist = entry * 0.01
        
        qty = (equity * RISK_PCT) / sl_dist
        if (qty * entry) > equity * 5: qty = (equity * 5) / entry
            
        pnl = (exit_px - entry) * qty
        fees = (entry * qty * FEE_PCT) + (exit_px * qty * FEE_PCT)
        net_pnl = pnl - fees
        
        equity += net_pnl
        peak_equity = max(peak_equity, equity)
        dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
        max_dd = max(max_dd, dd)
        
        t['net_pnl'] = net_pnl
        
    win_trades = [t for t in trades if t['net_pnl'] > 0]
    win_rate = len(win_trades) / len(trades) if trades else 0
    net_profit_pct = ((equity / START_BAL) - 1) * 100
    
    results.append({
        "ATR": f"{atr_mult}x",
        "WinRate": f"{win_rate*100:.1f}%",
        "NetProfit": f"{net_profit_pct:+.1f}%",
        "MaxDD": f"-{max_dd*100:.1f}%",
        "AvgWin": f"${np.mean([t['net_pnl'] for t in win_trades]) if win_trades else 0:.2f}",
        "AvgLoss": f"${np.mean([t['net_pnl'] for t in trades if t['net_pnl'] <= 0]) if len(trades)-len(win_trades)>0 else 0:.2f}"
    })

# Format Markdown Table
summary = "### 📈 Trailing Stop ATR Sweep (Volume Continuation)\n\n"
summary += "| ATR Mult | Win Rate | Net Profit | Max Drawdown | Avg Win | Avg Loss |\n"
summary += "| --- | --- | --- | --- | --- | --- |\n"

for r in results:
    summary += f"| {r['ATR']} | {r['WinRate']} | **{r['NetProfit']}** | {r['MaxDD']} | {r['AvgWin']} | {r['AvgLoss']} |\n"

with open("scratch/atr_sweep_volcont_results.md", "w") as f:
    f.write(summary)

print("\nSweep Complete! Results saved to scratch/atr_sweep_volcont_results.md")
