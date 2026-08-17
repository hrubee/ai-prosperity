#!/usr/bin/env python3
import sys, os, json, time, hmac, hashlib, requests, datetime
from dotenv import load_dotenv
load_dotenv("/root/trading-bot/crypto/.env")

key = os.environ.get("COINDCX_API_KEY", "").strip()
sec = os.environ.get("COINDCX_API_SECRET", "").strip()

def sign(body_str):
    return hmac.new(sec.encode(), body_str.encode(), hashlib.sha256).hexdigest()

def post(path, body):
    body["timestamp"] = int(time.time() * 1000)
    b_str = json.dumps(body, separators=(",", ":"))
    headers = {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": key,
        "X-AUTH-SIGNATURE": sign(b_str)
    }
    r = requests.post(f"https://api.coindcx.com{path}", data=b_str, headers=headers, timeout=10)
    return r.json()

print("=" * 95)
print("📊 LIVE COINDCX FUTURES EXECUTION & TRADE AUDIT")
print("=" * 95)

# 1. Check order history
print("1. Recent Orders / Status for DumpRide Strategy:")
orders = post("/exchange/v1/derivatives/futures/orders", {"page": 1, "size": 10})
if isinstance(orders, list):
    for o in orders:
        oid = o.get("id")
        pair = o.get("pair")
        side = o.get("side")
        st = o.get("status")
        otyp = o.get("order_type")
        px = o.get("price")
        avgpx = o.get("avg_price")
        qty = o.get("total_quantity")
        created = o.get("created_at")
        print(f"  -> ID: {oid} | Pair: {pair:<10} | Side: {side:<4} | Status: {st:<10} | Type: {otyp:<12} | Price: {px} | AvgPx: {avgpx} | Qty: {qty} | Time: {created}")
else:
    print("Orders Response:", orders)

# 2. Check trade fills
print("\n2. Recent Trade Fills:")
trades = post("/exchange/v1/derivatives/futures/user_trades", {"page": 1, "size": 10})
if isinstance(trades, list):
    for t in trades:
        tid = t.get("id")
        oid = t.get("order_id")
        pair = t.get("pair")
        side = t.get("side")
        px = t.get("price")
        qty = t.get("quantity")
        fee = t.get("fee_amount")
        pnl = t.get("realised_pnl")
        ts = t.get("timestamp")
        dt_str = datetime.datetime.fromtimestamp(ts/1000, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if ts else "N/A"
        print(f"  -> Trade ID: {tid} | Order: {oid} | Pair: {pair:<10} | Side: {side:<4} | Price: {px} | Qty: {qty} | Fee: ₹{fee} | PnL: ₹{pnl} | Time: {dt_str}")
else:
    print("Trades Response:", trades)

print("=" * 95)
