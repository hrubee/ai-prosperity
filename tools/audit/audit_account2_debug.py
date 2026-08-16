#!/usr/bin/env python3
import sys, os, json, time, urllib.request, hmac, hashlib

sys.path.insert(0, ".")
from shared_scripts.stream_fibvol_coindcx import load_env
load_env()

key2 = os.environ.get("COINDCX_KEY_2") or os.environ.get("COINDCX_ACCOUNT2_API_KEY")
secret2 = os.environ.get("COINDCX_SECRET_2") or os.environ.get("COINDCX_ACCOUNT2_API_SECRET")

print(f"Key2 present: {bool(key2)}, Secret2 present: {bool(secret2)}", flush=True)
if key2:
    print(f"Key2 ending: ...{key2[-6:]}", flush=True)

def post_req(path, body):
    if not key2 or not secret2:
        print("Missing key2/secret2!", flush=True)
        return None
    url = f"https://api.coindcx.com{path}"
    json_body = json.dumps(body, separators=(",", ":"))
    sig = hmac.new(secret2.encode("utf-8"), json_body.encode("utf-8"), hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "X-AUTH-APIKEY": key2, "X-AUTH-SIGNATURE": sig, "User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, data=json_body.encode("utf-8"), headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        raw = resp.read().decode()
        return json.loads(raw)
    except Exception as e:
        print(f"Error {path}: {e}", flush=True)
        return None

print("Checking Account 2 Balances...", flush=True)
bals = post_req("/exchange/v1/users/balances", {})
print(f"Balances result: {bals}", flush=True)

print("Checking Account 2 Positions...", flush=True)
pos = post_req("/exchange/v1/derivatives/futures/positions", {})
print(f"Positions result: {pos}", flush=True)

print("Checking Account 2 Trades...", flush=True)
trades = post_req("/exchange/v1/derivatives/futures/trades", {"page": "1", "size": "100"})
print(f"Trades page 1 count: {len(trades) if isinstance(trades, list) else trades}", flush=True)
