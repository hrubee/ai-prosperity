import sys
import os
import json
import time

sys.path.insert(0, "/root/aiprosperity/backend")

from app.db import SessionLocal
from app.models import User, TradejiniConnection
from app.tradejini import TradejiniClient
from app.crypto import decrypt_secret

print("=========================================================================================")
print("🚨 SQUARE-OFF ENGINE: CHECKING & FLATTENING ALL POSITIONS ACROSS ALL TRADEJINI ACCOUNTS")
print("=========================================================================================")

with SessionLocal() as db:
    conns = db.query(TradejiniConnection).all()
    
    total_positions_closed = 0
    
    for conn in conns:
        user = db.query(User).filter(User.id == conn.user_id).first()
        email = getattr(user, "email", "Unknown") if user else "Unknown"
        name = getattr(user, "name", "") or email
        
        print(f"\n─────────────────────────────────────────────────────────────────────────")
        print(f"👤 USER: {name} | Email: {email} | Client Code: {conn.client_code}")
        
        token = ""
        if conn.access_token_encrypted:
            try:
                token = decrypt_secret(conn.access_token_encrypted)
            except Exception as e:
                print(f"  ❌ Token Decryption Error: {e}")
                
        api_key = conn.api_key or ""
        if not token:
            print("  ⚠️ Missing token. Skipping.")
            continue
            
        try:
            client = TradejiniClient(access_token=token, api_key=api_key)
            raw_pos = client._get("/api/oms/positions", {"symDetails": "true"})
            rows = (raw_pos or {}).get("d", []) if isinstance(raw_pos, dict) else (raw_pos if isinstance(raw_pos, list) else [])
            
            open_positions = []
            if rows:
                for r in rows:
                    sym_obj = r.get("sym") or {}
                    sym = sym_obj.get("tradSymbol") or r.get("symId") or "Unknown"
                    sym_id = r.get("symId") or sym_obj.get("id") or ""
                    netqty = float(r.get("netQty", 0) or 0)
                    buyqty = float(r.get("buyQty", 0) or 0)
                    sellqty = float(r.get("sellQty", 0) or 0)
                    
                    if abs(netqty) > 1e-6:
                        open_positions.append({
                            "symbol": sym,
                            "sym_id": sym_id,
                            "netqty": netqty,
                            "buyqty": buyqty,
                            "sellqty": sellqty
                        })
                        
            if not open_positions:
                print(f"  ✅ CONFIRMED 100% FLAT: Account has 0 open/naked positions. (All legs bought and sold are equal).")
            else:
                print(f"  🚨 FOUND {len(open_positions)} OPEN POSITIONS TO SQUARE OFF:")
                for p in open_positions:
                    side_to_place = "SELL" if p["netqty"] > 0 else "BUY"
                    qty_to_place = abs(int(p["netqty"]))
                    print(f"    -> Placing emergency {side_to_place} {qty_to_place} {p['symbol']} to flatten position...")
                    try:
                        # Place market order to close
                        order_payload = {
                            "symId": p["sym_id"],
                            "tradSymbol": p["symbol"],
                            "side": side_to_place,
                            "qty": str(qty_to_place),
                            "ordType": "MKT",
                            "prc": "0",
                            "prdType": "M",  # Margin / Intraday
                            "ret": "DAY"
                        }
                        res = client._post("/api/oms/orders", order_payload)
                        print(f"       ✅ Squareoff Order Placed: {res}")
                        total_positions_closed += 1
                    except Exception as sq_err:
                        print(f"       ❌ Failed to place squareoff order: {sq_err}")
                        
        except Exception as client_err:
            print(f"  ❌ Error interacting with account {email}: {client_err}")

print(f"\n=========================================================================================")
print(f"🏁 SQUARE-OFF SUMMARY: {total_positions_closed} positions required emergency square-off.")
print("=========================================================================================")
