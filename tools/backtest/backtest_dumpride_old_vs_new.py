import urllib.request
import json
import time
import os
import ssl
import numpy as np
from concurrent.futures import ThreadPoolExecutor

ssl_ctx = ssl._create_unverified_context()

print("=========================================================================================")
print("🧪 BACKTEST EXPERIMENT: DUMPRIDE 1H EXHAUSTION SHORT (OLD VS NEW INSTITUTIONAL SETTINGS)")
print("=========================================================================================")

UNIVERSE = [
    "BTC", "ETH", "SOL", "DOGE", "NEAR", "AVAX", "SUI", "APT", "INJ", 
    "RENDER", "FET", "LINK", "ARB", "OP", "PEPE", "WIF", "SHIB", "GALA",
    "MATIC", "LDO", "TIA", "SEI", "FTM", "RUNE", "AAVE", "UNI", "FIL",
    "1000PEPE", "1000SHIB", "1000BONK", "1000FLOKI", "NOT", "PEOPLE",
    "JASMY", "ORDI", "KAS", "SATS", "STX", "BOME", "MEME", "WLD"
]

def fetch_single(sym):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}USDT&interval=1h&limit=1000"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        rows = json.load(urllib.request.urlopen(req, context=ssl_ctx, timeout=8))
        if isinstance(rows, list) and len(rows) > 50 and isinstance(rows[0], list):
            return sym, rows
    except Exception:
        pass
    return sym, None

print(f"📡 Pre-fetching 1,000 hourly candles across {len(UNIVERSE)} symbols via parallel threads...")
t0 = time.time()
CACHED_DATA = {}
with ThreadPoolExecutor(max_workers=20) as pool:
    results = pool.map(fetch_single, UNIVERSE)
    for sym, data in results:
        if data:
            CACHED_DATA[sym] = data

print(f"✅ Successfully cached {len(CACHED_DATA)} perpetual futures pairs in {time.time()-t0:.1f}s!\n")

def run_backtest_cached(min_pump_pct=3.0, min_vol_mult=3.0, min_candle_notional=250000.0, min_base_notional=35000.0, rr_target=2.0, sl_atr_mult=1.0):
    all_trades = []
    
    for sym, klines in CACHED_DATA.items():
        times = [int(r[0]) for r in klines]
        opens = np.array([float(r[1]) for r in klines])
        highs = np.array([float(r[2]) for r in klines])
        lows = np.array([float(r[3]) for r in klines])
        closes = np.array([float(r[4]) for r in klines])
        vols = np.array([float(r[5]) for r in klines])
        quote_vols = np.array([float(r[7]) for r in klines])
        
        n = len(klines)
        
        # ATR(14)
        tr = np.zeros(n)
        tr[0] = highs[0] - lows[0]
        for i in range(1, n):
            tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            
        atr = np.zeros(n)
        for i in range(13, n):
            atr[i] = np.mean(tr[i-13:i+1])
            
        i = 25
        while i < n - 24:
            c_open = opens[i]
            c_close = closes[i]
            c_high = highs[i]
            c_low = lows[i]
            c_vol = vols[i]
            c_usdt_vol = quote_vols[i]
            c_atr = atr[i]
            
            # Green candle check
            if c_close <= c_open:
                i += 1
                continue
                
            pump_pct = ((c_close - c_open) / c_open) * 100.0
            if pump_pct < min_pump_pct:
                i += 1
                continue
                
            base_vol = np.mean(vols[i-20:i])
            base_usdt_vol = np.mean(quote_vols[i-20:i])
            if base_vol <= 0 or base_usdt_vol < min_base_notional:
                i += 1
                continue
                
            vol_mult = c_vol / base_vol
            if vol_mult < min_vol_mult:
                i += 1
                continue
                
            if c_usdt_vol < min_candle_notional:
                i += 1
                continue
                
            entry_idx = i + 1
            entry_px = opens[entry_idx]
            risk_dist = sl_atr_mult * c_atr
            if risk_dist <= 0:
                i += 1
                continue
                
            sl_px = entry_px + risk_dist
            tp_px = entry_px - (rr_target * risk_dist)
            
            outcome = None
            bars_held = 0
            for f in range(entry_idx, min(n, entry_idx + 48)):
                f_high = highs[f]
                f_low = lows[f]
                bars_held += 1
                
                sl_hit = (f_high >= sl_px)
                tp_hit = (f_low <= tp_px)
                
                if sl_hit and tp_hit:
                    outcome = "LOSS"
                    break
                elif sl_hit:
                    outcome = "LOSS"
                    break
                elif tp_hit:
                    outcome = "WIN"
                    break
                    
            if outcome is None:
                exit_px = closes[min(n-1, entry_idx + 47)]
                pnl_r = (entry_px - exit_px) / risk_dist
                outcome = "WIN" if pnl_r > 0 else "LOSS"
            else:
                pnl_r = rr_target if outcome == "WIN" else -1.0
                
            all_trades.append({
                "symbol": sym,
                "entry_time": times[entry_idx],
                "pump_pct": pump_pct,
                "vol_mult": vol_mult,
                "entry_px": entry_px,
                "sl_px": sl_px,
                "tp_px": tp_px,
                "outcome": outcome,
                "pnl_r": pnl_r,
                "bars_held": bars_held
            })
            
            i += max(1, bars_held)
            
    return all_trades

