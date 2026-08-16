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

endpoints = [
    "/exchange/v1/users/info",
    "/exchange/v1/users/balances",
    "/exchange/v1/derivatives/futures/wallets",
    "/exchange/v1/derivatives/futures/balances",
    "/exchange/v1/derivatives/futures/positions",
    "/exchange/v1/derivatives/futures/orders",
    "/exchange/v1/margin/balances",
    "/exchange/v1/margin/positions"
]

for ep in endpoints:
    try:
        res = a._post(ep, {})
        print(f"=== {ep} ===")
        print(json.dumps(res, indent=2)[:800])
    except Exception as e:
        print(f"=== {ep} === Error: {e}")
