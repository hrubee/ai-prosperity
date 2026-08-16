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

print("=== TESTING ORDER STATUS & TRADES ENDPOINTS ===")
endpoints = [
    ("/exchange/v1/derivatives/futures/orders/status", {"id": "a315004d-3005-40be-98b3-b651a2afffd7"}),
    ("/exchange/v1/derivatives/futures/orders", {"status": "filled", "pair": "B-RED_USDT"}),
    ("/exchange/v1/derivatives/futures/orders", {"status": "filled", "limit": 50}),
    ("/exchange/v1/users/trade_history", {"limit": 50}),
    ("/exchange/v1/derivatives/futures/trades", {"limit": 50})
]

for path, body in endpoints:
    try:
        res = a._post(path, body)
        print(f"=== SUCCESS: {path} (Body: {body}) ===")
        print(json.dumps(res, indent=2)[:1000])
    except Exception as e:
        print(f"=== ERROR: {path} (Body: {body}) -> {e}")
