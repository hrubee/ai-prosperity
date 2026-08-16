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

key2 = os.environ.get("COINDCX_KEY_2", "").strip()
secret2 = os.environ.get("COINDCX_SECRET_2", "").strip()

print("===================================================================")
print("  ⚡ ACCOUNT 2 (KEY ...783b7e) EXACT TRADE-BY-TRADE PNL AUDIT ⚡")
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

trades_data = fetch_coindcx_trades(key2, secret2)

if isinstance(trades_data, list):
    print(f"Total Trade Fills Returned from Exchange API for Account 2: {len(trades_data)}")
    
    # Group fills by symbol/pair
    pairs = {}
    total_fee_usdt = 0.0
    
    for t in trades_data:
        pair = t.get("pair") or t.get("symbol") or "UNKNOWN"
        side = t.get("side") or ""
        price = float(t.get("price") or 0)
        qty = float(t.get("quantity") or t.get("qty") or 0)
        fee = float(t.get("fee") or 0)
        pnl = float(t.get("pnl") or t.get("realized_pnl") or 0)
        created = t.get("created_at") or t.get("timestamp") or 0
        
        total_fee_usdt += fee
        
        if pair not in pairs:
            pairs[pair] = {"buys_qty": 0.0, "buys_val": 0.0, "sells_qty": 0.0, "sells_val": 0.0, "raw_pnl": 0.0, "fills": []}
            
        pairs[pair]["fills"].append(t)
        pairs[pair]["raw_pnl"] += pnl
        
        if side.lower() == "buy":
            pairs[pair]["buys_qty"] += qty
            pairs[pair]["buys_val"] += (qty * price)
        elif side.lower() == "sell":
            pairs[pair]["sells_qty"] += qty
            pairs[pair]["sells_val"] += (qty * price)

    print("\n--- ACCOUNT 2 PER-SYMBOL RECONCILED PNL ---")
    grand_tot_pnl = 0.0
    
    for pair, stats in pairs.items():
        buy_vwap = (stats["buys_val"] / stats["buys_qty"]) if stats["buys_qty"] > 0 else 0
        sell_vwap = (stats["sells_val"] / stats["sells_qty"]) if stats["sells_qty"] > 0 else 0
        
        # Realized PnL = (Sell Value - Buy Value) for Long positions
        calc_pnl = stats["sells_val"] - stats["buys_val"]
        grand_tot_pnl += calc_pnl
        
        sign = "+" if calc_pnl > 0 else ""
        print(f"\nCoin: {pair}")
        print(f"  - Buy Qty: {stats['buys_qty']:,.2f} @ VWAP ${buy_vwap:.6f} (Total Buy: ${stats['buys_val']:.2f})")
        print(f"  - Sell Qty: {stats['sells_qty']:,.2f} @ VWAP ${sell_vwap:.6f} (Total Sell: ${stats['sells_val']:.2f})")
        print(f"  - Realized Trade PnL: {sign}${calc_pnl:.2f} USDT (₹{calc_pnl * 88.5:+,.2f} INR)")

    print("\n===================================================================")
    print("  📊 ACCOUNT 2 SUMMARY METRICS")
    print("===================================================================")
    print(f"  - Total Exchange Trade Fills: {len(trades_data)}")
    print(f"  - Total Exchange Fees Paid: ${total_fee_usdt:.2f} USDT (₹{total_fee_usdt * 88.5:.2f} INR)")
    print(f"  - NET TRADE PNL (USDT): ${grand_tot_pnl:+.2f} USDT")
    print(f"  - NET TRADE PNL (INR @ 88.5): ₹{grand_tot_pnl * 88.5:+,.2f} INR")

else:
    print("API Error Response:", json.dumps(trades_data))

print("\n===================================================================")
