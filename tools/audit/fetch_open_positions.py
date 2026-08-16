#!/usr/bin/env python3
import sys, os, json, time, urllib.request, hmac, hashlib

sys.path.insert(0, ".")
from shared_scripts.stream_fibvol_coindcx import load_env
load_env()

key1 = os.environ.get("COINDCX_LIVE_API_KEY") or os.environ.get("COINDCX_API_KEY")
secret1 = os.environ.get("COINDCX_LIVE_API_SECRET") or os.environ.get("COINDCX_API_SECRET")

key2 = os.environ.get("COINDCX_KEY_2") or os.environ.get("COINDCX_ACCOUNT2_API_KEY")
secret2 = os.environ.get("COINDCX_SECRET_2") or os.environ.get("COINDCX_ACCOUNT2_API_SECRET")

def post_req(key, secret, path, body):
    if not key or not secret:
        return None
    url = f"https://api.coindcx.com{path}"
    json_body = json.dumps(body, separators=(",", ":"))
    sig = hmac.new(secret.encode("utf-8"), json_body.encode("utf-8"), hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "X-AUTH-APIKEY": key, "X-AUTH-SIGNATURE": sig, "User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, data=json_body.encode("utf-8"), headers=headers)
    try:
        res = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        return res
    except Exception as e:
        return None

def check_account_positions(acc_name, key, secret):
    print(f"==================================================")
    print(f"🔍 {acc_name} OPEN POSITIONS AUDIT")
    print(f"==================================================")
    pos_res = post_req(key, secret, "/exchange/v1/derivatives/futures/positions", {})
    if pos_res is None:
        print("Failed to query positions (check API key permissions or network).\n")
        return 0.0, 0
    
    if not isinstance(pos_res, list):
        print(f"Unexpected response: {pos_res}\n")
        return 0.0, 0
        
    open_pos = [p for p in pos_res if abs(float(p.get("quantity", 0) or p.get("active_units", 0))) > 0]
    if not open_pos:
        print("No open positions found (0 active trades).\n")
        return 0.0, 0
        
    tot_upnl_usd = 0.0
    hdr = f"{'PAIR':<15} | {'SIDE':<6} | {'QTY':<10} | {'ENTRY PX':<10} | {'MARK PX':<10} | {'uPNL ($)':<12} | {'uPNL (INR)':<12}"
    print(hdr)
    print("-" * len(hdr))
    
    for p in open_pos:
        pair = p.get("pair") or p.get("symbol")
        side = (p.get("side") or ("LONG" if float(p.get("quantity", 0)) > 0 else "SHORT")).upper()
        qty = abs(float(p.get("quantity", 0) or p.get("active_units", 0)))
        entry_px = float(p.get("entry_price") or p.get("avg_price") or 0.0)
        mark_px = float(p.get("mark_price") or p.get("last_price") or entry_px)
        upnl_usd = float(p.get("unrealized_pnl") or p.get("pnl") or 0.0)
        
        # If exchange returns 0 upnl field, calculate directly
        if upnl_usd == 0.0 and entry_px > 0 and qty > 0:
            if side == "LONG":
                upnl_usd = (mark_px - entry_px) * qty
            else:
                upnl_usd = (entry_px - mark_px) * qty
                
        upnl_inr = upnl_usd * 86.0
        tot_upnl_usd += upnl_usd
        
        upnl_usd_str = f"${upnl_usd:+.2f}"
        upnl_inr_str = f"Rs.{upnl_inr:+.2f}"
        print(f"{pair:<15} | {side:<6} | {qty:<10.1f} | {entry_px:<10.5f} | {mark_px:<10.5f} | {upnl_usd_str:<12} | {upnl_inr_str:<12}")
        
    print("-" * len(hdr))
    print(f"🎯 TOTAL {acc_name} UNREALIZED PnL: ${tot_upnl_usd:+.2f} USD | Rs.{tot_upnl_usd * 86.0:+.2f} INR\n")
    return tot_upnl_usd, len(open_pos)

u1, count1 = check_account_positions("ACCOUNT 1 (Primary)", key1, secret1)
u2, count2 = check_account_positions("ACCOUNT 2 (Secondary)", key2, secret2)

comb_upnl = u1 + u2
print("==================================================")
print(f"📊 COMBINED TOTAL OPEN POSITIONS: {count1 + count2}")
print(f"💥 COMBINED UNREALIZED PnL (uPnL): ${comb_upnl:+.2f} USD | Rs.{comb_upnl * 86.0:+.2f} INR")
print("==================================================")
