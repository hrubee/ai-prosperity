import urllib.request, json, ssl, time
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pandas as pd

ctx = ssl._create_unverified_context()
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'

# 1. Fetch all linear USDT perpetual symbols from Bybit
req = urllib.request.Request('https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000', headers={'User-Agent': UA})
info = json.load(urllib.request.urlopen(req, timeout=10, context=ctx))
symbols = [s['symbol'] for s in info['result']['list'] if s['symbol'].endswith('USDT') and s['status'] == 'Trading']

print(f'Discovered {len(symbols)} active Linear USDT Perpetual instruments.')
print('Fetching 4-Hour historical candles (1,000 bars per pair ~ 166 days) in parallel...')

def fetch_4h_data(sym):
    url = f'https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=240&limit=1000'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        raw = json.load(urllib.request.urlopen(req, timeout=5, context=ctx))
        klist = raw.get('result', {}).get('list', [])
        if not klist or len(klist) < 100:
            return None
        # Bybit returns desc (newest first), reverse to chronological asc
        klist.reverse()
        return (sym, klist)
    except Exception:
        return None

with ThreadPoolExecutor(max_workers=30) as ex:
    all_raw = list(ex.map(fetch_4h_data, symbols))

dataset = [d for d in all_raw if d is not None]
print(f'Successfully loaded 4H datasets for {len(dataset)} pairs ({len(dataset)*1000:,} total 4H bars).\n')

def run_4h_simulation(dataset, vol_mult_thresh=5.0, require_upper_wick=False, min_wick_pct=40.0, rr_target=2.0):
    trades = []
    
    for sym, klines in dataset:
        n = len(klines)
        if n < 50:
            continue
            
        times = [int(r[0]) for r in klines]
        opens = [float(r[1]) for r in klines]
        highs = [float(r[2]) for r in klines]
        lows = [float(r[3]) for r in klines]
        closes = [float(r[4]) for r in klines]
        vols = [float(r[5]) for r in klines]
        
        # Calculate 20 SMA Volume and ATR(14)
        for i in range(25, n - 1):
            past_vols = vols[i-20:i]
            vol_ma = np.mean(past_vols)
            if vol_ma <= 0: continue
            
            vol_spike = vols[i] / vol_ma
            if vol_spike < vol_mult_thresh:
                continue
                
            # Candle range and upper wick
            c_range = highs[i] - lows[i]
            if c_range <= 0: continue
            
            upper_wick = highs[i] - max(opens[i], closes[i])
            upper_wick_ratio = (upper_wick / c_range) * 100.0
            
            if require_upper_wick and upper_wick_ratio < min_wick_pct:
                continue
                
            # Calculate ATR(14)
            tr_list = []
            for k in range(max(1, i-13), i+1):
                cur_h, cur_l, prev_c = highs[k], lows[k], closes[k-1]
                tr_list.append(max(cur_h - cur_l, abs(cur_h - prev_c), abs(cur_l - prev_c)))
            atr14 = np.mean(tr_list) if tr_list else c_range
            
            entry_px = closes[i]
            # Stop loss placed safely above spike high or 1.0x ATR
            sl_dist = max(highs[i] * 1.003 - entry_px, 1.0 * atr14)
            sl_px = entry_px + sl_dist
            tp_px = entry_px - (rr_target * sl_dist)
            
            result = 'OPEN'
            r_pnl = 0.0
            bars_held = 0
            
            # Step forward bar by bar
            for f_i in range(i + 1, n):
                bars_held += 1
                f_high = highs[f_i]
                f_low = lows[f_i]
                
                # Check SL hit
                if f_high >= sl_px:
                    result = 'LOSS'
                    r_pnl = -1.0
                    break
                # Check TP hit
                elif f_low <= tp_px:
                    result = 'WIN'
                    r_pnl = rr_target
                    break
                    
                # Max hold cap 30 bars (5 days)
                if bars_held >= 30:
                    last_c = closes[f_i]
                    r_pnl = (entry_px - last_c) / sl_dist
                    result = 'WIN' if r_pnl > 0 else 'LOSS'
                    break
                    
            trades.append({
                'symbol': sym.replace('USDT', ''),
                'date': time.strftime('%Y-%m-%d %H:%M', time.gmtime(times[i]/1000)),
                'vol_spike': vol_spike,
                'entry_px': entry_px,
                'sl_px': sl_px,
                'tp_px': tp_px,
                'wick_ratio': upper_wick_ratio,
                'result': result,
                'r_pnl': r_pnl,
                'bars_held': bars_held
            })
            
    return pd.DataFrame(trades)

