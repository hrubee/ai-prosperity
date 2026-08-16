#!/usr/bin/env python3
import os
import sys
import json
import requests
import time
import hmac
import hashlib

# Load env file
env_f = "/root/trading-bot/crypto/.env"
if os.path.exists(env_f):
    with open(env_f) as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

key2 = os.environ.get("COINDCX_KEY_2", "").strip()
secret2 = os.environ.get("COINDCX_SECRET_2", "").strip()

print("===================================================================")
print("  ⚡ FULL COINDCX BALANCE AUDIT FOR ACCOUNT 2 (...783b7e) ⚡")
print("===================================================================")

if not key2 or not secret2:
    print("Account 2 keys not found in environment.")
    sys.exit(1)

def query_endpoint(endpoint_path, method="POST", payload=None):
    url = f"https://api.coindcx.com{endpoint_path}"
    ts = int(time.time() * 1000)
    body_dict = {"timestamp": ts}
    if payload:
        body_dict.update(payload)
    json_body = json.dumps(body_dict, separators=(',', ':'))
    sig = hmac.new(secret2.encode('utf-8'), json_body.encode('utf-8'), hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": key2,
        "X-AUTH-SIGNATURE": sig
    }
    try:
        if method == "POST":
            r = requests.post(url, data=json_body, headers=headers, timeout=10)
        else:
            r = requests.get(url, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

# 1. Futures Balances
fut_bal = query_endpoint("/exchange/v1/derivatives/futures/balances")
print("\n1. FUTURES BALANCES ENDPOINT (/exchange/v1/derivatives/futures/balances):")
print(json.dumps(fut_bal, indent=2))

# 2. Spot / Main User Balances
spot_bal = query_endpoint("/exchange/v1/users/balances", method="POST")
print("\n2. SPOT / MAIN USER BALANCES ENDPOINT (/exchange/v1/users/balances):")
if isinstance(spot_bal, list):
    non_zero = [b for b in spot_bal if float(b.get("balance") or 0) > 0 or float(b.get("locked_balance") or 0) > 0]
    print(f"Total Non-Zero Assets: {len(non_zero)}")
    for item in non_zero:
        print("  *", json.dumps(item))
else:
    print(json.dumps(spot_bal, indent=2))

# 3. Positions check
pos_data = query_endpoint("/exchange/v1/derivatives/futures/positions")
print("\n3. ACTIVE POSITIONS ENDPOINT (/exchange/v1/derivatives/futures/positions):")
print(json.dumps(pos_data, indent=2))

print("\n===================================================================")
