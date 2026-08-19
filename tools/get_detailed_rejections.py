import sys
import os
import json

sys.path.insert(0, "/root/aiprosperity/backend")

from app.db import SessionLocal
from app.models import User, TradejiniConnection
from app.tradejini import TradejiniClient
from app.crypto import decrypt_secret

print("=========================================================================================")
print("🔍 DETAILED FORENSIC AUDIT: WHY TRADEJINI ORDERS REJECTED")
print("=========================================================================================")

with SessionLocal() as db:
    conns = db.query(TradejiniConnection).all()
    for conn in conns:
        user = db.query(User).filter(User.id == conn.user_id).first()
        email = getattr(user, "email", "Unknown") if user else "Unknown"
        name = getattr(user, "name", "") or email
        
        token = ""
        if conn.access_token_encrypted:
            try:
                token = decrypt_secret(conn.access_token_encrypted)
            except Exception:
                pass
        api_key = conn.api_key or ""
        if not token:
            continue
            
        try:
            client = TradejiniClient(access_token=token, api_key=api_key)
            raw_orders = client._get("/api/oms/orders")
            rows = (raw_orders or {}).get("d", []) if isinstance(raw_orders, dict) else (raw_orders if isinstance(raw_orders, list) else [])
            
            if rows:
                print(f"\n👤 CLIENT: {name} ({email}) | Total Orders: {len(rows)}")
                for o in rows:
                    stat = o.get("ordStatus") or o.get("status") or o.get("stat")
                    sym_obj = o.get("sym") or {}
                    sym = sym_obj.get("tradSymbol") or o.get("symId") or "Unknown"
                    side = o.get("side") or o.get("trantype") or "Unknown"
                    qty = o.get("qty") or o.get("quantity") or 0
                    fills = o.get("fillShares") or o.get("fillshares") or 0
                    price = o.get("limitPrice") or o.get("price") or 0
                    ord_type = o.get("orderType") or o.get("ordType") or "MKT"
                    rej_reason = o.get("rejReason") or o.get("rejreason") or o.get("text") or o.get("cancelRejectReason") or "N/A"
                    order_time = o.get("exchOrderTime") or o.get("ordenttm") or o.get("orderTime") or ""
                    
                    print(f"  • [{stat}] {side.upper()} {sym} | Type: {ord_type} | Qty: {qty} | Filled: {fills} | Price: ₹{price} | Time: {order_time}")
                    if stat in ("REJECTED", "CANCELLED", "REJ", "rejected") or rej_reason != "N/A":
                        print(f"    ⚠️ REJECTION CODE / MESSAGE: {rej_reason}")
                        print(f"    🔍 RAW ORDER OBJECT: {json.dumps(o)}")
        except Exception as e:
            print(f"❌ Error checking orders for {email}: {e}")

print("=========================================================================================")
