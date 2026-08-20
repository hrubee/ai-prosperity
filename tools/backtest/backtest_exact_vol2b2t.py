import urllib.request, json, ssl, time
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pandas as pd

ctx = ssl._create_unverified_context()
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'

# Fetch top 150 liquid pairs from Bybit
req = urllib.request.Request('https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000', headers={'User-Agent': UA})
info = json.load(urllib.request.urlopen(req, timeout=10, context=ctx))
symbols = [s['symbol'] for s in info['result']['list'] if s['symbol'].endswith('USDT') and s['status'] == 'Trading'][:150]

print(f'Fetching 15m historical candles (1,000 bars per pair) for {len(symbols)} coins...')

def fetch_15m_data(sym):
    url = f'https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=15&limit=1000'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        raw = json.load(urllib.request.urlopen(req, timeout=5, context=ctx))
        klist = raw.get('result', {}).get('list', [])
        if not klist or len(klist) < 200: return None
        klist.reverse()
        return (sym, klist)
    except Exception:
        return None

with ThreadPoolExecutor(max_workers=30) as ex:
    all_raw = list(ex.map(fetch_15m_data, symbols))

dataset = [d for d in all_raw if d is not None]
print(f'Successfully loaded 15m data for {len(dataset)} pairs ({len(dataset)*1000:,} bars).\n')

def run_exact_vol2b2t(
    dataset,
    vol_thresh=20.0,
    require_green=True,
    require_second_green=True,
    confirm_vol=3.0,
    min_cum_vol=20.0,
    ema_gate=50,
    watch_bars=20,
    rr_target=3.0,
    trailing_pct=0.02
):
    trades = []
    
    for sym, klines in dataset:
        n = len(klines)
        if n < 60: continue
        
        times = [int(r[0]) for r in klines]
        opens = [float(r[1]) for r in klines]
        highs = [float(r[2]) for r in klines]
        lows = [float(r[3]) for r in klines]
        closes = [float(r[4]) for r in klines]
        vols = [float(r[5]) for r in klines]
        
        # EMA 50
        ema50 = np.zeros(n)
        alpha = 2.0 / (ema_gate + 1)
        ema50[0] = closes[0]
        for idx in range(1, n):
            ema50[idx] = alpha * closes[idx] + (1.0 - alpha) * ema50[idx-1]
            
        i = 40
        while i < n - watch_bars - 5:
            # 1. 40-SMA Volume base
            past_vols = vols[i-40:i]
            avg_vol = np.mean(past_vols)
            if avg_vol <= 0: i += 1; continue
            
            spike_idx = i
            follow_idx = i + 1
            
            mult = vols[spike_idx] / avg_vol
            if mult < vol_thresh:
                i += 1
                continue
                
            # Green Spike Check
            if require_green and closes[spike_idx] <= opens[spike_idx]:
                i += 1; continue
                
            # Second Candle Green Check
            if require_second_green and closes[follow_idx] <= opens[follow_idx]:
                i += 1; continue
                
            # Confirmation Volume
            if confirm_vol > 0 and (vols[follow_idx] / avg_vol) < confirm_vol:
                i += 1; continue
                
            # Cumulative 2-bar volume
            if min_cum_vol > 0 and ((vols[spike_idx] + vols[follow_idx]) / avg_vol) < min_cum_vol:
                i += 1; continue
                
            # EMA 50 Gate
            if ema_gate > 0 and closes[follow_idx] < ema50[follow_idx]:
                i += 1; continue
                
            prev_low = lows[follow_idx]
            
            # 2. Watch for 2B Sweep & Reclaim over the next watch_bars
            armed = False
            sweep_ext = prev_low
            reclaimed = False
            reclaim_idx = -1
            
            for w in range(follow_idx + 1, min(n, follow_idx + 1 + watch_bars)):
                w_low = lows[w]
                w_high = highs[w]
                
                # Drop below prev_low -> Armed
                if w_low < prev_low:
                    armed = True
                    sweep_ext = min(sweep_ext, w_low)
                    
                # If armed and bounces back >= prev_low -> Reclaim!
                if armed and w_high >= prev_low:
                    reclaimed = True
                    reclaim_idx = w
                    break
                    
            if not reclaimed:
                i += 1
                continue
                
            # 3. Enter Long
            entry_px = prev_low
            # Stop loss at sweep_ext, clamped between 0.3% and 2.0%
            raw_sl = sweep_ext
            max_sl = entry_px * 0.98
            min_sl = entry_px * 0.997
            sl_px = max(max_sl, min(min_sl, raw_sl))
            sl_dist = entry_px - sl_px
            if sl_dist <= 0: sl_dist = entry_px * 0.005
            
            tp_px = entry_px + (rr_target * sl_dist)
            
            # Simulate Trade Execution forward
            result = 'LOSS'
            r_pnl = -1.0
            peak_px = entry_px
            bars_held = 0
            
            for h_i in range(reclaim_idx, min(n, reclaim_idx + 60)):
                bars_held += 1
                c_high = highs[h_i]
                c_low = lows[h_i]
                
                if c_high > peak_px:
                    peak_px = c_high
                    
                # Trailing SL check (trailing by trailing_pct from peak)
                trail_sl = peak_px * (1.0 - trailing_pct)
                cur_sl = max(sl_px, trail_sl) if peak_px > entry_px * 1.015 else sl_px
                
                if c_low <= cur_sl:
                    r_pnl = (cur_sl - entry_px) / sl_dist
                    result = 'WIN' if r_pnl > 0 else 'LOSS'
                    break
                elif c_high >= tp_px:
                    result = 'WIN'
                    r_pnl = rr_target
                    break
                    
            trades.append({
                'symbol': sym.replace('USDT', ''),
                'date': time.strftime('%Y-%m-%d %H:%M', time.gmtime(times[spike_idx]/1000)),
                'spike_mult': mult,
                'entry_px': entry_px,
                'sl_px': sl_px,
                'tp_px': tp_px,
                'result': result,
                'r_pnl': r_pnl,
                'bars_held': bars_held
            })
            
            i = reclaim_idx + 5
            
    return pd.DataFrame(trades)

