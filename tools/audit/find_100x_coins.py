#!/usr/bin/env python3
import sys, os, json
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, "/root/go-trader/platforms/coindcx")
from adapter import CoinDCXExchangeAdapter

try:
    for ln in open("/root/go-trader/.env"):
        ln = ln.strip()
        if ln and "=" in ln and not ln.startswith("#"):
            k, v = ln.split("=", 1)
            os.environ[k.strip()] = v.strip().strip('"').strip("'")
except Exception:
    pass

A = CoinDCXExchangeAdapter(os.environ.get("COINDCX_API_KEY"), os.environ.get("COINDCX_SECRET_KEY"))

try:
    instruments = A._get("https://api.coindcx.com/exchange/v1/derivatives/futures/data/active_instruments")
    bases = sorted(list({A.base_of(inst) for inst in instruments}))
    
    coins_100x = []
    
    def check_coin(base):
        try:
            inst = A.instrument(base)
            max_lev = float(inst.get("max_leverage_long") or 0)
            if max_lev >= 100:
                coins_100x.append((base, max_lev))
        except Exception:
            pass

    print(f"Scanning {len(bases)} active instruments using 50 parallel threads...", flush=True)
    with ThreadPoolExecutor(max_workers=50) as executor:
        executor.map(check_coin, bases)
        
    print("\n=== Coins supporting 100x leverage or more ===")
    if coins_100x:
        for coin, lev in sorted(coins_100x):
            print(f"- {coin}: {lev}x leverage")
    else:
        print("No coins found with 100x leverage or higher.")
except Exception as e:
    print("Error:", e)
