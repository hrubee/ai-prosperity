#!/usr/bin/env python3
import sys, os
sys.path.insert(0, "/root/aiprosperity/backend")
from app.db import SessionLocal
from app.models import TradejiniConnection, Subscription, User
from app.crypto import decrypt_secret
from app import tradejini_auth, tradejini
from sqlalchemy import select

print("=" * 90)
print("🔑 TRADEJINI CLIENT ACCOUNT CONNECTION & TRADING READINESS AUDIT")
print("=" * 90)

with SessionLocal() as db:
    conns = db.execute(select(TradejiniConnection)).scalars().all()
    print(f"Found {len(conns)} Tradejini connection records in database.\n")
    
    for idx, c in enumerate(conns, 1):
        user = db.get(User, c.user_id)
        sub = db.query(Subscription).filter(Subscription.user_id == c.user_id).first()
        email = user.email if user else f"User ID {c.user_id}"
        
        has_creds = tradejini_auth.has_auto_creds(c)
        sub_status = sub.status if sub else "none"
        sub_end = sub.current_period_end if sub else "N/A"
        is_sub_valid = (sub is not None and sub.is_active)
        
        print(f"[{idx}] Account: {email}")
        print(f"    - DB Connection Status : {c.status} (Paused: {c.paused})")
        print(f"    - Auto-Creds Configured : {'YES' if has_creds else 'NO'}")
        print(f"    - Subscription Status  : {sub_status} | Valid: {'YES' if is_sub_valid else 'EXPIRED/NONE'} (End: {sub_end})")
        
        if not has_creds:
            print("    ❌ TRADE STATUS: CANNOT PLACE TRADES (Missing API Key / Password / TOTP seed)")
            print("-" * 90)
            continue
            
        try:
            tok = tradejini_auth.ensure_client_token(db, c)
            cl = tradejini.TradejiniClient(tok, api_key=c.api_key)
            
            # Fetch equity & buyable cash
            equity = cl.equity_inr()
            buyable = cl.buyable_cash_inr()
            positions = cl.open_positions()
            
            print(f"    - Daily Token Mint     : ✅ SUCCESS (Token Active)")
            print(f"    - Total Equity (Margin): ₹{equity:,.2f} INR")
            print(f"    - Buyable Option Cash  : ₹{buyable:,.2f} INR")
            print(f"    - Open Positions Count : {len(positions)}")
            
            if not is_sub_valid:
                print(f"    ⚠️ COPIER EXECUTION NOTE: Account credentials are valid, but subscription is '{sub_status}'. Copier will BLOCK new entries until subscription is activated.")
            else:
                print(f"    ✅ TRADE STATUS: 100% READY TO PLACE REAL TRADES")
        except Exception as e:
            print(f"    - Connection / Auth    : ❌ FAILED ({e})")
            print(f"    - Trade Status         : ❌ NOT READY")
            
        print("-" * 90)

print("=" * 90)
