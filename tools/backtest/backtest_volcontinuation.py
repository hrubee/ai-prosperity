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

def calculate_atr(highs, lows, closes, period=14):
    tr1 = highs - lows
    tr2 = (highs - closes.shift(1)).abs()
    tr3 = (lows - closes.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

print("Fetching universe...")
tickers = requests.get("https://fapi.binance.com/fapi/v1/ticker/24hr").json()
symbols = [t["symbol"] for t in tickers if t["symbol"].endswith("USDT")][:150]

VOL_MULT = 20.0
PUMP_PCT = 0.025
SUSTAIN = 1.0
ATR_MULT = 3.0

# Simulation params
START_BAL = 5000.0
RISK_PCT = 0.01 # 1% risk per trade
FEE_PCT = 0.0005 # 0.05% per side

trades = []

print("Running deep backtest across 150 coins...")

for i, sym in enumerate(symbols):
    df = fetch_klines(sym, "15m", 1500)
    if df is None or len(df) < 1100: continue
    
    df['avg_vol_1000'] = df['v'].shift(1).rolling(window=1000).mean()
    df['pump_pct'] = (df['c'] - df['o']) / df['o']
    df['mult'] = df['v'] / df['avg_vol_1000']
    df['atr'] = calculate_atr(df['h'], df['l'], df['c'], 14)
    
    spikes = df[(df['mult'] >= VOL_MULT) & (df['pump_pct'] >= PUMP_PCT)].index.tolist()
    
    for idx in spikes:
        if idx >= len(df) - 2: continue 
        
        spike_row = df.iloc[idx]
        next_row = df.iloc[idx+1]
        
        sustain_ratio = next_row['v'] / spike_row['v']
        if sustain_ratio >= SUSTAIN:
            
            entry = next_row['c']
            sl = spike_row['l']
            atr = spike_row['atr']
            
            # SL cannot be greater than entry
            if sl >= entry: sl = entry * 0.999
            
            trail_gap = atr * ATR_MULT
            
            # Simulate the trade on the future bars
            future_df = df.iloc[idx+2 :]
            if len(future_df) == 0: continue
            
            highest_px = entry
            exit_px = 0
            exit_time = None
            reason = "Open"
            
            for f_idx, r in future_df.iterrows():
                highest_px = max(highest_px, r['h'])
                trailing_sl = highest_px - trail_gap
                actual_sl = max(sl, trailing_sl)
                
                # Did we hit SL during this bar?
                if r['l'] <= actual_sl:
                    # We slipped out at actual_sl or the open of a nasty gap down
                    exit_px = min(r['o'], actual_sl)
                    exit_time = pd.to_datetime(r['t'], unit='ms')
                    reason = "Trailing SL Hit" if trailing_sl > sl else "Hard SL Hit"
                    break
            
            # If never hit, force close at end
            if exit_px == 0:
                exit_px = future_df.iloc[-1]['c']
                exit_time = pd.to_datetime(future_df.iloc[-1]['t'], unit='ms')
                reason = "End of Data"
                
            trades.append({
                "symbol": sym,
                "entry_time": pd.to_datetime(next_row['t'], unit='ms'),
                "exit_time": exit_time,
                "entry": entry,
                "exit": exit_px,
                "sl": sl,
                "reason": reason
            })

# Sort trades chronologically by entry_time to accurately simulate the wallet
trades = sorted(trades, key=lambda x: x['entry_time'])

equity = START_BAL
peak_equity = START_BAL
max_dd = 0.0

print(f"\n--- Trade Simulation (Start: ${START_BAL}, Risk: {RISK_PCT*100}%) ---")

results_list = []
for t in trades:
    entry = t['entry']
    exit_px = t['exit']
    sl = t['sl']
    
    # Calculate position size based on 1% risk of CURRENT equity
    risk_usd = equity * RISK_PCT
    stop_dist = entry - sl
    if stop_dist <= 0: continue
    
    qty = risk_usd / stop_dist
    notional = qty * entry
    
    # Cap leverage if necessary (assume max 10x)
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
    t['notional'] = notional
    t['pnl'] = pnl_net
    t['equity'] = equity
    
    results_list.append(t)
    print(f"{t['entry_time']} | {t['symbol']} | {t['reason']} | PnL: ${pnl_net:+.2f} | Eq: ${equity:.2f}")

win_trades = [t for t in results_list if t['pnl'] > 0]
lose_trades = [t for t in results_list if t['pnl'] <= 0]
win_rate = len(win_trades) / len(results_list) if results_list else 0

total_return = ((equity / START_BAL) - 1) * 100

summary = f"""
### 📊 VolContinuation Backtest Results
**Timeframe:** 15m
**Lookback Baseline:** 1000 Bars (10.4 Days)
**Signal:** 20x Volume Spike + 2.5% Pump + 70% Continuation Sustain
**Trailing Stop:** 3x ATR
**Wallet / Risk:** $5000 Start | 1% Risk per Trade | 0.05% Fees per side

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

with open("scratch/backtest_results.md", "w") as f:
    f.write(summary)
    
    f.write("\n\n#### Trade Log\n")
    f.write("| Entry Time | Symbol | Reason | PnL | Equity |\n")
    f.write("| --- | --- | --- | --- | --- |\n")
    for t in results_list:
        f.write(f"| {t['entry_time']} | {t['symbol']} | {t['reason']} | ${t['pnl']:+.2f} | ${t['equity']:.2f} |\n")

print("\nSaved to scratch/backtest_results.md")
