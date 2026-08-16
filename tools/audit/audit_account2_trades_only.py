#!/usr/bin/env python3
import sys, os, json, time, urllib.request, hmac, hashlib

sys.path.insert(0, ".")
from shared_scripts.stream_fibvol_coindcx import load_env
load_env()

key2 = os.environ.get("COINDCX_KEY_2") or os.environ.get("COINDCX_ACCOUNT2_API_KEY")
secret2 = os.environ.get("COINDCX_SECRET_2") or os.environ.get("COINDCX_ACCOUNT2_API_SECRET")

print(f"=== COINDCX ACCOUNT 2 TRADES AUDIT (Key ending: ...{key2[-6:]}) ===", flush=True)

def fetch_fills(key, secret):
    url = "https://api.coindcx.com/exchange/v1/derivatives/futures/trades"
    all_trades = []
    for page in range(1, 25):
        body = {"page": str(page), "size": "100"}
        json_body = json.dumps(body, separators=(",", ":"))
        sig = hmac.new(secret.encode("utf-8"), json_body.encode("utf-8"), hashlib.sha256).hexdigest()
        headers = {"Content-Type": "application/json", "X-AUTH-APIKEY": key, "X-AUTH-SIGNATURE": sig, "User-Agent": "Mozilla/5.0"}
        req = urllib.request.Request(url, data=json_body.encode("utf-8"), headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            res = json.loads(resp.read().decode())
            if isinstance(res, list) and len(res) > 0:
                all_trades.extend(res)
            else:
                break
        except Exception as e:
            print(f"Page {page} error: {e}", flush=True)
            break
    return all_trades

trades = fetch_fills(key2, secret2)
print(f"Total Raw Trade Fills Returned for Account 2: {len(trades)}", flush=True)

if not trades:
    print("No trades found for Account 2.", flush=True)
    sys.exit(0)

by_pair = {}
for t in trades:
    pair = t.get("pair")
    if pair:
        by_pair.setdefault(pair, []).append(t)

print(f"Total Unique Pairs Traded on Account 2: {len(by_pair)}\n", flush=True)
hdr_pair = "PAIR"
hdr_bqty = "BUY QTY"
hdr_bvwap = "BUY VWAP"
hdr_sqty = "SELL QTY"
hdr_svwap = "SELL VWAP"
hdr_fees = "FEES ($)"
hdr_netpnl = "NET PNL ($)"
hdr_netinr = "NET PNL (INR)"

header = f"{hdr_pair:<15} | {hdr_bqty:<10} | {hdr_bvwap:<10} | {hdr_sqty:<10} | {hdr_svwap:<10} | {hdr_fees:<10} | {hdr_netpnl:<12} | {hdr_netinr:<12}"
print(header, flush=True)
print("-" * len(header), flush=True)

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
    print(f"{pair:<15} | {b_qty:<10.1f} | {b_vwap:<10.5f} | {s_qty:<10.1f} | {s_vwap:<10.5f} | {fee_str:<10} | {net_usd_str:<12} | {net_inr_str:<12}", flush=True)

print("-" * len(header), flush=True)
print(f"🎯 ACCOUNT 2 TOTAL REALIZED NET PNL: ${tot_pnl_usd:+.2f} USD | Rs.{tot_pnl_usd * 86.0:+.2f} INR", flush=True)
print(f"Total Wins: {wins} | Total Losses: {losses} | Win Rate: {(wins/(wins+losses)*100 if (wins+losses)>0 else 0):.1f}% | Total Fees Paid: ${tot_fees_usd:.4f}", flush=True)