def print_metrics(label, trades):
    if not trades:
        print(f"[{label}] No trades generated.\n")
        return
    total = len(trades)
    wins = len([t for t in trades if t["outcome"] == "WIN"])
    losses = total - wins
    win_rate = (wins / total) * 100.0
    net_r = sum(t["pnl_r"] for t in trades)
    gross_win_r = sum(t["pnl_r"] for t in trades if t["pnl_r"] > 0)
    gross_loss_r = abs(sum(t["pnl_r"] for t in trades if t["pnl_r"] < 0))
    profit_factor = (gross_win_r / gross_loss_r) if gross_loss_r > 0 else float("inf")
    
    equity_curve = [0.0]
    for t in trades:
        equity_curve.append(equity_curve[-1] + t["pnl_r"])
    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak: peak = eq
        dd = peak - eq
        if dd > max_dd: max_dd = dd
        
    avg_bars = np.mean([t["bars_held"] for t in trades])
    
    print(f"=========================================================================================")
    print(f"📊 {label}")
    print(f"=========================================================================================")
    print(f"  • Total Trades       : {total}")
    print(f"  • Win / Loss         : {wins} Wins / {losses} Losses")
    print(f"  • Win Rate           : {win_rate:.2f}% (Break-even needed: 33.3%)")
    print(f"  • Net Return (R)     : {'+' if net_r > 0 else ''}{net_r:.2f} R")
    print(f"  • Profit Factor      : {profit_factor:.2f}")
    print(f"  • Max Drawdown (R)   : -{max_dd:.2f} R")
    print(f"  • Avg Hold Time      : {avg_bars:.1f} hours")
    print(f"  • Return / Drawdown  : {(net_r / max_dd):.2f}x" if max_dd > 0 else "N/A")
    print()

# 1. OLD UNFILTERED SETTING (0% Pump, $25k Min Vol, 0 Base Floor, 3.5x Spike)
trades_old = run_backtest_cached(
    min_pump_pct=0.0,
    min_vol_mult=3.5,
    min_candle_notional=25000.0,
    min_base_notional=0.0,
    rr_target=2.0,
    sl_atr_mult=1.0
)

# 2. INTERMEDIATE (+2.0% Pump, $100k Min Vol, $20k Base, 3.5x Spike)
trades_inter = run_backtest_cached(
    min_pump_pct=2.0,
    min_vol_mult=3.5,
    min_candle_notional=100000.0,
    min_base_notional=20000.0,
    rr_target=2.0,
    sl_atr_mult=1.0
)

# 3. NEW INSTITUTIONAL SETTING (+3.0% Pump, $250k Min Vol, $35k Base, 3.5x Spike)
trades_new = run_backtest_cached(
    min_pump_pct=3.0,
    min_vol_mult=3.5,
    min_candle_notional=250000.0,
    min_base_notional=35000.0,
    rr_target=2.0,
    sl_atr_mult=1.0
)

# 4. PARABOLIC EXHAUSTION SETTING (+5.0% Pump, $500k Min Vol, $50k Base, 4.0x Spike)
trades_parabolic = run_backtest_cached(
    min_pump_pct=5.0,
    min_vol_mult=4.0,
    min_candle_notional=500000.0,
    min_base_notional=50000.0,
    rr_target=2.0,
    sl_atr_mult=1.0
)

print_metrics("1. OLD UNFILTERED (0% Pump, No Liquidity Floor, 3.5x Spike)", trades_old)
print_metrics("2. WEAK PUMP (+2.0% Pump, $100k Min Vol, 3.5x Spike)", trades_inter)
print_metrics("3. NEW INSTITUTIONAL (+3.0% Pump, $250k Min Vol, $35k Base, 3.5x Spike)", trades_new)
print_metrics("4. PARABOLIC EXHAUSTION (+5.0% Pump, $500k Min Vol, $50k Base, 4.0x Spike)", trades_parabolic)
print("=========================================================================================")
