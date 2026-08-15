#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, "/root/go-trader/platforms/coindcx")
from adapter import CoinDCXExchangeAdapter

for env_f in ["/root/trading-bot/crypto/.env", "/root/go-trader/.env"]:
    if os.path.exists(env_f):
        with open(env_f) as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")

k1 = os.environ.get("COINDCX_LIVE_API_KEY", "").strip()
s1 = os.environ.get("COINDCX_LIVE_API_SECRET", "").strip()

k2 = os.environ.get("COINDCX_KEY_2", "").strip()
s2 = os.environ.get("COINDCX_SECRET_2", "").strip()

print("===================================================================")
print("  ⚡ ADAPTER BALANCE AUDIT FOR ACCOUNT 1 AND ACCOUNT 2 ⚡")
print("===================================================================")

a1 = CoinDCXExchangeAdapter(key=k1, secret=s1)
a2 = CoinDCXExchangeAdapter(key=k2, secret=s2)

bal1 = a1.get_free_inr_balance()
bal2 = a2.get_free_inr_balance()

print(f"Account 1 (Key ending in ...{k1[-6:]}): Free INR Balance = ₹{bal1:,.2f} INR")
print(f"Account 2 (Key ending in ...{k2[-6:]}): Free INR Balance = ₹{bal2:,.2f} INR")

# Let's inspect raw balance response for Account 2 in adapter
raw_user_bal2 = a2._post("/exchange/v1/users/balances", {})
print("\nRaw User Balances for Account 2 from Adapter:")
if isinstance(raw_user_bal2, list):
    non_zero = [b for b in raw_user_bal2 if float(b.get("balance") or 0) > 0 or float(b.get("locked_balance") or 0) > 0]
    print(f"Found {len(non_zero)} non-zero currency balances:")
    for item in non_zero:
        print("  *", item)
else:
    print(raw_user_bal2)

print("\n===================================================================")
