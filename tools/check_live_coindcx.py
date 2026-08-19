import os
import sys
import time
import hmac
import hashlib
import json
import requests

cred_file = "/root/coindcx_credentials.json"
if os.path.exists(cred_file):
    with open(cred_file) as f:
        creds = json.load(f)
else:
    creds = {
        "key": os.environ.get("COINDCX_API_KEY", ""),
        "secret": os.environ.get("COINDCX_API_SECRET", "")
    }

key = creds.get("key", "")
secret = creds.get("secret", "")

def get_auth_headers(body_dict):
    body_json = json.dumps(body_dict, separators=(",", ":"))
    sig = hmac.new(secret.encode("utf-8"), body_json.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": key,
        "X-AUTH-SIGNATURE": sig
    }, body_json

ts = int(time.time() * 1000)

# 1. Wallets & Balances
headers, body = get_auth_headers({"timestamp": ts})
r = requests.post("https://api.coindcx.com/exchange/v1/users/balances", headers=headers, data=body)
balances = r.json()
inr_bal = next((float(b.get("balance", 0)) for b in balances if b.get("currency") == "INR"), 0.0)
usdt_bal = next((float(b.get("balance", 0)) for b in balances if b.get("currency") == "USDT"), 0.0)

# 2. Futures Positions
headers, body = get_auth_headers({"timestamp": ts, "page": 1, "size": 50})
r_pos = requests.post("https://api.coindcx.com/exchange/v1/derivatives/futures/positions", headers=headers, data=body)
positions = r_pos.json()

# 3. Recent Orders
headers, body = get_auth_headers({"timestamp": ts, "page": 1, "size": 30})
r_ord = requests.post("https://api.coindcx.com/exchange/v1/derivatives/futures/orders", headers=headers, data=body)
orders = r_ord.json()

# 4. Trades History
headers, body = get_auth_headers({"timestamp": ts, "page": 1, "size": 30})
r_tr = requests.post("https://api.coindcx.com/exchange/v1/derivatives/futures/trades", headers=headers, data=body)
trades = r_tr.json()

print("=========================================================================================")
print("💼 COINDCX LIVE ACCOUNT & TRADING AUDIT")
print("=========================================================================================")
print(f"• Active INR Balance : INR {inr_bal:,.2f}")
print(f"• Active USDT Balance: USD {usdt_bal:,.2f}")

print(f"\n• Open Positions ({len(positions) if isinstance(positions, list) else 0}):")
if isinstance(positions, list) and positions:
    for p in positions:
        print(f"  -> {p.get('pair')}: {p.get('side')} {p.get('active_pos')} @ Entry {p.get('entry_price')} | Unrealized PnL: INR {float(p.get('pnl', 0)):,.2f}")
else:
    print("  None currently active.")

print(f"\n• Recent Futures Orders ({len(orders) if isinstance(orders, list) else 0}):")
if isinstance(orders, list) and orders:
    for o in orders[:10]:
        print(f"  -> [{o.get('status')}] {o.get('side')} {o.get('pair')} | Qty: {o.get('quantity')} | Price: {o.get('price')} | Created: {o.get('created_at')}")
else:
    print("  No recent orders.")

print(f"\n• Recent Trades History ({len(trades) if isinstance(trades, list) else 0}):")
if isinstance(trades, list) and trades:
    for t in trades[:10]:
        print(f"  -> {t.get('side')} {t.get('pair')} | Qty: {t.get('quantity')} | Price: {t.get('price')} | Fee: {t.get('fee')} | Time: {t.get('created_at')}")
else:
    print("  No trades executed.")
print("=========================================================================================")
