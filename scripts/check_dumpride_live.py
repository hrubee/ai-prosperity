#!/usr/bin/env python3
import sys, os, json
sys.path.insert(0, "/root/trading-bot/crypto/shared_scripts")
from dotenv import load_dotenv
load_dotenv("/root/trading-bot/crypto/.env")
import coindcx_client as cdx

print("=" * 80)
print("🚀 COINDCX DUMPRIDE LIVE STATUS AUDIT")
print("=" * 80)

# Account 1
try:
    c1 = cdx.CoinDCXFuturesClient()
    b1 = c1.get_balance()
    pos1 = c1.get_active_positions()
    orders1 = c1.get_active_orders()
    bal1 = float(b1.get("total_balance_inr", 0) or 0)
    free1 = float(b1.get("available_balance_inr", 0) or 0)
    print(f"Account 1 (Primary):")
    print(f"  • Total Balance    : ₹{bal1:,.2f} INR")
    print(f"  • Free Available   : ₹{free1:,.2f} INR")
    print(f"  • Active Positions : {len(pos1)}")
    for p in pos1:
        pnl = float(p.get("unrealised_pnl_inr", 0) or 0)
        print(f"    -> Pair: {p.get('pair')} | Side: {p.get('side')} | Qty: {p.get('open_position')} | Entry: {p.get('avg_entry_price')} | Unrealized PnL: ₹{pnl:,.2f}")
    print(f"  • Open Bracket Orders: {len(orders1)}")
    for o in orders1:
        print(f"    -> ID: {o.get('order_id')} | Pair: {o.get('pair')} | Type: {o.get('order_type')} | Price: {o.get('price')}")
except Exception as e:
    print(f"Account 1 Check Error: {e}")

print("-" * 80)

# Account 2
try:
    key2 = os.environ.get("COINDCX_API_KEY_2", "")
    sec2 = os.environ.get("COINDCX_API_SECRET_2", "")
    if key2:
        c2 = cdx.CoinDCXFuturesClient(api_key=key2, api_secret=sec2)
        b2 = c2.get_balance()
        pos2 = c2.get_active_positions()
        orders2 = c2.get_active_orders()
        bal2 = float(b2.get("total_balance_inr", 0) or 0)
        free2 = float(b2.get("available_balance_inr", 0) or 0)
        print(f"Account 2 (Secondary):")
        print(f"  • Total Balance    : ₹{bal2:,.2f} INR")
        print(f"  • Free Available   : ₹{free2:,.2f} INR")
        print(f"  • Active Positions : {len(pos2)}")
        for p in pos2:
            pnl = float(p.get("unrealised_pnl_inr", 0) or 0)
            print(f"    -> Pair: {p.get('pair')} | Side: {p.get('side')} | Qty: {p.get('open_position')} | Entry: {p.get('avg_entry_price')} | Unrealized PnL: ₹{pnl:,.2f}")
        print(f"  • Open Bracket Orders: {len(orders2)}")
    else:
        print("Account 2: Not configured in .env")
except Exception as e:
    print(f"Account 2 Check Error: {e}")

print("=" * 80)
