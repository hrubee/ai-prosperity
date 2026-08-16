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
print("  ⚡ SEARCHING ALL COINDCX FUTURES WALLET & BALANCE ENDPOINTS FOR ACC 2 ⚡")
print("===================================================================")

endpoints = [
    ("/exchange/v1/derivatives/futures/wallets", "GET"),
    ("/exchange/v1/derivatives/futures/balances", "GET"),
    ("/exchange/v1/derivatives/futures/user_info", "GET"),
    ("/exchange/v1/derivatives/futures/account", "GET"),
    ("/exchange/v1/users/info", "POST"),
    ("/exchange/v1/users/balances", "POST"),
    ("/exchange/v1/derivatives/futures/positions", "GET"),
    ("/exchange/v1/derivatives/futures/orders/active", "POST"),
]

for ep, method in endpoints:
    url = f"https://api.coindcx.com{ep}"
    ts = int(time.time() * 1000)
    body_dict = {"timestamp": ts}
    json_body = json.dumps(body_dict, separators=(',', ':'))
    sig = hmac.new(secret2.encode('utf-8'), json_body.encode('utf-8'), hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": key2,
        "X-AUTH-SIGNATURE": sig
    }
    try:
        if method == "POST":
            r = requests.post(url, data=json_body, headers=headers, timeout=5)
        else:
            r = requests.get(url, headers=headers, timeout=5)
        res = r.json()
        print(f"\n--- {method} {ep} ---")
        print(json.dumps(res, indent=2)[:500])
    except Exception as e:
        print(f"\n--- {method} {ep} Error: {e} ---")

print("\n===================================================================")
