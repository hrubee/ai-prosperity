import urllib.request, json, ssl, time
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pandas as pd

ctx = ssl._create_unverified_context()
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

req = urllib.request.Request('https://fapi.binance.com/fapi/v1/exchangeInfo', headers={'User-Agent': UA})
info = json.load(urllib.request.urlopen(req, timeout=10, context=ctx))
symbols = [s['symbol'] for s in info['symbols'] if s['contractType'] == 'PERPETUAL' and s['status'] == 'TRADING' and s['quoteAsset'] == 'USDT']

print(f'Discovered {len(symbols)} active Perpetual USDT futures instruments.')
print('Fetching last 24 hours (1,440 1-minute bars) in parallel...')

def fetch_1m_data(sym):
    url = f'https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1m&limit=1440'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        raw = json.load(urllib.request.urlopen(req, timeout=5, context=ctx))
        if not raw or len(raw) < 1200:
            return None
        return (sym, raw)
    except Exception:
        return None

with ThreadPoolExecutor(max_workers=30) as ex:
    all_raw = list(ex.map(fetch_1m_data, symbols))

dataset = [d for d in all_raw if d is not None]
print(f'Successfully loaded {len(dataset)} valid 1-minute datasets ({len(dataset)*1440:,} 1m bars).\n')

def run_simulation(dataset, require_upper_wick=False):
    trades = []
    
    for sym, klines_1m in dataset:
        n_bars = len(klines_1m)
        hourly_candles = []
        
        i = 0
        while i < n_bars:
            chunk = []
            while i < n_bars and len(chunk) < 60:
                chunk.append(klines_1m[i])
                i += 1
            if len(chunk) == 60:
                h_time = int(chunk[0][0])
                h_open = float(chunk[0][1])
                h_high = max(float(c[2]) for c in chunk)
                h_low = min(float(c[3]) for c in chunk)
                h_close = float(chunk[-1][4])
                h_vol = sum(float(c[5]) for c in chunk)
                hourly_candles.append({
                    'time': h_time,
                    'open': h_open,
                    'high': h_high,
                    'low': h_low,
                    'close': h_close,
                    'vol': h_vol,
                    'end_idx_1m': i - 1
                })
                
        if len(hourly_candles) < 22:
            continue
            
        for h_idx in range(20, len(hourly_candles) - 1):
            h_bar = hourly_candles[h_idx]
            
            past_vols = [hourly_candles[k]['vol'] for k in range(h_idx-20, h_idx)]
            vol_ma20 = np.mean(past_vols)
            if vol_ma20 <= 0: continue
            
            vol_mult = h_bar['vol'] / vol_ma20
            
            if vol_mult < 10.0:
                continue
                
            h_range = h_bar['high'] - h_bar['low']
            if h_range <= 0: continue
            
            upper_wick = h_bar['high'] - max(h_bar['open'], h_bar['close'])
            upper_wick_ratio = upper_wick / h_range
            
            if require_upper_wick and upper_wick_ratio < 0.40:
                continue
                
            tr_list = []
            for k in range(max(1, h_idx-13), h_idx+1):
                cur_h = hourly_candles[k]['high']
                cur_l = hourly_candles[k]['low']
                prev_c = hourly_candles[k-1]['close']
                tr_list.append(max(cur_h - cur_l, abs(cur_h - prev_c), abs(cur_l - prev_c)))
            atr14 = np.mean(tr_list) if tr_list else h_range
            
            entry_px = h_bar['close']
            sl_dist = 1.0 * atr14
            sl_px = entry_px + sl_dist
            tp_px = entry_px - (2.0 * sl_dist)
            
            entry_1m_idx = h_bar['end_idx_1m'] + 1
            if entry_1m_idx >= len(klines_1m):
                continue
                
            result = 'OPEN'
            r_pnl = 0.0
            exit_px = entry_px
            exit_1m_idx = len(klines_1m) - 1
            
            for m_i in range(entry_1m_idx, len(klines_1m)):
                m_bar = klines_1m[m_i]
                m_high = float(m_bar[2])
                m_low = float(m_bar[3])
                
                if m_high >= sl_px:
                    result = 'LOSS'
                    r_pnl = -1.0
                    exit_px = sl_px
                    exit_1m_idx = m_i
                    break
                elif m_low <= tp_px:
                    result = 'WIN'
                    r_pnl = +2.0
                    exit_px = tp_px
                    exit_1m_idx = m_i
                    break
                    
            if result == 'OPEN':
                last_c = float(klines_1m[-1][4])
                r_pnl = (entry_px - last_c) / sl_dist
                exit_px = last_c
                
            trades.append({
                'symbol': sym.replace('USDT', ''),
                'spike_time': time.strftime('%d %b %H:%M UTC', time.gmtime(h_bar['time']/1000)),
                'spike_mult': vol_mult,
                'entry_px': entry_px,
                'sl_px': sl_px,
                'tp_px': tp_px,
                'wick_ratio': upper_wick_ratio * 100,
                'result': result,
                'r_pnl': r_pnl,
                'duration_min': exit_1m_idx - entry_1m_idx + 1
            })
            
    return pd.DataFrame(trades)

