#!/usr/bin/env python3
import os
import sys
import json
import requests
import time
import hmac
import hashlib

# Load env file
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
    CoinDCXExchangeAdapter = None

key1 = os.environ.get("COINDCX_LIVE_API_KEY", os.environ.get("COINDCX_KEY", "")).strip()
secret1 = os.environ.get("COINDCX_LIVE_API_SECRET", os.environ.get("COINDCX_SECRET", "")).strip()

print("===================================================================")
print("  ⚡ FETCHING LIVE COINDCX EXECUTED TRADES & CALCULATING PNL ⚡")
print("===================================================================")

def direct_post(url_path, extra_payload=None):
    url = f"https://api.coindcx.com{url_path}"
    ts = int(time.time() * 1000)
    body_dict = {"timestamp": ts}
    if extra_payload:
        body_dict.update(extra_payload)
    json_body = json.dumps(body_dict, separators=(',', ':'))
    sig = hmac.new(secret1.encode('utf-8'), json_body.encode('utf-8'), hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": key1,
        "X-AUTH-SIGNATURE": sig
    }
    try:
        r = requests.post(url, data=json_body, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

# 1. Fetch executed trades history from CoinDCX API
trades_data = direct_post("/exchange/v1/derivatives/futures/trade_history", {"limit": 100})
orders_data = direct_post("/exchange/v1/derivatives/futures/orders", {"status": "filled", "limit": 100})

print("\n--- 1. DIRECT COINDCX API TRADES HISTORY ---")
if isinstance(trades_data, list) and len(trades_data) > 0:
    print(f"Total Direct Trades Returned: {len(trades_data)}")
    for t in trades_data[:10]:
        print("  *", json.dumps(t))
else:
    print("Trade History Response:", json.dumps(trades_data))

print("\n--- 2. DIRECT COINDCX API FILLED ORDERS ---")
if isinstance(orders_data, list) and len(orders_data) > 0:
    print(f"Total Filled Orders Returned: {len(orders_data)}")
    for o in orders_data[:10]:
        print("  *", json.dumps(o))
else:
    print("Filled Orders Response:", json.dumps(orders_data))

# 3. Read Strategy Run Log Trades
log_path = "/root/fibvol_coindcx/run.log"
print(f"\n--- 3. STRATEGY ENGINE EXECUTION LOG TRADES ({log_path}) ---")

if os.path.exists(log_path):
    lines = open(log_path).readlines()
    closed_trades = []
    for l in lines:
        if "PnL:" in l and "TELEGRAM:" not in l:
            try:
                parts = l.split("PnL: ")
                pnl_str = parts[1].strip().replace("$", "").replace("+", "").replace("!", "")
                pnl_val = float(pnl_str)
                ts = l[1:24]
                coin = l.split("] [")[1].split("]")[0]
                closed_trades.append((ts, coin, pnl_val, l.strip()))
            except Exception:
                pass

    print(f"Total Closed Strategy Trades: {len(closed_trades)}")
    pnls = [t[2] for t in closed_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    
    total_pnl_usdt = sum(pnls)
    total_pnl_inr = total_pnl_usdt * 88.5
    win_rate = (len(wins) / len(closed_trades) * 100) if closed_trades else 0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else 0

    print(f"\n================================================ shadow")
    print(f"  📊 RECONCILED PNL METRICS")
    print(f"================================================ shadow")
    print(f"  - Total Trades Executed: {len(closed_trades)}")
    print(f"  - Wins: {len(wins)} | Losses: {len(losses)} | Win Rate: {win_rate:.1f}%")
    print(f"  - Gross Profit: +${gross_win:.2f} USDT")
    print(f"  - Gross Loss: -${gross_loss:.2f} USDT")
    print(f"  - Profit Factor: {pf:.2f}")
    print(f"  - NET REALIZED PNL (USDT): ${total_pnl_usdt:+.2f} USDT")
    print(f"  - NET REALIZED PNL (INR @ 88.5): ₹{total_pnl_inr:+,.2f} INR")

    print("\n--- ALL EXECUTED TRADES LIST ---")
    for t in closed_trades:
        sign = "+" if t[2] > 0 else ""
        print(f"  {t[0]} | {t[1]:<10} | PnL: {sign}${t[2]:.2f}")

print("\n===================================================================")
