import sys
import os
import json
import time

sys.path.insert(0, "/root/aiprosperity/backend")

from app.db import SessionLocal
from app.models import TradejiniClient, OrderLog
from app.tradejini_client import get_tradejini_client_for_account

print("=========================================================================================")
print("🔍 TRADEJINI LIVE CLIENT AUDIT & POSITION AUDIT (SAMEER SARANG & ALL CLIENTS)")
print("=========================================================================================")

with SessionLocal() as db:
    clients = db.query(TradejiniClient).all()
    for c in clients:
        print(f"\n👤 Client Account: {c.account_name} ({c.email}) | UCC: {c.ucc_id} | Status: {c.status}")
        try:
            tj = get_tradejini_client_for_account(c)
            
            # 1. Fetch Net Positions
            pos_resp = tj.get_positions()
            print(f"  • Raw Positions Response: {pos_resp}")
            
            # 2. Check for Open / Naked Positions
            if isinstance(pos_resp, list) and pos_resp:
                open_positions = []
                for p in pos_resp:
                    netqty = int(p.get("netqty", 0) or 0)
                    if netqty != 0:
                        open_positions.append(p)
                        print(f"    ⚠️ OPEN/NAKED POSITION DETECTED: {p.get('tsym')} | Net Qty: {netqty} | Buy Qty: {p.get('buyqty')} | Sell Qty: {p.get('sellqty')} | Avg Price: {p.get('netavgprc')}")
                if not open_positions:
                    print("    ✅ Net Positions: 0 (No open / naked positions. All bought/sold quantities are flat).")
            elif isinstance(pos_resp, dict) and pos_resp.get("stat") == "Not_Ok":
                print(f"    ℹ️ Positions API Status: {pos_resp.get('emsg', 'No positions found')}")
            else:
                print("    ✅ No active positions found on Tradejini.")
                
            # 3. Fetch Order Book
            ord_resp = tj.get_order_book()
            if isinstance(ord_resp, list):
                print(f"  • Orders Placed Today ({len(ord_resp)}):")
                for o in ord_resp:
                    status = o.get("status", o.get("stat", "UNKNOWN"))
                    tsym = o.get("tsym", "N/A")
                    side = o.get("trantype", "N/A")
                    qty = o.get("qty", "0")
                    fills = o.get("fillshares", "0")
                    rej = o.get("rejreason", "")
                    norenordno = o.get("norenordno", "")
                    print(f"    -> [{status}] {side} {tsym} | Qty: {qty} (Filled: {fills}) | OrderNo: {norenordno} | RejReason: {rej}")
            else:
                print(f"  • Order Book: {ord_resp}")
                
        except Exception as e:
            print(f"  ❌ Error querying Tradejini for {c.email}: {e}")

    print("\n=========================================================================================")
    print("📋 RECENT ORDER COPIER DATABASE LOGS (LAST 20 LOGS)")
    print("=========================================================================================")
    logs = db.query(OrderLog).order_by(OrderLog.id.desc()).limit(25).all()
    if logs:
        for l in logs:
            print(f"• [{l.created_at}] Client: {l.client_email} | Symbol: {l.trading_symbol} | Side: {l.transaction_type} | Qty: {l.quantity} | Status: {l.status} | Msg: {l.message}")
    else:
        print("No order logs recorded in database.")
    print("=========================================================================================")
