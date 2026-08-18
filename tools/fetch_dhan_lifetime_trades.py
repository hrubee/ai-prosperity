#!/usr/bin/env python3
"""tools/fetch_dhan_lifetime_trades.py — Fetch all trades/orders ever executed on Dhan account."""
import os
import sys
import json
import csv
import subprocess

def main():
    fetch_code = """
import os, requests, json
from dotenv import load_dotenv
load_dotenv('/root/aiprosperity/backend/.env')
cid = os.getenv('DHAN_CLIENT_ID', '')
token = os.getenv('DHAN_ACCESS_TOKEN', '')

headers = {
    'access-token': token,
    'client-id': cid,
    'Content-Type': 'application/json',
    'Accept': 'application/json'
}

all_trades = []

# Fetch historical trades across 2024, 2025, 2026
for year in [2024, 2025, 2026]:
    for half in [(f'{year}-01-01', f'{year}-06-30'), (f'{year}-07-01', f'{year}-12-31')]:
        start_d, end_d = half
        page = 0
        while True:
            url = f'https://api.dhan.co/v2/trades/{start_d}/{end_d}/{page}'
            try:
                r = requests.get(url, headers=headers, timeout=10)
                if r.status_code != 200:
                    break
                data = r.json()
                if not isinstance(data, list) or len(data) == 0:
                    break
                all_trades.extend(data)
                page += 1
            except Exception:
                break

# Also fetch today's trades
try:
    r_today = requests.get('https://api.dhan.co/v2/trades', headers=headers, timeout=10)
    if r_today.status_code == 200:
        t_data = r_today.json()
        if isinstance(t_data, list):
            for td in t_data:
                all_trades.append(td)
except Exception:
    pass

print(json.dumps(all_trades))
"""
    print("Connecting to Dhan API via VPS bridge...", flush=True)
    proc = subprocess.run(
        ["ssh", "root@187.127.132.39", f"/root/aiprosperity/backend/.venv/bin/python3 -c '{fetch_code}'"],
        capture_output=True,
        text=True
    )
    
    try:
        raw_trades = json.loads(proc.stdout)
    except Exception as e:
        print(f"Error parsing response: {e}\nRaw output: {proc.stdout[:300]}")
        return

    # Deduplicate trades
    unique_trades = []
    seen = set()
    for t in raw_trades:
        oid = t.get('orderId')
        e_oid = t.get('exchangeOrderId')
        e_tid = t.get('exchangeTradeId')
        qty = t.get('tradedQuantity')
        px = t.get('tradedPrice')
        k = f"{oid}_{e_oid}_{e_tid}_{qty}_{px}"
        if k not in seen:
            seen.add(k)
            unique_trades.append(t)

    # Sort newest first
    def sort_key(x):
        return str(x.get('exchangeTime') or x.get('createTime') or '')
    unique_trades.sort(key=sort_key, reverse=True)

    csv_path = "/Users/hrushi/Desktop/ai-prosperity/dhan_all_lifetime_trades.csv"
    fieldnames = [
        "exchangeTime", "createTime", "orderId", "exchangeOrderId", "exchangeTradeId",
        "tradingSymbol", "customSymbol", "transactionType", "tradedQuantity", "tradedPrice",
        "exchangeSegment", "productType", "instrument", "drvExpiryDate", "drvOptionType", "drvStrikePrice",
        "brokerageCharges", "exchangeTransactionCharges", "sebiTax", "serviceTax", "stt", "stampDuty"
    ]

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in unique_trades:
            sym = t.get('customSymbol') or t.get('tradingSymbol')
            writer.writerow({
                "exchangeTime": t.get('exchangeTime'),
                "createTime": t.get('createTime'),
                "orderId": t.get('orderId'),
                "exchangeOrderId": t.get('exchangeOrderId'),
                "exchangeTradeId": t.get('exchangeTradeId'),
                "tradingSymbol": t.get('tradingSymbol') or sym,
                "customSymbol": t.get('customSymbol') or sym,
                "transactionType": t.get('transactionType'),
                "tradedQuantity": t.get('tradedQuantity'),
                "tradedPrice": t.get('tradedPrice'),
                "exchangeSegment": t.get('exchangeSegment'),
                "productType": t.get('productType'),
                "instrument": t.get('instrument'),
                "drvExpiryDate": t.get('drvExpiryDate'),
                "drvOptionType": t.get('drvOptionType'),
                "drvStrikePrice": t.get('drvStrikePrice'),
                "brokerageCharges": t.get('brokerageCharges', 0.0),
                "exchangeTransactionCharges": t.get('exchangeTransactionCharges', 0.0),
                "sebiTax": t.get('sebiTax', 0.0),
                "serviceTax": t.get('serviceTax', 0.0),
                "stt": t.get('stt', 0.0),
                "stampDuty": t.get('stampDuty', 0.0)
            })

    print(f"✅ Saved all {len(unique_trades)} lifetime trades to {csv_path}")

if __name__ == "__main__":
    main()
