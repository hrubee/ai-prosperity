#!/usr/bin/env python3
import sys, os, json, time, urllib.request, hmac, hashlib

def load_env_file(path):
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")

load_env_file("/root/trading-bot/crypto/.env")
load_env_file("/root/go-trader/.env")

key1 = os.environ.get("COINDCX_LIVE_API_KEY") or os.environ.get("COINDCX_API_KEY")
secret1 = os.environ.get("COINDCX_LIVE_API_SECRET") or os.environ.get("COINDCX_API_SECRET")

key2 = os.environ.get("COINDCX_KEY_2") or os.environ.get("COINDCX_ACCOUNT2_API_KEY")
secret2 = os.environ.get("COINDCX_SECRET_2") or os.environ.get("COINDCX_ACCOUNT2_API_SECRET")

def post_req(key, secret, path, body):
    if not key or not secret: return None
    url = f"https://api.coindcx.com{path}"
    json_body = json.dumps(body, separators=(",", ":"))
    sig = hmac.new(secret.encode("utf-8"), json_body.encode("utf-8"), hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "X-AUTH-APIKEY": key, "X-AUTH-SIGNATURE": sig, "User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, data=json_body.encode("utf-8"), headers=headers)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    except Exception as e:
        print(f"Error {path}: {e}")
        return None

def audit_pair_fills(acc_name, key, secret):
    print(f"\n==================================================")
    print(f"🔍 AUDITING HEI TRADES FOR {acc_name}")
    print(f"==================================================")
    if not key or not secret:
        print("API keys missing!")
        return
    all_trades = []
    for page in range(1, 25):
        res = post_req(key, secret, "/exchange/v1/derivatives/futures/trades", {"page": str(page), "size": "100"})
        if isinstance(res, list) and len(res) > 0:
            all_trades.extend(res)
        else:
            break
            
    hei_trades = [t for t in all_trades if "HEI" in (t.get("pair") or "").upper()]
    print(f"Total HEI Trade Fills Found: {len(hei_trades)}")
    
    if not hei_trades:
        return
        
    buys = [t for t in hei_trades if t.get("side") == "buy"]
    sells = [t for t in hei_trades if t.get("side") == "sell"]
    b_qty = sum(float(t.get("quantity", 0)) for t in buys)
    b_vol = sum(float(t.get("quantity", 0)) * float(t.get("price", 0)) for t in buys)
    b_vwap = b_vol / b_qty if b_qty > 0 else 0.0
    
    s_qty = sum(float(t.get("quantity", 0)) for t in sells)
    s_vol = sum(float(t.get("quantity", 0)) * float(t.get("price", 0)) for t in sells)
    s_vwap = s_vol / s_qty if s_qty > 0 else 0.0
    
    fee_usd = sum(float(t.get("fee_amount", 0) or t.get("fee", 0)) for t in hei_trades)
    matched = min(b_qty, s_qty)
    pnl = (s_vwap - b_vwap) * matched - fee_usd if matched > 0 else 0.0
    
    print(f"Buy Qty: {b_qty} @ VWAP {b_vwap:.5f} | Sell Qty: {s_qty} @ VWAP {s_vwap:.5f}")
    print(f"Matched Qty: {matched} | Total Fees: ${fee_usd:.4f} | Net Realized PnL: ${pnl:+.2f} (Rs.{pnl*86.0:+.2f})")
    print("-" * 50)
    for t in hei_trades:
        ts = t.get("timestamp") or t.get("created_at")
        side = t.get("side")
        qty = t.get("quantity")
        px = t.get("price")
        fee = t.get("fee_amount") or t.get("fee")
        oid = t.get("order_id") or t.get("id")
        print(f"Time: {ts} | Side: {side:<5} | Qty: {qty:<10} | Price: {px:<10} | Fee: {fee} | OrderID: {oid}")

audit_pair_fills("ACCOUNT 1 (Primary)", key1, secret1)
audit_pair_fills("ACCOUNT 2 (Secondary)", key2, secret2)
