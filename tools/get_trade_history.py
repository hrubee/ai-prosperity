import sys
import os
import json
import time

sys.path.insert(0, "/root/trading-bot/crypto")
sys.path.insert(0, "/root/trading-bot/crypto/shared_scripts")

from stream_dumpride_coindcx import CoinDCXExchangeAdapter

adapter = CoinDCXExchangeAdapter()
print("Adapter initialized with key:", adapter.key[:8] + "...")

orders = adapter.auth_post("/exchange/v1/derivatives/futures/orders", {"page": 1, "size": 20})
trades = adapter.auth_post("/exchange/v1/derivatives/futures/trades", {"page": 1, "size": 20})

print("\n=========================================================================================")
print("📋 RECENT FUTURES ORDERS (COINDCX)")
print("=========================================================================================")
if isinstance(orders, list):
    for o in orders[:12]:
        created_dt = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(o.get('created_at', 0)/1000)) if o.get('created_at') else 'N/A'
        print(f"• [{o.get('status')}] {o.get('side', '').upper()} {o.get('pair')} | Qty: {o.get('total_quantity')} | AvgPx: ${o.get('avg_price')} | Trigger: ${o.get('trigger_price')} | Time: {created_dt}")
else:
    print(orders)

print("\n=========================================================================================")
print("💰 RECENT FUTURES TRADES & EXITS (COINDCX)")
print("=========================================================================================")
if isinstance(trades, list):
    for t in trades[:12]:
        created_dt = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t.get('created_at', 0)/1000)) if t.get('created_at') else 'N/A'
        print(f"• {t.get('side', '').upper()} {t.get('pair')} | Qty: {t.get('quantity')} | Price: ${t.get('price')} | Fee: {t.get('fee')} | Realized PnL: {t.get('realised_pnl')} | Time: {created_dt}")
else:
    print(trades)
print("=========================================================================================")
