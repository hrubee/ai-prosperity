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

print("=== FETCHING REAL COINDCX FILLS FOR SWARMS ===")
try:
    trades = a._post("/exchange/v1/derivatives/futures/trades", {"pair": "B-SWARMS_USDT"})
    print(json.dumps(trades, indent=2))
except Exception as e:
    print("Error:", e)
