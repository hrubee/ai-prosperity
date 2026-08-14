import os
import json
import time
import hmac
import hashlib
import urllib.request
from typing import Optional
from fastapi import APIRouter, Request, Depends, HTTPException, status
from pydantic import BaseModel

import jwt
from fastapi.security import HTTPAuthorizationCredentials
from . import db
from .models import User, CoinDCXConnection
from .crypto import encrypt_secret, decrypt_secret
from .config import settings
from .auth import _bearer

router = APIRouter(prefix="/api/coindcx", tags=["coindcx"])

class ConnectCoinDCXRequest(BaseModel):
    api_key: str
    api_secret: str
    user_id: Optional[str] = None

def validate_coindcx_credentials(api_key: str, api_secret: str) -> tuple[bool, str, float]:
    """Validates CoinDCX API key & secret via an authentic balance request."""
    try:
        secret_bytes = bytes(api_secret, 'utf-8')
        timeStamp = int(round(time.time() * 1000))
        body = {"timestamp": timeStamp}
        json_body = json.dumps(body, separators=(',', ':'))
        
        signature = hmac.new(secret_bytes, json_body.encode('utf-8'), hashlib.sha256).hexdigest()
        
        headers = {
            'Content-Type': 'application/json',
            'X-AUTH-APIKEY': api_key,
            'X-AUTH-SIGNATURE': signature
        }
        
        url = "https://api.coindcx.com/exchange/v1/users/balances"
        req = urllib.request.Request(url, data=json_body.encode('utf-8'), headers=headers, method='POST')
        
        with urllib.request.urlopen(req, timeout=10) as response:
            res_body = response.read().decode('utf-8')
            balances = json.loads(res_body)
            
            usdt_bal = 0.0
            if isinstance(balances, list):
                for b in balances:
                    if b.get("currency") == "USDT":
                        usdt_bal = float(b.get("balance", 0.0))
                        break
            return True, "Connection successful", usdt_bal
    except urllib.error.HTTPError as e:
        err_text = e.read().decode('utf-8') if e.fp else str(e)
        return False, f"CoinDCX API Authentication Failed ({e.code}): {err_text}", 0.0
    except Exception as e:
        return False, f"Validation error: {str(e)}", 0.0

@router.post("/connect")
def connect_coindcx(req: ConnectCoinDCXRequest, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    api_key = req.api_key.strip()
    api_secret = req.api_secret.strip()
    
    if not api_key or not api_secret:
        raise HTTPException(status_code=400, detail="API Key and API Secret are required")
        
    valid, msg, usdt_bal = validate_coindcx_credentials(api_key, api_secret)
    if not valid:
        raise HTTPException(status_code=400, detail=msg)
        
    encrypted_secret = encrypt_secret(api_secret)
    
    # Try to identify user via JWT token first
    jwt_user_id = None
    if creds and creds.credentials:
        try:
            payload = jwt.decode(creds.credentials, settings.jwt_secret, algorithms=["HS256"])
            jwt_user_id = payload.get("sub")
        except Exception:
            pass

    with db.session_scope() as session:
        user = None
        target_id = req.user_id or jwt_user_id
        if target_id:
            user = session.get(User, target_id)
        if not user:
            user = session.query(User).order_by(User.created_at.desc()).first()
            
        if not user:
            user = User(email="coindcx_client@aiprosperity.in", role="user")
            session.add(user)
            session.flush()
            
        conn = session.query(CoinDCXConnection).filter_by(user_id=user.id).one_or_none()
        if not conn:
            conn = CoinDCXConnection(
                user_id=user.id,
                api_key=api_key,
                api_secret_encrypted=encrypted_secret,
                status="connected",
                paused=False,
                balance_usdt=usdt_bal
            )
            session.add(conn)
        else:
            conn.api_key = api_key
            conn.api_secret_encrypted = encrypted_secret
            conn.status = "connected"
            conn.paused = False
            conn.balance_usdt = usdt_bal
            
        session.flush()
        return {
            "status": "success",
            "message": "CoinDCX account connected successfully",
            "user_id": user.id,
            "balance_usdt": usdt_bal
        }

@router.get("/status")
def get_coindcx_status(user_id: Optional[str] = None, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    jwt_user_id = None
    if creds and creds.credentials:
        try:
            payload = jwt.decode(creds.credentials, settings.jwt_secret, algorithms=["HS256"])
            jwt_user_id = payload.get("sub")
        except Exception:
            pass

    with db.session_scope() as session:
        conn = None
        target_id = user_id or jwt_user_id
        if target_id:
            conn = session.query(CoinDCXConnection).filter_by(user_id=target_id).one_or_none()
        else:
            conn = session.query(CoinDCXConnection).order_by(CoinDCXConnection.created_at.desc()).first()
            
        if not conn:
            return {"connected": False, "status": "disconnected"}
            
        return {
            "connected": conn.status == "connected",
            "status": conn.status,
            "paused": conn.paused,
            "api_key": conn.api_key[:6] + "..." + conn.api_key[-4:] if conn.api_key else "",
            "balance_usdt": conn.balance_usdt,
            "updated_at": conn.updated_at.isoformat() if conn.updated_at else None
        }

@router.post("/disconnect")
def disconnect_coindcx(user_id: Optional[str] = None):
    with db.session_scope() as session:
        conn = None
        if user_id:
            conn = session.query(CoinDCXConnection).filter_by(user_id=user_id).one_or_none()
        else:
            conn = session.query(CoinDCXConnection).order_by(CoinDCXConnection.created_at.desc()).first()
            
        if conn:
            conn.status = "disconnected"
            session.flush()
        return {"status": "success", "message": "CoinDCX account disconnected"}

@router.post("/toggle-pause")
def toggle_pause_coindcx(user_id: Optional[str] = None):
    with db.session_scope() as session:
        conn = None
        if user_id:
            conn = session.query(CoinDCXConnection).filter_by(user_id=user_id).one_or_none()
        else:
            conn = session.query(CoinDCXConnection).order_by(CoinDCXConnection.created_at.desc()).first()
            
        if not conn:
            raise HTTPException(status_code=404, detail="No CoinDCX connection found")
            
        conn.paused = not conn.paused
        session.flush()
        return {"status": "success", "paused": conn.paused}
