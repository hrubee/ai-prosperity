import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "/root/trading-bot/crypto")
from platforms.coindcx.adapter import CoinDCXExchangeAdapter

A = CoinDCXExchangeAdapter()
universe = A.active_bases()
print(f"Total Universe: {len(universe)} coins")

def check_coin(base):
    try:
        ohlcv_closed = A.get_ohlcv(base, "4h", limit=35, include_forming=False)
        ohlcv_forming = A.get_ohlcv(base, "4h", limit=35, include_forming=True)
        res = {
            "base": base,
            "closed_mult": 0.0,
            "closed_pump": 0.0,
            "closed_green": False,
            "forming_mult": 0.0,
            "forming_pump": 0.0,
            "forming_green": False
        }
        if ohlcv_closed and len(ohlcv_closed) >= 22:
            base_vol = sum(c[5] for c in ohlcv_closed[-21:-1]) / 20.0
            last_c = ohlcv_closed[-1]
            if base_vol > 0:
                res["closed_mult"] = last_c[5] / base_vol
                res["closed_pump"] = ((last_c[4] - last_c[1]) / last_c[1]) * 100.0
                res["closed_green"] = last_c[4] > last_c[1]
        if ohlcv_forming and len(ohlcv_forming) >= 22:
            base_vol_f = sum(c[5] for c in ohlcv_forming[-21:-1]) / 20.0
            form_c = ohlcv_forming[-1]
            if base_vol_f > 0:
                res["forming_mult"] = form_c[5] / base_vol_f
                res["forming_pump"] = ((form_c[4] - form_c[1]) / form_c[1]) * 100.0
                res["forming_green"] = form_c[4] > form_c[1]
        return res
    except Exception:
        return None

results = []
with ThreadPoolExecutor(max_workers=50) as ex:
    futs = [ex.submit(check_coin, b) for b in universe]
    for f in as_completed(futs):
        r = f.result()
        if r:
            results.append(r)

print("\n" + "="*70)
print("TOP 10 CLOSED 4H VOLUME MULTIPLIERS (Last Closed Candle: 09:30 AM IST):")
print("="*70)
top_c = sorted(results, key=lambda x: x["closed_mult"], reverse=True)[:10]
for r in top_c:
    print(f"#{r['base']:<10} -> Volume: {r['closed_mult']:>6.2f}x | Pump: {r['closed_pump']:>+6.2f}% | Green: {r['closed_green']}")

print("\n" + "="*70)
print("TOP 10 CURRENT FORMING 4H VOLUME MULTIPLIERS (Forming toward 01:30 PM IST Close):")
print("="*70)
top_f = sorted(results, key=lambda x: x["forming_mult"], reverse=True)[:10]
for r in top_f:
    print(f"#{r['base']:<10} -> Volume: {r['forming_mult']:>6.2f}x | Pump: {r['forming_pump']:>+6.2f}% | Green: {r['forming_green']}")

spikes_10x_closed = [r for r in results if r["closed_mult"] >= 10.0 and r["closed_green"]]
spikes_10x_forming = [r for r in results if r["forming_mult"] >= 10.0 and r["forming_green"]]
print("\n" + "="*70)
print(f"Total Closed 4H Spikes >= 10x (Green): {len(spikes_10x_closed)}")
print(f"Total Forming 4H Spikes >= 10x (Green): {len(spikes_10x_forming)}")
print("="*70)
