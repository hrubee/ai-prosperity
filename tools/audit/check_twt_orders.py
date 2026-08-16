#!/usr/bin/env python3
import sys, os, json
sys.path.insert(0, "/root/go-trader/platforms/coindcx")
from adapter import CoinDCXExchangeAdapter

# Load env variables
try:
    for ln in open("/root/go-trader/.env"):
        ln = ln.strip()
        if ln and "=" in ln and not ln.startswith("#"):
            k, v = ln.split("=", 1)
            os.environ[k.strip()] = v.strip().strip('"').strip("'")
except Exception:
    pass

key = os.environ.get("COINDCX_API_KEY")
secret = os.environ.get("COINDCX_SECRET_KEY")

A = CoinDCXExchangeAdapter(key, secret)

# Fetch orders for TWT
try:
    print("=== Position Details for TWT_USDT ===")
    r = A._get("https://api.coindcx.com/exchange/v1/derivatives/futures/positions?pair=B-TWT_USDT")
    print(json.dumps(r, indent=2))
except Exception as e:
    print(f"No active positions: {e}")

try:
    print("\n=== Recent Fills ===")
    t = A._post("/exchange/v1/derivatives/futures/trades", {"pair": "B-TWT_USDT"})
    print(json.dumps(t, indent=2))
except Exception as e:
    print(f"Error checking fills: {e}")
