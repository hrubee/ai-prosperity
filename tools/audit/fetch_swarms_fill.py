#!/usr/bin/env python3
import sys, json, os

if os.path.exists('/root/go-trader/.env'):
    for ln in open('/root/go-trader/.env'):
        ln = ln.strip()
        if ln and '=' in ln and not ln.startswith('#'):
            k, v = ln.split('=', 1)
            os.environ[k.strip()] = v.strip().strip("'").strip('"')

sys.path.insert(0, '/root/trading-bot/crypto/platforms/coindcx')
from adapter import CoinDCXExchangeAdapter
a = CoinDCXExchangeAdapter()

print("=== TESTING AUTHENTICATED GET ENDPOINTS ===")
get_endpoints = [
    "/exchange/v1/derivatives/futures/orders",
    "/exchange/v1/derivatives/futures/positions",
    "/exchange/v1/derivatives/futures/trades",
    "/exchange/v1/orders/trade_history",
    "/exchange/v1/users/balances"
]

for path in get_endpoints:
    url = f"https://api.coindcx.com{path}"
    try:
        res = a._get(url)
        print(f"=== GET SUCCESS: {path} ===")
        print(json.dumps(res, indent=2)[:1000])
    except Exception as e:
        print(f"=== GET ERROR: {path} -> {e}")
