import sys
import os

sys.path.insert(0, "/root/aiprosperity/backend")

from app.db import SessionLocal
from app.models import User, TradejiniConnection

print("=========================================================================================")
print("▶️ CONFIGURING TRADE COPIER: UNPAUSE TRADING FOR ALL CLIENT ACCOUNTS")
print("=========================================================================================")

with SessionLocal() as db:
    conns = db.query(TradejiniConnection).all()
    
    for conn in conns:
        user = db.query(User).filter(User.id == conn.user_id).first()
        email = getattr(user, "email", "Unknown") if user else "Unknown"
        name = getattr(user, "name", "") or email
        
        conn.paused = False
        print(f"🟢 [UNPAUSED] User: {name} ({email}) -> PAUSED = FALSE (Trading Enabled)")
            
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
        print(f"• {status_tag}: {u_name} ({u_email}) | Client Code: {c.client_code} | Connection: {c.status}")
print("=========================================================================================")