def print_metrics(df, name):
    if df.empty:
        print('No trades.')
        return
    wins = len(df[df['result'] == 'WIN'])
    losses = len(df[df['result'] == 'LOSS'])
    tot = len(df)
    win_rate = (wins / tot) * 100 if tot > 0 else 0
    net_r = df['r_pnl'].sum()
    gross_win = df[df['r_pnl'] > 0]['r_pnl'].sum()
    gross_loss = abs(df[df['r_pnl'] < 0]['r_pnl'].sum())
    pf = gross_win / gross_loss if gross_loss > 0 else 99.0
    
    print(f'📊 {name}:')
    print(f'  • Total Trades Fired   : {tot:,}')
    print(f'  • Wins                 : {wins:,} ({win_rate:.1f}%)')
    print(f'  • Losses               : {losses:,} ({100-win_rate:.1f}%)')
    print(f'  • Net R-Multiple       : {net_r:+,.2f} R')
    print(f'  • Profit Factor        : {pf:.2f}')
    print(f'  • Avg Trade Duration   : {df["bars_held"].mean()*4:.1f} hours ({df["bars_held"].mean():.1f} bars)\n')

print('====================================================================================================')
print('🔬 1. BASELINE 4-HOUR DUMPRIDE (VOL >= 5.0X, 1:2.0 RR, NO WICK FILTER)')
print('====================================================================================================')
df1 = run_4h_simulation(dataset, vol_mult_thresh=5.0, require_upper_wick=False, rr_target=2.0)
print_metrics(df1, "Baseline 4H (5.0x Vol, No Wick Filter, 1:2.0 RR)")

print('====================================================================================================')
print('🔬 2. BASELINE 4-HOUR DUMPRIDE (VOL >= 8.0X, 1:2.0 RR, NO WICK FILTER)')
print('====================================================================================================')
df2 = run_4h_simulation(dataset, vol_mult_thresh=8.0, require_upper_wick=False, rr_target=2.0)
print_metrics(df2, "Baseline 4H (8.0x Vol, No Wick Filter, 1:2.0 RR)")

print('====================================================================================================')
print('💎 3. IMPROVED 4-HOUR EXHAUSTION DUMP (VOL >= 5.0X + UPPER WICK >= 40% + 1:2.0 RR)')
print('====================================================================================================')
df3 = run_4h_simulation(dataset, vol_mult_thresh=5.0, require_upper_wick=True, min_wick_pct=40.0, rr_target=2.0)
print_metrics(df3, "Improved 4H (5.0x Vol + Wick >= 40% + 1:2.0 RR)")

print('====================================================================================================')
print('🚀 4. ASYMMETRIC 4-HOUR EXHAUSTION DUMP (VOL >= 5.0X + UPPER WICK >= 40% + 1:3.0 RR)')
print('====================================================================================================')
df4 = run_4h_simulation(dataset, vol_mult_thresh=5.0, require_upper_wick=True, min_wick_pct=40.0, rr_target=3.0)
print_metrics(df4, "Asymmetric 4H (5.0x Vol + Wick >= 40% + 1:3.0 RR)")

print('====================================================================================================')
print('👑 5. SOTA PIN-BAR REJECTION 4-HOUR DUMP (VOL >= 6.0X + UPPER WICK >= 50% + 1:3.0 RR)')
print('====================================================================================================')
df5 = run_4h_simulation(dataset, vol_mult_thresh=6.0, require_upper_wick=True, min_wick_pct=50.0, rr_target=3.0)
print_metrics(df5, "SOTA 4H Pin-Bar Exhaustion (6.0x Vol + Wick >= 50% + 1:3.0 RR)")
