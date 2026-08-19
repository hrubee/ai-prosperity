import sys
import os
import json
import time

sys.path.insert(0, "/root/aiprosperity/backend")

from app.db import SessionLocal
from app.models import User, TradejiniConnection, ClientOrder
from app.tradejini import TradejiniClient
from app.crypto import decrypt_secret

print("=========================================================================================")
print("🚨 AUDITING TRADEJINI FOR SAMEER SARANG & ALL CONNECTED CLIENTS")
print("=========================================================================================")

with SessionLocal() as db:
    conns = db.query(TradejiniConnection).all()
    print(f"Total Tradejini Connections in DB: {len(conns)}")
    
    for conn in conns:
        user = db.query(User).filter(User.id == conn.user_id).first()
        email = getattr(user, "email", "Unknown") if user else "Unknown"
        name = getattr(user, "name", "") or email
        
        print(f"\n─────────────────────────────────────────────────────────────────────────")
        print(f"👤 USER: {name} | Email: {email} | Client Code: {conn.client_code} | Status: {conn.status} | Paused: {conn.paused}")
        
        token = ""
        if conn.access_token_encrypted:
            try:
                token = decrypt_secret(conn.access_token_encrypted)
            except Exception as e:
                print(f"  ❌ Token Decryption Error: {e}")
                
        api_key = conn.api_key or ""
        print(f"  • API Key Present: {bool(api_key)} | Token Present: {bool(token)} (Len: {len(token)})")
        
        if not token:
            print("  ⚠️ Missing token. Skipping live API call.")
            continue
            
        try:
            client = TradejiniClient(access_token=token, api_key=api_key)
            
            # 1. Fetch Limits / Margin
            try:
                equity = client.equity_inr()
                print(f"  • Live Equity / Available Cash: ₹{equity:,.2f}")
            except Exception as eq_err:
                print(f"  • Equity fetch err: {eq_err}")
                
            # 2. Fetch Raw Positions
            try:
                raw_pos = client._get("/api/oms/positions", {"symDetails": "true"})
                print(f"\n  📊 LIVE POSITIONS RESPONSE:")
                rows = (raw_pos or {}).get("d", []) if isinstance(raw_pos, dict) else (raw_pos if isinstance(raw_pos, list) else [])
                
                open_pos_count = 0
                if rows:
                    for r in rows:
                        sym_obj = r.get("sym") or {}
                        sym = sym_obj.get("tradSymbol") or r.get("symId") or "Unknown"
                        netqty = float(r.get("netQty", 0) or 0)
                        buyqty = float(r.get("buyQty", 0) or 0)
                        sellqty = float(r.get("sellQty", 0) or 0)
                        buyval = float(r.get("buyVal", 0) or 0)
                        sellval = float(r.get("sellVal", 0) or 0)
                        rpnl = float(r.get("realizedPnl", 0) or r.get("rpnl", 0) or 0)
                        upnl = float(r.get("unrealizedPnl", 0) or r.get("urmtom", 0) or 0)
                        
                        if abs(netqty) > 1e-6:
                            open_pos_count += 1
                            side = "🟢 LONG (BUY)" if netqty > 0 else "🔴 SHORT (SELL) [NAKED]"
                            print(f"    🚨🚨 OPEN / NAKED POSITION DETECTED: {sym}")
                            print(f"       ├─ Side        : {side}")
                            print(f"       ├─ Net Quantity: {netqty:,.0f} (Bought: {buyqty:,.0f}, Sold: {sellqty:,.0f})")
                            print(f"       ├─ Buy Value   : ₹{buyval:,.2f} | Sell Value: ₹{sellval:,.2f}")
                            print(f"       └─ Unrealized  : ₹{upnl:,.2f} | Realized: ₹{rpnl:,.2f}")
                        else:
                            print(f"    ✓ FLAT (Closed): {sym} | Bought: {buyqty:,.0f}, Sold: {sellqty:,.0f} | Net: 0 (Realized PnL: ₹{rpnl:,.2f})")
                else:
                    print("    ✅ No open or closed positions returned for today (Account is completely flat).")
                    
                if open_pos_count == 0 and rows:
                    print("    ✅ SUMMARY: Account has 0 open positions. All legs are completely flat.")
            except Exception as pe:
                print(f"    ❌ Positions error: {pe}")
                
            # 3. Fetch Orders Today
            try:
                raw_orders = client._get("/api/oms/orders")
                print(f"\n  📋 TODAY'S ORDERS ({len(raw_orders.get('d', [])) if isinstance(raw_orders, dict) else len(raw_orders) if isinstance(raw_orders, list) else 0}):")
                ord_rows = (raw_orders or {}).get("d", []) if isinstance(raw_orders, dict) else (raw_orders if isinstance(raw_orders, list) else [])
                if ord_rows:
                    for o in ord_rows:
                        status = o.get("ordStatus") or o.get("status") or o.get("stat") or "UNKNOWN"
                        sym_obj = o.get("sym") or {}
                        sym = sym_obj.get("tradSymbol") or o.get("symId") or "Unknown"
                        side = o.get("side") or o.get("trantype") or "Unknown"
                        qty = o.get("qty") or o.get("quantity") or 0
                        fills = o.get("fillShares") or o.get("fillshares") or 0
                        price = o.get("limitPrice") or o.get("price") or 0
                        rej = o.get("rejReason") or o.get("rejreason") or o.get("text") or ""
                        time_str = o.get("exchOrderTime") or o.get("ordenttm") or o.get("created_at") or ""
                        
                        flag = "🔴 REJECTED" if status in ("REJECTED", "CANCELLED", "REJ") else ("🟢 FILLED" if status in ("COMPLETE", "FILLED", "TRADED") else f"🟡 {status}")
                        print(f"    -> [{flag}] {side.upper()} {sym} | Qty: {qty} (Filled: {fills}) | Price: ₹{price} | Time: {time_str}")
                        if rej:
                            print(f"       ⚠️ REJECTION REASON: {rej}")
                else:
                    print("    No orders placed today.")
            except Exception as oe:
                print(f"    ❌ Orders error: {oe}")
                
        except Exception as client_err:
            print(f"  ❌ Client connection error: {client_err}")

    # 4. Check Client Orders recorded in database
    print(f"\n─────────────────────────────────────────────────────────────────────────")
    print(f"📋 COPIER DATABASE RECORD (ClientOrder Table - Recent Records):")
    client_orders = db.query(ClientOrder).order_by(ClientOrder.id.desc()).limit(20).all()
    if client_orders:
        for co in client_orders:
            print(f"  • ID: {co.id} | User: {co.user_id} | Symbol: {co.trading_symbol} | Side: {co.transaction_type} | Qty: {co.quantity} | Status: {co.status} | OrderId: {co.broker_order_id} | Err: {co.error_message}")
    else:
        print("  No records in ClientOrder table.")
print("=========================================================================================")