df_base = run_simulation(dataset, require_upper_wick=False)

print('========================================================================================')
print('🔬 1. BASELINE DUMPRIDE STRATEGY (LAST 24 HOURS, 1-MINUTE TICK SIMULATION)')
print('   Parameters: >=10.0x Vol Spike | 1.0x ATR Stop Loss | 1:2.0 RR Target | No Wick Filter')
print('========================================================================================')
if not df_base.empty:
    wins = len(df_base[df_base['result'] == 'WIN'])
    losses = len(df_base[df_base['result'] == 'LOSS'])
    opens = len(df_base[df_base['result'] == 'OPEN'])
    tot = len(df_base)
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
    net_r = df_base['r_pnl'].sum()
    gross_win = df_base[df_base['r_pnl'] > 0]['r_pnl'].sum()
    gross_loss = abs(df_base[df_base['r_pnl'] < 0]['r_pnl'].sum())
    pf = gross_win / gross_loss if gross_loss > 0 else 99.0
    
    print(f'Total Trades Fired   : {tot}')
    print(f'Wins (+2.0R)         : {wins} ({win_rate:.1f}%)')
    print(f'Losses (-1.0R)       : {losses} ({100-win_rate:.1f}%)')
    print(f'Still Open           : {opens}')
    print(f'Net Return           : {net_r:+,.2f} R')
    print(f'Profit Factor        : {pf:.2f}')
    print(f'Avg Trade Duration   : {df_base["duration_min"].mean():.1f} minutes\n')
    print(df_base[['symbol', 'spike_time', 'spike_mult', 'entry_px', 'sl_px', 'tp_px', 'wick_ratio', 'result', 'r_pnl', 'duration_min']].to_string(index=False))
else:
    print('No trades triggered in the last 24h.')

print('\n========================================================================================')
print('💎 2. IMPROVED DUMPRIDE STRATEGY (WITH UPPER REJECTION WICK FILTER >= 40%)')
print('   Parameters: >=10.0x Vol Spike + Upper Wick Rejection >=40% + 1:2.0 RR + 1m Granularity')
print('========================================================================================')
df_imp = run_simulation(dataset, require_upper_wick=True)

if not df_imp.empty:
    wins_i = len(df_imp[df_imp['result'] == 'WIN'])
    losses_i = len(df_imp[df_imp['result'] == 'LOSS'])
    opens_i = len(df_imp[df_imp['result'] == 'OPEN'])
    tot_i = len(df_imp)
    win_rate_i = (wins_i / (wins_i + losses_i) * 100) if (wins_i + losses_i) > 0 else 0
    net_r_i = df_imp['r_pnl'].sum()
    gw_i = df_imp[df_imp['r_pnl'] > 0]['r_pnl'].sum()
    gl_i = abs(df_imp[df_imp['r_pnl'] < 0]['r_pnl'].sum())
    pf_i = gw_i / gl_i if gl_i > 0 else 99.0
    
    print(f'Total Trades Fired   : {tot_i}')
    print(f'Wins (+2.0R)         : {wins_i} ({win_rate_i:.1f}%)')
    print(f'Losses (-1.0R)       : {losses_i} ({100-win_rate_i:.1f}%)')
    print(f'Still Open           : {opens_i}')
    print(f'Net Return           : {net_r_i:+,.2f} R')
    print(f'Profit Factor        : {pf_i:.2f}')
    print(f'Avg Trade Duration   : {df_imp["duration_min"].mean():.1f} minutes\n')
    print(df_imp[['symbol', 'spike_time', 'spike_mult', 'entry_px', 'sl_px', 'tp_px', 'wick_ratio', 'result', 'r_pnl', 'duration_min']].to_string(index=False))
else:
    print('No trades triggered with rejection wick filter.')
