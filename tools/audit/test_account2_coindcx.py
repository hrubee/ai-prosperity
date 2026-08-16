#!/usr/bin/env python3
import sys, json, os

sys.path.insert(0, '/root/trading-bot/crypto/platforms/coindcx')
from adapter import CoinDCXExchangeAdapter

key2 = "b432d4fd94a65fcf075f412e055c30f9b658884d22783b7e"
secret2 = "b02e411339eba67773eb891ccd21eff868273bc7b41177974956f2efb65f9b26"

a2 = CoinDCXExchangeAdapter(key=key2, secret=secret2)

print("=== TESTING SECONDARY COINDCX ACCOUNT ===")
try:
    balances = a2._post("/exchange/v1/users/balances", {})
    inr_bal = [b for b in balances if isinstance(b, dict) and b.get("currency") == "INR"]
    print("INR Balances Account 2:", json.dumps(inr_bal, indent=2))
except Exception as e:
    print("Error fetching balances Account 2:", e)

try:
    pos = a2.fetch_positions()
    print("Active Positions Account 2:", json.dumps(pos, indent=2))
except Exception as e:
    print("Error fetching positions Account 2:", e)