def print_results(df, title):
    if df.empty:
        print(f'{title}: No trades.')
        return
    wins = len(df[df['result'] == 'WIN'])
    losses = len(df[df['result'] == 'LOSS'])
    tot = len(df)
    win_rate = (wins / tot) * 100 if tot > 0 else 0
    net_r = df['r_pnl'].sum()
    gw = df[df['r_pnl'] > 0]['r_pnl'].sum()
    gl = abs(df[df['r_pnl'] < 0]['r_pnl'].sum())
    pf = gw / gl if gl > 0 else 99.0
    
    print(f'📊 {title}:')
    print(f'  • Total Trades Fired   : {tot}')
    print(f'  • Wins                 : {wins} ({win_rate:.1f}%)')
    print(f'  • Losses               : {losses} ({100-win_rate:.1f}%)')
    print(f'  • Net Return           : {net_r:+,.2f} R')
    print(f'  • Profit Factor        : {pf:.2f}')
    print(f'  • Avg Trade Duration   : {df["bars_held"].mean()*15:.1f} mins ({df["bars_held"].mean():.1f} bars)\n')

print('====================================================================================================')
print('🔬 1. EXACT LIVE VOL2B2T (VOL >= 30X, 2 GREEN BARS, EMA50, 2B SWEEP RECLAIM, 1:3.0 RR)')
print('====================================================================================================')
df1 = run_exact_vol2b2t(dataset, vol_thresh=30.0, confirm_vol=3.0, min_cum_vol=25.0, rr_target=3.0)
print_results(df1, "Live Vol2b2t Config (30x Vol Thresh)")

print('====================================================================================================')
print('🔬 2. SENSITIVE VOL2B2T (VOL >= 15X, 2 GREEN BARS, EMA50, 2B SWEEP RECLAIM, 1:3.0 RR)')
print('====================================================================================================')
df2 = run_exact_vol2b2t(dataset, vol_thresh=15.0, confirm_vol=2.5, min_cum_vol=18.0, rr_target=3.0)
print_results(df2, "Sensitive Vol2b2t (15x Vol Thresh)")

print('====================================================================================================')
print('💎 3. FIXED 1:4.0 RR VOL2B2T (VOL >= 15X, 2 GREEN BARS, EMA50, NO EARLY TRAIL)')
print('====================================================================================================')
df3 = run_exact_vol2b2t(dataset, vol_thresh=15.0, confirm_vol=2.5, min_cum_vol=18.0, rr_target=4.0, trailing_pct=0.04)
print_results(df3, "Asymmetric Vol2b2t (15x Vol, 1:4.0 RR)")
