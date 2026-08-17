#!/usr/bin/env python3
import requests, json, os, datetime, sys
sys.path.insert(0, "/root/aiprosperity/backend")
from app.db import SessionLocal
from app.models import DhanConnection, TradejiniConnection, Subscription, User
from app.crypto import decrypt_secret

print("=" * 80)
print("🔍 AI PROSPERITY POLLER & COPIER FULL DIAGNOSTIC AUDIT")
print("=" * 80)

# 1. Check Local Backend HTTP endpoints
try:
    r = requests.get("http://127.0.0.1:8000/api/copier-status", timeout=3)
    print(f"1. Backend /api/copier-status: {r.status_code} -> {r.json()}")
except Exception as e:
    print(f"1. Backend /api/copier-status: FAILED ({e})")

# 2. Check Copier Webhook Receiver
try:
    wh_payload = {"type": "status", "message": "Diagnostic health ping"}
    r = requests.post("http://127.0.0.1:8000/api/webhook", json=wh_payload, timeout=3)
    print(f"2. Backend /api/webhook ping: {r.status_code} -> {r.json()}")
except Exception as e:
    print(f"2. Backend /api/webhook ping: FAILED ({e})")

# 3. Test Master Dhan Token against DhanHQ API
print("\n3. Testing Master Dhan Credentials:")
with SessionLocal() as db:
    dhan_conn = db.query(DhanConnection).filter(
        DhanConnection.status == "connected",
        DhanConnection.access_token_encrypted.isnot(None)
    ).order_by(DhanConnection.id.desc()).first()
    
    if dhan_conn:
        try:
            token = decrypt_secret(dhan_conn.access_token_encrypted)
            client_id = dhan_conn.client_id
            headers = {
                "access-token": token,
                "client-id": client_id,
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            dhan_res = requests.get("https://api.dhan.co/v2/fundlimit", headers=headers, timeout=5)
            print(f"   Dhan Client ID: {client_id}")
            print(f"   Dhan API HTTP Status: {dhan_res.status_code}")
            if dhan_res.status_code == 200:
                fund_data = dhan_res.json()
                print(f"   ✅ DHAN POLLER TOKEN IS VALID! Available Balance: {fund_data.get('availabelBalance', 'N/A')}")
            elif dhan_res.status_code == 401:
                print(f"   ❌ DHAN TOKEN EXPIRED (401 Unauthorized). Updated on: {dhan_conn.updated_at}")
                print(f"      Response: {dhan_res.text}")
            else:
                print(f"   ⚠️ Dhan API returned: {dhan_res.status_code} - {dhan_res.text}")
        except Exception as de:
            print(f"   Dhan decrypt/test error: {de}")
    else:
        print("   ❌ No active Dhan connection found in database.")

# 4. Test Client Tradejini Tokens
print("\n4. Testing Client Tradejini Accounts:")
with SessionLocal() as db:
    tj_conns = db.query(TradejiniConnection).all()
    for c in tj_conns:
        user = db.query(User).filter(User.id == c.user_id).first()
        sub = db.query(Subscription).filter(Subscription.user_id == c.user_id).first()
        u_email = user.email if user else "Unknown"
        sub_status = sub.status if sub else "none"
        
        tj_token = ""
        if c.access_token_encrypted:
            try:
                tj_token = decrypt_secret(c.access_token_encrypted)
            except:
                pass
        
        has_token = "YES" if len(tj_token) > 10 else "NO"
        print(f"   Client: {u_email:<30} | TJ Status: {c.status:<10} | Has Token: {has_token:<4} | Sub: {sub_status:<8} | Paused: {c.paused}")

# 5. Check Log Monitor and Active WebSockets
print("\n5. Checking Copier Logs and WebSockets:")
try:
    log_res = requests.get("http://127.0.0.1:8000/api/logs", timeout=3)
    if log_res.status_code == 200:
        logs = log_res.json()
        print(f"   Backend Logs Count: {len(logs)} log entries available")
        if logs:
            last_log = logs[-1]
            print(f"   Most Recent Log Entry: {json.dumps(last_log)[:120]}...")
except Exception as le:
    print(f"   Failed to fetch logs: {le}")

print("=" * 80)
