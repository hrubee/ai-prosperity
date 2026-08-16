#!/usr/bin/env python3
import os
import sys
import json
import requests
import time
import hmac
import hashlib

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
print("  ⚡ DUMPING ALL CURRENCY BALANCES FOR ACCOUNT 2 (Satyajeet Jadhav) ⚡")
print("===================================================================")

def query_endpoint(endpoint_path, payload=None):
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
        r = requests.post(url, data=json_body, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

res = query_endpoint("/exchange/v1/users/balances")

if isinstance(res, list):
    print(f"Total currency records returned by CoinDCX: {len(res)}")
    for item in res:
        b = str(item.get("balance", "0"))
        lb = str(item.get("locked_balance", "0"))
        if b != "0" and b != "0.0" and b != "0.00" or lb != "0" and lb != "0.0" and lb != "0.00":
            print("  *", item)
else:
    print("API Error Response:", res)

print("\n===================================================================")
