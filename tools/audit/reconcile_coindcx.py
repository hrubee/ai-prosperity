#!/usr/bin/env python3
import os
import sys
import json
import requests
import time
import hmac
import hashlib

# Load /root/trading-bot/crypto/.env
env_f = "/root/trading-bot/crypto/.env"
if os.path.exists(env_f):
    with open(env_f) as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

sys.path.insert(0, "/root/trading-bot/crypto/platforms/coindcx")
sys.path.insert(0, "/root/go-trader/platforms/coindcx")

try:
    from adapter import CoinDCXExchangeAdapter
except Exception as e:
    print("Could not import adapter:", e)
    CoinDCXExchangeAdapter = None

key1 = os.environ.get("COINDCX_LIVE_API_KEY", os.environ.get("COINDCX_KEY", "")).strip()
secret1 = os.environ.get("COINDCX_LIVE_API_SECRET", os.environ.get("COINDCX_SECRET", "")).strip()
key2 = os.environ.get("COINDCX_KEY_2", "").strip()
secret2 = os.environ.get("COINDCX_SECRET_2", "").strip()

print("===================================================================")
print("  ⚡ COINDCX LIVE ACCOUNTS & BALANCE RECONCILIATION AUDIT ⚡")
print("===================================================================")

def query_coindcx_futures_balance(key, secret):
    url = "https://api.coindcx.com/exchange/v1/derivatives/futures/balances"
    ts = int(time.time() * 1000)
    body = json.dumps({"timestamp": ts}, separators=(',', ':'))
    sig = hmac.new(secret.encode('utf-8'), body.encode('utf-8'), hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": key,
        "X-AUTH-SIGNATURE": sig
    }
    try:
        r = requests.post(url, data=body, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def audit_account(acc_label, key, secret, start_bal_inr):
    if not key or not secret:
        print(f"\n[{acc_label}] Not Configured")
        return
    print(f"\n[{acc_label}] API Key Ending: ...{key[-6:]}")
    
    # Direct API Balances
    raw_bal = query_coindcx_futures_balance(key, secret)
    print("  - CoinDCX Futures Balances Response:")
    if isinstance(raw_bal, list):
        for item in raw_bal:
            print("    *", json.dumps(item))
    else:
        print("    *", json.dumps(raw_bal))

    if CoinDCXExchangeAdapter:
        adapter = CoinDCXExchangeAdapter(key=key, secret=secret)
        try:
            free_inr = adapter.get_free_inr_balance()
            eq_inr = adapter.get_inr_equity()
            print(f"  - Live Free INR Balance: ₹{free_inr:,.2f} INR")
            print(f"  - Live Total Equity INR: ₹{eq_inr:,.2f} INR (${eq_inr/88.5:,.2f} USDT)")
            pnl_diff = eq_inr - start_bal_inr
            print(f"  - Configured Start Balance: ₹{start_bal_inr:,.2f} INR")
            print(f"  - Reconciled Net PnL: {pnl_diff:+,.2f} INR (${pnl_diff/88.5:+,.2f} USDT)")
        except Exception as e:
            print("  - Adapter Error:", e)

        try:
            positions = adapter.fetch_positions()
            print(f"  - Active Open Positions: {len(positions)}")
            for p in positions:
                print(f"    * Symbol: {p.get('symbol')} | Qty: {p.get('size')} | Entry: ${p.get('entry_price')}")
        except Exception as e:
            print("  - Position Error:", e)

audit_account("Account 1 (Primary Key)", key1, secret1, 15000.0)
audit_account("Account 2 (Secondary Key)", key2, secret2, 20000.0)

print("\n===================================================================")
