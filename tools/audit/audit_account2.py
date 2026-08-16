#!/usr/bin/env python3
import sys, os, json, time, urllib.request, hmac, hashlib

sys.path.insert(0, ".")
from shared_scripts.stream_fibvol_coindcx import load_env
load_env()

key2 = os.environ.get("COINDCX_KEY_2") or os.environ.get("COINDCX_ACCOUNT2_API_KEY")
secret2 = os.environ.get("COINDCX_SECRET_2") or os.environ.get("COINDCX_ACCOUNT2_API_SECRET")

print("=== ACCOUNT 2 API KEY CHECK ===")
print("Key 2 present:", bool(key2), "Key ending in:", key2[-6:] if key2 else "None")

def post_req(path, body):
    if not key2 or not secret2: return None
    url = f"https://api.coindcx.com{path}"
    json_body = json.dumps(body, separators=(",", ":"))
    sig = hmac.new(secret2.encode("utf-8"), json_body.encode("utf-8"), hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "X-AUTH-APIKEY": key2, "X-AUTH-SIGNATURE": sig, "User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, data=json_body.encode("utf-8"), headers=headers)
    try:
        res = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        return res
    except Exception as e:
        print(f"API request error {path}: {e}")
        return None

# Fetch Account 2 Balances
print("\n=== ACCOUNT 2 WALLET BALANCES ===")
bal_res = post_req("/exchange/v1/users/balances", {})
if isinstance(bal_res, list):
    for b in bal_res:
        currency = b.get("currency")
        balance = float(b.get("balance", 0))
        locked = float(b.get("locked_balance", 0))
        if balance > 0 or locked > 0:
            print(f"Currency: {currency:<10} | Balance: {balance:<12.4f} | Locked: {locked:<12.4f}")

# Fetch Open Futures Positions on Account 2
print("\n=== ACCOUNT 2 OPEN FUTURES POSITIONS ===")
pos_res = post_req("/exchange/v1/derivatives/futures/positions", {})
if isinstance(pos_res, list):
    open_positions = [p for p in pos_res if abs(float(p.get("quantity", 0))) > 0]
    print(f"Total Open Positions: {len(open_positions)}")
    for p in open_positions:
        print(p)

# Fetch Trade History on Account 2
print("\n=== ACCOUNT 2 HISTORICAL TRADE FILLS ===")
all_trades = []
for page in range(1, 20):
    res = post_req("/exchange/v1/derivatives/futures/trades", {"page": str(page), "size": "100"})
    if isinstance(res, list) and len(res) > 0:
        all_trades.extend(res)
    else:
        break

print(f"Total Trade Fills Returned for Account 2: {len(all_trades)}")

if not all_trades:
    print("No trade fills found for Account 2.")
    sys.exit(0)

by_pair = {}
for t in all_trades:
    pair = t.get("pair")
    if pair:
        by_pair.setdefault(pair, []).append(t)

print(f"\nTotal Pairs Traded on Account 2: {len(by_pair)}\n")
header = f"{'PAIR':<15} | {'BUY QTY':<10} | {'BUY VWAP':<10} | {'SELL QTY':<10} | {'SELL VWAP':<10} | {'FEES ($)':<10} | {'NET PNL ($)':<12} | {'NET PNL (INR)':<12}"
print(header)
print("-" * len(header))

tot_pnl_usd = 0.0
tot_fees_usd = 0.0
wins = 0
losses = 0

for pair, fills in sorted(by_pair.items()):
    buys = [f for f in fills if f.get("side") == "buy"]
    sells = [f for f in fills if f.get("side") == "sell"]
    
    b_qty = sum(float(f.get("quantity", 0)) for f in buys)
    b_vol = sum(float(f.get("quantity", 0)) * float(f.get("price", 0)) for f in buys)
    b_vwap = b_vol / b_qty if b_qty > 0 else 0.0
    
    s_qty = sum(float(f.get("quantity", 0)) for f in sells)
    s_vol = sum(float(f.get("quantity", 0)) * float(f.get("price", 0)) for f in sells)
    s_vwap = s_vol / s_qty if s_qty > 0 else 0.0
    
    fee_usd = sum(float(f.get("fee_amount", 0) or f.get("fee", 0)) for f in fills)
    
    matched_qty = min(b_qty, s_qty)
    gross_pnl = (s_vwap - b_vwap) * matched_qty if matched_qty > 0 else 0.0
    net_pnl = gross_pnl - fee_usd
    net_inr = net_pnl * 86.0
    
    tot_pnl_usd += net_pnl
    tot_fees_usd += fee_usd
    
    if net_pnl > 0.05: wins += 1
    elif net_pnl < -0.05: losses += 1
    
    fee_str = f"${fee_usd:.4f}"
    net_usd_str = f"${net_pnl:+.2f}"
    net_inr_str = f"Rs.{net_inr:+.2f}"
    print(f"{pair:<15} | {b_qty:<10.1f} | {b_vwap:<10.5f} | {s_qty:<10.1f} | {s_vwap:<10.5f} | {fee_str:<10} | {net_usd_str:<12} | {net_inr_str:<12}")

print("-" * len(header))
print(f"🎯 ACCOUNT 2 TOTAL REALIZED NET PNL: ${tot_pnl_usd:+.2f} USD | Rs.{tot_pnl_usd * 86.0:+.2f} INR")
print(f"Total Wins: {wins} | Total Losses: {losses} | Win Rate: {(wins/(wins+losses)*100 if (wins+losses)>0 else 0):.1f}% | Total Fees Paid: ${tot_fees_usd:.4f}")
