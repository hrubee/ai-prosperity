#!/usr/bin/env python3
import os
import sys
import json
import requests
import time
import hmac
import hashlib

# Load environment
for env_f in ["/root/trading-bot/crypto/.env", "/root/go-trader/.env", "/root/aiprosperity/backend/.env"]:
    if os.path.exists(env_f):
        with open(env_f) as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")

key1 = os.environ.get("COINDCX_LIVE_API_KEY", os.environ.get("COINDCX_KEY", "")).strip()
secret1 = os.environ.get("COINDCX_LIVE_API_SECRET", os.environ.get("COINDCX_SECRET", "")).strip()

key2 = os.environ.get("COINDCX_KEY_2", "").strip()
secret2 = os.environ.get("COINDCX_SECRET_2", "").strip()

print("===================================================================")
print("  ⚡ DIRECT COINDCX EXCHANGE WALLET TRADES & PNL RECONCILIATION ⚡")
print("===================================================================")

def fetch_coindcx_trades(key, secret):
    url = "https://api.coindcx.com/exchange/v1/derivatives/futures/trades"
    ts = int(time.time() * 1000)
    body_dict = {"timestamp": ts, "page": "1", "size": "100"}
    json_body = json.dumps(body_dict, separators=(',', ':'))
    sig = hmac.new(secret.encode('utf-8'), json_body.encode('utf-8'), hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": key,
        "X-AUTH-SIGNATURE": sig
    }
    try:
        r = requests.post(url, data=json_body, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def audit_account_trades(label, key, secret, start_bal_inr):
    print(f"\n[{label}] API Key: ...{key[-6:]}")
    data = fetch_coindcx_trades(key, secret)
    
    if isinstance(data, list):
        print(f"  - Total Trades Returned from Exchange API: {len(data)}")
        if len(data) == 0:
            print("  - No trades recorded on this exchange account.")
            return
        
        tot_pnl = 0.0
        wins = 0
        losses = 0
        
        print("\n  --- EXCHANGE TRADE FILLS LIST ---")
        for i, t in enumerate(data, 1):
            pair = t.get("pair") or t.get("symbol") or t.get("instrument") or "UNKNOWN"
            side = t.get("side") or t.get("position_side") or ""
            price = float(t.get("price") or t.get("avg_price") or 0)
            qty = float(t.get("quantity") or t.get("qty") or t.get("size") or 0)
            pnl = float(t.get("pnl") or t.get("realized_pnl") or t.get("profit") or 0)
            fee = float(t.get("fee") or t.get("fee_amount") or 0)
            created = t.get("created_at") or t.get("timestamp") or ""

            tot_pnl += pnl
            if pnl > 0: wins += 1
            elif pnl < 0: losses += 1
            
            print(f"  {i:2d}. {created} | {pair:<15} | {side:<4} | Qty: {qty:<10.2f} | Px: ${price:<8.4f} | PnL: ${pnl:+6.2f} | Fee: ${fee:.4f}")
            
        print("\n  ------------------------------------------------")
        print(f"  Summary for {label}:")
        print(f"  - Total Trades: {len(data)} (Wins: {wins}, Losses: {losses})")
        print(f"  - Net Realized Trade PnL: ${tot_pnl:+.2f} USDT (₹{tot_pnl*88.5:+,.2f} INR)")
    else:
        print("  - Exchange API Error / Response:", json.dumps(data))

if key1 and secret1:
    audit_account_trades("ACCOUNT 1 (Primary Key)", key1, secret1, 15000.0)

if key2 and secret2:
    audit_account_trades("ACCOUNT 2 (Secondary Key)", key2, secret2, 20000.0)

print("\n===================================================================")
