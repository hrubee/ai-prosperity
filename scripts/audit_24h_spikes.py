import sys
import os
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "/root/trading-bot/crypto")
from platforms.coindcx.adapter import CoinDCXExchangeAdapter

A = CoinDCXExchangeAdapter()
universe = A.active_bases()
print(f"Auditing past 24 hours across {len(universe)} CoinDCX coins...")

now_ts = int(time.time() * 1000)
# 24 hours = 6 x 4H candles
# Let's inspect the last 8 closed 4H candles for every coin

def audit_coin(base):
    try:
        # Fetch last 35 4H candles (closed only)
        candles = A.get_ohlcv(base, "4h", limit=35, include_forming=False)
        if not candles or len(candles) < 26:
            return []
            
        spikes = []
        # Last 6 closed 4H candles represent the past 24 hours
        for i in range(-6, 0):
            idx = len(candles) + i
            c = candles[idx]
            # 20-period baseline volume prior to candle c
            prior_20 = candles[idx-20 : idx]
            base_vol = sum(x[5] for x in prior_20) / 20.0
            if base_vol <= 0:
                continue
                
            vol_mult = c[5] / base_vol
            c_open = c[1]
            c_high = c[2]
            c_low = c[3]
            c_close = c[4]
            c_vol = c[5]
            pump_pct = ((c_close - c_open) / c_open) * 100.0
            is_green = c_close > c_open
            
            c_time_str = datetime.fromtimestamp(c[0]/1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            c_time_ist = datetime.fromtimestamp(c[0]/1000 + 19800, tz=timezone.utc).strftime("%d-%b %I:%M %p IST")
            
            if vol_mult >= 8.0:
                spikes.append({
                    "base": base,
                    "candle_ts": c[0],
                    "time_ist": c_time_ist,
                    "time_utc": c_time_str,
                    "vol_mult": vol_mult,
                    "pump_pct": pump_pct,
                    "is_green": is_green,
                    "open": c_open,
                    "close": c_close,
                    "vol": c_vol
                })
        return spikes
    except Exception:
        return []

all_spikes = []
with ThreadPoolExecutor(max_workers=50) as ex:
    futs = [ex.submit(audit_coin, b) for b in universe]
    for f in as_completed(futs):
        res = f.result()
        if res:
            all_spikes.extend(res)

print("\n" + "="*85)
print(f"ALL 4H VOLUME SPIKES >= 8.0x DETECTED IN THE LAST 24 HOURS (Total: {len(all_spikes)})")
print("="*85)
print(f"{'TIMESTAMP (IST)':<20} | {'COIN':<10} | {'VOLUME SPIKE':<14} | {'4H PUMP %':<12} | {'CANDLE COLOR'}")
print("-" * 85)

sorted_spikes = sorted(all_spikes, key=lambda x: x["candle_ts"], reverse=True)
for s in sorted_spikes:
    color = "🟢 GREEN" if s["is_green"] else "🔴 RED"
    print(f"{s['time_ist']:<20} | #{s['base']:<9} | {s['vol_mult']:>11.2f}x | {s['pump_pct']:>+10.2f}% | {color}")

print("\n" + "="*85)
print("BREAKDOWN BY THRESHOLD (GREEN EXPANSIONS):")
print(f"• Spikes >= 10.0x: {len([s for s in all_spikes if s['vol_mult'] >= 10.0 and s['is_green']])}")
print(f"• Spikes >= 15.0x: {len([s for s in all_spikes if s['vol_mult'] >= 15.0 and s['is_green']])}")
print(f"• Spikes >= 20.0x: {len([s for s in all_spikes if s['vol_mult'] >= 20.0 and s['is_green']])}")
print(f"• Spikes >= 30.0x: {len([s for s in all_spikes if s['vol_mult'] >= 30.0 and s['is_green']])}")
print("="*85)
