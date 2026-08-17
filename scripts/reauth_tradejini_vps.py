#!/usr/bin/env python3
import sys, os, urllib.parse, urllib.request, json, time, ssl
sys.path.insert(0, "/root/aiprosperity/backend")
from app.db import SessionLocal
from app.models import TradejiniConnection, Subscription, User
from app.crypto import decrypt_secret
from app.config import settings
from app import tradejini_auth, tradejini
from sqlalchemy import select

print("=" * 90)
print("🔐 TRADEJINI VPS LIVE AUTHENTICATION & LOGIN AUDIT")
print("=" * 90)

_SSL = tradejini._SSL

with SessionLocal() as db:
    conns = db.execute(select(TradejiniConnection)).scalars().all()
    print(f"Total Accounts: {len(conns)}\n")
    
    for idx, c in enumerate(conns, 1):
        user = db.get(User, c.user_id)
        email = user.email if user else f"User ID {c.user_id}"
        
        print(f"[{idx}] Testing Account: {email}")
        print(f"    - API Key in DB : {c.api_key[:10] if c.api_key else 'None'}...")
        
        if not tradejini_auth.has_auto_creds(c):
            print("    ❌ Missing auto-login credentials in DB.\n" + "-" * 90)
            continue
            
        try:
            pw = decrypt_secret(c.password_encrypted)
            seed = decrypt_secret(c.totp_seed_encrypted)
            code = tradejini._totp_now(seed)
            print(f"    - Password Decrypted : {'*' * len(pw)} (len: {len(pw)})")
            print(f"    - TOTP Seed Decrypted: {'*' * 10}... (len: {len(seed)})")
            print(f"    - Generated TOTP Code: {code}")
            
            # Step 1: Call Tradejini individual-token-v2
            login_url = f"{settings.tradejini_base_url}/api-gw/oauth/individual-token-v2"
            body = urllib.parse.urlencode({
                "password": pw,
                "twoFa": code,
                "twoFaTyp": "totp"
            }).encode()
            
            req = urllib.request.Request(
                login_url,
                data=body,
                method="POST",
                headers={
                    "Authorization": f"Bearer {c.api_key}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json"
                }
            )
            
            print(f"    - Sending Auth Request to: {login_url}")
            try:
                resp_raw = urllib.request.urlopen(req, timeout=15, context=_SSL).read()
                resp_json = json.loads(resp_raw.decode())
                tok = resp_json.get("access_token")
                expires_in = resp_json.get("expires_in")
                print(f"    - Auth Response      : ✅ HTTP 200 OK | Token: {tok[:15]}... | Expires In: {expires_in}s")
                
                # Save newly minted token
                tradejini_auth.mint_and_store(db, c)
                print(f"    - Token Persisted in DB: ✅ YES (Status: connected)")
                
                # Step 2: Test OMS API limits
                cl = tradejini.TradejiniClient(tok, api_key=c.api_key)
                equity = cl.equity_inr()
                buyable = cl.buyable_cash_inr()
                positions = cl.open_positions()
                print(f"    - OMS Limits Handshake: ✅ HTTP 200 OK")
                print(f"    - Available Margin   : ₹{equity:,.2f} INR")
                print(f"    - Buyable Cash       : ₹{buyable:,.2f} INR")
                print(f"    - Open Positions     : {len(positions)}")
                print(f"    🎉 RESULT: AUTHENTICATION FULLY SUCCESSFUL!")
                
            except urllib.error.HTTPError as he:
                err_content = he.read().decode()
                print(f"    ❌ Auth Failed: HTTP {he.code}")
                print(f"       Response Body: {err_content}")
            except Exception as e:
                print(f"    ❌ Auth Request Error: {e}")
                
        except Exception as de:
            print(f"    ❌ Decryption / Prep Error: {de}")
            
        print("-" * 90)

print("=" * 90)
