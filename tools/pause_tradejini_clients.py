import sys
import os

sys.path.insert(0, "/root/aiprosperity/backend")

from app.db import SessionLocal
from app.models import User, TradejiniConnection

print("=========================================================================================")
print("⏸️ CONFIGURING TRADE COPIER: PAUSE ALL ACCOUNTS EXCEPT SAMEER SARANG")
print("=========================================================================================")

with SessionLocal() as db:
    conns = db.query(TradejiniConnection).all()
    
    for conn in conns:
        user = db.query(User).filter(User.id == conn.user_id).first()
        email = getattr(user, "email", "Unknown") if user else "Unknown"
        name = getattr(user, "name", "") or email
        
        # Check if Sameer Sarang
        is_sameer = ("sameer" in name.lower() or "samsar2001" in email.lower())
        
        if is_sameer:
            conn.paused = False
            print(f"🟢 [ACTIVE] User: {name} ({email}) -> PAUSED = FALSE (Trading Enabled)")
        else:
            conn.paused = True
            print(f"🔴 [PAUSED] User: {name} ({email}) -> PAUSED = TRUE (Trading Blocked)")
            
    db.commit()

    print("\n=========================================================================================")
    print("✅ DATABASE VERIFICATION: CURRENT STATUS OF ALL TRADEJINI CONNECTIONS")
    print("=========================================================================================")
    updated_conns = db.query(TradejiniConnection).all()
    for c in updated_conns:
        u = db.query(User).filter(User.id == c.user_id).first()
        u_email = getattr(u, "email", "Unknown") if u else "Unknown"
        u_name = getattr(u, "name", "") or u_email
        status_tag = "🟢 ACTIVE (UNPAUSED)" if not c.paused else "🔴 PAUSED"
        print(f"• {status_tag}: {u_name} ({u_email}) | Client Code: {c.client_code} | Connection Status: {c.status}")
print("=========================================================================================")
