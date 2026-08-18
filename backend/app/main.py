"""FastAPI app (Phase 2 entrypoint).

Run: uvicorn app.main:app --host 0.0.0.0 --port 8000
Caddy proxies app.diffraction.in/api/* → here.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, RedirectResponse
from typing import Optional
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .dhan_poller import run_poller_async
import asyncio
from . import auth, dodo, orderbook, screener, signal_bus, tradejini, tradejini_auth, vol2b2t, volcontinuation, copier, coindcx_copier
from .config import settings
from .crypto import decrypt_secret, encrypt_secret
from .db import get_db
from .delta import DeltaClient, DeltaError
from .models import BrainEvent, ClientOrder, ClientProfitLedger, DeltaConnection, DhanConnection, CoinDCXConnection, PaymentScreenshot, PricingPlan, Signal, Subscription, TradejiniConnection, User, EntitlementCounter, StraddlePosition
from .packages import PACKAGES

log = logging.getLogger("api")
app = FastAPI(title="AI Prosperity API", version="0.1.0")

app.include_router(vol2b2t.router, prefix="/vol2b2t")
app.include_router(volcontinuation.router, prefix="/volcontinuation")
app.include_router(copier.router)
app.include_router(coindcx_copier.router)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(run_poller_async())

@app.get("/plans")
def get_plans(db: Session = Depends(get_db)):
    """Return active pricing plans formatted as a dictionary keyed by months for the frontend."""
    plans = db.query(PricingPlan).filter(PricingPlan.is_active == True).all()
    # The frontend expects plansObj: Record<string, { name: string; price_inr: number; months: number }>
    return {str(p.months): {"name": p.name, "price_inr": p.price_inr, "months": p.months} for p in plans}

import base64
@app.get("/payment/qr")
def get_payment_qr():
    """Return the UPI QR code base64."""
    qr_path = os.path.join(os.path.dirname(__file__), "..", "qr.jpeg")
    try:
        with open(qr_path, "rb") as f:
            qr_base64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        qr_base64 = settings.upi_qr_base64

    if not qr_base64:
        raise HTTPException(status_code=404, detail="QR code not found")

    return {
        "qr_base64": qr_base64,
        "amount_inr": settings.upi_qr_amount_inr,
        "name": settings.upi_qr_name,
        "upi_id": settings.upi_qr_upi_id
    }


class ScreenshotUploadRequest(BaseModel):
    image_b64: str
    mime_type: str

@app.post("/payment/upload-screenshot")
def upload_screenshot(body: ScreenshotUploadRequest, user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    from datetime import datetime, timezone
    
    screenshot = db.query(PaymentScreenshot).filter(PaymentScreenshot.user_id == user.id).one_or_none()
    
    if screenshot:
        screenshot.image_b64 = body.image_b64
        screenshot.mime_type = body.mime_type
        screenshot.status = "pending"
        screenshot.review_note = None
        screenshot.reviewed_by = None
        screenshot.reviewed_at = None
        screenshot.created_at = datetime.now(timezone.utc)
    else:
        screenshot = PaymentScreenshot(
            user_id=user.id,
            image_b64=body.image_b64,
            mime_type=body.mime_type,
            status="pending"
        )
        db.add(screenshot)
    
    if user.payment_status != "approved":
        user.payment_status = "pending_verification"
        
    db.commit()
    return {"ok": True, "status": "pending_verification"}


@app.delete("/payment/screenshot")
def delete_payment_screenshot(user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    screenshot = db.query(PaymentScreenshot).filter(PaymentScreenshot.user_id == user.id).one_or_none()
    if screenshot:
        db.delete(screenshot)
        if user.payment_status == "pending_verification":
            user.payment_status = "pending"
        db.commit()
    return {"ok": True}


@app.get("/payment/status")
def get_payment_status(user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    screenshot = db.query(PaymentScreenshot).filter(PaymentScreenshot.user_id == user.id).one_or_none()
    
    screenshot_data = None
    if screenshot:
        screenshot_data = {
            "id": screenshot.id,
            "status": screenshot.status,
            "image_b64": screenshot.image_b64,
            "mime_type": screenshot.mime_type,
            "review_note": screenshot.review_note
        }
        
    return {
        "payment_status": user.payment_status,
        "screenshot": screenshot_data
    }


app.add_middleware(
    CORSMiddleware,
    # Accept both the old and new domains during the diffraction→aiprosperity
    # migration so neither breaks mid-cutover.
    allow_origins=sorted({
        settings.app_origin,
        "https://app.aiprosperity.in",
        "https://app.diffraction.in",
        "http://localhost:3000",
    }),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── schemas ────────────────────────────────────────────────
class RegisterRequest(BaseModel):
    name: str = ""
    email: EmailStr
    phone: str = ""
    password: str
    client_id: Optional[str] = None


class LoginRequest(BaseModel):
    identifier: str  # email or phone
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ConnectRequest(BaseModel):
    api_key: str
    api_secret: str
    sandbox: bool = False  # real clients connect LIVE; testnet only for canary/testing


class TradejiniConnectRequest(BaseModel):
    api_key: str
    password: str
    totp_seed: str

class DhanConnectRequest(BaseModel):
    client_id: Optional[str] = None
    access_token: str  # base32 TOTP secret — stored encrypted for daily AUTO-login


class CheckoutRequest(BaseModel):
    package: str


# ── health ─────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"ok": True, "status": "pending_verification"}


@app.delete("/payment/screenshot")
def delete_payment_screenshot(user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    screenshot = db.query(PaymentScreenshot).filter(PaymentScreenshot.user_id == user.id).one_or_none()
    if screenshot:
        db.delete(screenshot)
        if user.payment_status == "pending_verification":
            user.payment_status = "pending"
        db.commit()
    return {"ok": True}


@app.get("/payment/status")
def get_payment_status(user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    screenshot = db.query(PaymentScreenshot).filter(PaymentScreenshot.user_id == user.id).one_or_none()
    
    screenshot_data = None
    if screenshot:
        screenshot_data = {
            "id": screenshot.id,
            "status": screenshot.status,
            "image_b64": screenshot.image_b64,
            "mime_type": screenshot.mime_type,
            "review_note": screenshot.review_note
        }
        
    return {
        "payment_status": user.payment_status,
        "screenshot": screenshot_data
    }


# ── auth ───────────────────────────────────────────────────
@app.post("/auth/register")
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    """Sign up a NEW user (name + email + phone + password + client_id)."""
    user = auth.register(db, body.name, body.email, body.phone, body.password, body.client_id)
    return {"token": auth.make_jwt(user), "email": user.email, "role": user.role, "name": user.name, "client_id": user.client_id}


@app.post("/auth/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    """Returning user: email OR phone + password."""
    user = auth.login(db, body.identifier, body.password)
    return {"token": auth.make_jwt(user), "email": user.email, "role": user.role, "name": user.name, "client_id": user.client_id}


@app.post("/auth/change-password")
def change_password(body: ChangePasswordRequest, user: User = Depends(auth.current_user),
                    db: Session = Depends(get_db)):
    auth.change_password(db, user, body.current_password, body.new_password)
    return {"ok": True}


# ── account ────────────────────────────────────────────────
@app.get("/me")
def me(user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    sub = db.query(Subscription).filter(Subscription.user_id == user.id).one_or_none()
    conn = db.query(DeltaConnection).filter(DeltaConnection.user_id == user.id).one_or_none()
    effective_status = user.payment_status or "approved"
    return {
        "email": user.email,
        "name": user.name,
        "phone": user.phone,
        "client_id": user.client_id,
        "role": user.role,
        "payment_status": effective_status,
        "subscription": {"package": "pro", "status": "active"} if sub is None else {"package": sub.package, "status": sub.status},
        "connection": None if conn is None else {"status": conn.status, "paused": conn.paused},
    }


@app.get("/me/positions")
def my_positions(user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    """Live equity + open positions from the client's own Delta account."""
    conn = db.query(DeltaConnection).filter(DeltaConnection.user_id == user.id).one_or_none()
    if conn is None or conn.status != "connected":
        return {"connected": False, "equity": 0.0, "positions": []}
    try:
        client = DeltaClient(conn.api_key, decrypt_secret(conn.api_secret_encrypted), sandbox=conn.sandbox)
        return {"connected": True, "equity": client.equity_usd(), "positions": client.open_positions()}
    except DeltaError as e:
        return {"connected": True, "equity": 0.0, "positions": [], "error": str(e)}


@app.get("/me/orders")
def my_orders(user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    """Recent execution attempts for this client — drives the signal feed."""
    rows = (
        db.query(ClientOrder, Signal)
        .join(Signal, Signal.id == ClientOrder.signal_id)
        .filter(ClientOrder.user_id == user.id)
        .order_by(ClientOrder.created_at.desc())
        .limit(25)
        .all()
    )
    return [
        {
            "symbol": s.symbol,
            "side": s.side,
            "status": co.status,
            "detail": co.detail,
            "size": co.size,
            "fill_px": co.fill_px,
            "at": co.created_at.isoformat() if co.created_at else None,
        }
        for (co, s) in rows
    ]


# ── Delta connection ───────────────────────────────────────
@app.post("/connect")
def connect(body: ConnectRequest, user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    # Validate against Delta in the requested mode (live for real clients). This
    # also proves our IP is whitelisted on the key. A real key validated in
    # sandbox would fail (and vice-versa), which is the correct guard.
    try:
        DeltaClient(body.api_key, body.api_secret, sandbox=body.sandbox).validate()
    except DeltaError as e:
        msg = str(e)
        hint = " — make sure our IP is whitelisted on the key" if "ip_not_whitelisted" in msg else ""
        raise HTTPException(status_code=400, detail=f"Delta validation failed: {msg}{hint}")

    conn = db.query(DeltaConnection).filter(DeltaConnection.user_id == user.id).one_or_none()
    enc = encrypt_secret(body.api_secret)
    if conn is None:
        conn = DeltaConnection(user_id=user.id, api_key=body.api_key, api_secret_encrypted=enc, sandbox=body.sandbox)
        db.add(conn)
    else:
        conn.api_key = body.api_key
        conn.api_secret_encrypted = enc
        conn.status = "connected"
        conn.paused = False
        conn.sandbox = body.sandbox
    db.flush()
    return {"status": "connected"}


@app.post("/connection/pause")
def pause(paused: bool = True, user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    conn = db.query(DeltaConnection).filter(DeltaConnection.user_id == user.id).one_or_none()
    if conn is None:
        raise HTTPException(status_code=404, detail="no connection")
    conn.paused = paused
    return {"paused": conn.paused}


@app.post("/disconnect")
def disconnect(user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    conn = db.query(DeltaConnection).filter(DeltaConnection.user_id == user.id).one_or_none()
    if conn is not None:
        conn.status = "disconnected"
        conn.paused = True
    return {"status": "disconnected"}


# ── Tradejini (Indian F&O) — per-client AUTO-login (connect ONCE) ──
@app.post("/tradejini/connect")
def tradejini_connect(body: TradejiniConnectRequest, user: User = Depends(auth.current_user),
                      db: Session = Depends(get_db)):
    """Connect ONCE. The client creates their own Tradejini app (whitelisting our
    backend IP) and supplies api_key + password + TOTP seed (base32). We validate
    by minting a token immediately, then store all three (Fernet-encrypted) and
    auto-mint a fresh token every day thereafter — the client NEVER reconnects."""
    api_key = body.api_key.strip()
    pw = body.password
    seed = body.totp_seed.strip().replace(" ", "")
    if not (api_key and pw and seed):
        log.info("tradejini connect for %s rejected: missing fields (api_key=%s pin=%s seed=%s)",
                 user.id, bool(api_key), bool(pw), bool(seed))
        raise HTTPException(status_code=400, detail="API key, login PIN and TOTP seed are all required")
    # validate immediately: generate a TOTP from the seed and mint a real token.
    try:
        code = tradejini._totp_now(seed)
    except Exception as e:  # not a valid base32 seed (e.g. the 6-digit CODE was pasted)
        log.warning("tradejini connect for %s: invalid TOTP seed (len=%d, api_key_len=%d): %s",
                    user.id, len(seed), len(api_key), e)
        raise HTTPException(status_code=400,
                            detail="That doesn't look like a TOTP seed. Paste the base32 'setup key' from your authenticator (e.g. JBSWY3DPEHPK3PXP), not the 6-digit code.")
    try:
        tok, expires_in = tradejini.mint_client_token(api_key, pw, code, "totp")
        tradejini.TradejiniClient(tok, api_key=api_key).validate()
    except tradejini.TradejiniError as e:
        log.warning("tradejini connect failed for %s (api_key_len=%d, pin_len=%d, seed_len=%d): %s",
                    user.id, len(api_key), len(pw), len(seed), e)  # lengths only, never values
        raise HTTPException(status_code=400, detail=f"Tradejini login failed: {e}")
    conn = db.query(TradejiniConnection).filter(TradejiniConnection.user_id == user.id).one_or_none()
    if conn is None:
        conn = TradejiniConnection(user_id=user.id, access_token_encrypted="")
        db.add(conn)
    conn.api_key = api_key
    conn.password_encrypted = encrypt_secret(pw)
    conn.totp_seed_encrypted = encrypt_secret(seed)
    conn.access_token_encrypted = encrypt_secret(tok)
    conn.status = "connected"
    conn.paused = False
    conn.expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in or 86400))
    db.commit()
    return {"connected": True, "expires_at": conn.expires_at.isoformat()}


@app.get("/me/tradejini")
def my_tradejini(user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    conn = db.query(TradejiniConnection).filter(TradejiniConnection.user_id == user.id).one_or_none()
    if conn is None:
        return {"connected": False, "connected_once": False}
    connected_once = tradejini_auth.has_auto_creds(conn) and conn.status != "disconnected"
    equity = 0.0
    positions: list = []
    err = None
    if connected_once:
        try:
            # auto-mint a fresh token if the current one lapsed — client never re-auths.
            tok = tradejini_auth.ensure_client_token(db, conn)
            client = tradejini.TradejiniClient(tok, api_key=conn.api_key)
            equity = client.equity_inr()
            positions = client.open_positions()
        except tradejini.TradejiniError as e:
            err = str(e)
    return {
        "connected": bool(connected_once and not err),
        "status": conn.status,
        "connected_once": connected_once,
        "paused": conn.paused,
        "expires_at": conn.expires_at.isoformat() if conn.expires_at else None,
        "equity_inr": equity,
        "positions": positions,
        "error": err,
    }


@app.get("/kite/callback")
def kite_callback(request_token: str = "", status: str = ""):
    """One-time Kite app authorization landing. Kite redirects here after you
    approve the app; the request_token is consumed server-side on the next
    auto-login, so this just confirms success to the operator."""
    from fastapi.responses import HTMLResponse
    ok = bool(request_token) and status == "success"
    msg = ("✓ Kite app authorized. The NIFTY brain can now log in automatically — "
           "you can close this tab.") if ok else \
          ("Authorization did not complete. Re-open the connect URL and approve the app.")
    return HTMLResponse(
        f"<html><body style='font-family:system-ui;background:#0b0d12;color:#e8edf2;"
        f"display:grid;place-items:center;height:100vh;margin:0'>"
        f"<div style='text-align:center;max-width:420px'>"
        f"<h2 style='color:{'#37d39b' if ok else '#ef6b73'}'>{'Authorized' if ok else 'Not completed'}</h2>"
        f"<p style='color:#9aa6b2'>{msg}</p></div></body></html>")


@app.post("/tradejini/pause")
def tradejini_pause(paused: bool = True, user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    conn = db.query(TradejiniConnection).filter(TradejiniConnection.user_id == user.id).one_or_none()
    if conn is None:
        raise HTTPException(status_code=404, detail="no tradejini connection")
    conn.paused = paused
    return {"paused": conn.paused}


@app.post("/tradejini/disconnect")
def tradejini_disconnect(user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    conn = db.query(TradejiniConnection).filter(TradejiniConnection.user_id == user.id).one_or_none()
    if conn is not None:
        conn.status = "disconnected"
        conn.paused = True
    return {"status": "disconnected"}


@app.post("/admin/dhan/connect")
@app.post("/admin/dhan/update-token")
def dhan_connect(body: DhanConnectRequest, _: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    client_id = body.client_id.strip() if body.client_id else ""
    access_token = body.access_token.strip()
    
    if not access_token:
        raise HTTPException(status_code=400, detail="Access Token is required")
        
    # Test token against Dhan API
    try:
        import httpx
        headers = {
            "access-token": access_token,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        if client_id:
            headers["client-id"] = client_id
        r = httpx.get("https://api.dhan.co/v2/orders", headers=headers, timeout=5.0)
        if r.status_code == 401:
            raise HTTPException(status_code=400, detail="Invalid or expired Dhan Access Token (HTTP 401 Unauthorized)")
    except HTTPException:
        raise
    except Exception as e:
        log.warning("Dhan token validation check error: %s", e)

    conn = db.query(DhanConnection).filter(DhanConnection.status == "connected").first()
    if conn is None:
        conn = db.query(DhanConnection).first()
    if conn is None:
        conn = DhanConnection(api_key=client_id, client_id=client_id, access_token_encrypted="")
        db.add(conn)
        
    if client_id:
        conn.client_id = client_id
        conn.api_key = client_id
    conn.access_token_encrypted = encrypt_secret(access_token)
    conn.status = "connected"
    conn.paused = False
    db.commit()

    # Synchronize .env file on disk
    try:
        env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                lines = f.readlines()
            new_lines = []
            has_tok = False
            has_cid = False
            for l in lines:
                if l.startswith("DHAN_ACCESS_TOKEN="):
                    new_lines.append(f'DHAN_ACCESS_TOKEN="{access_token}"\n')
                    has_tok = True
                elif client_id and l.startswith("DHAN_CLIENT_ID="):
                    new_lines.append(f'DHAN_CLIENT_ID="{client_id}"\n')
                    has_cid = True
                else:
                    new_lines.append(l)
            if not has_tok:
                new_lines.append(f'DHAN_ACCESS_TOKEN="{access_token}"\n')
            if client_id and not has_cid:
                new_lines.append(f'DHAN_CLIENT_ID="{client_id}"\n')
            with open(env_path, "w") as f:
                f.writelines(new_lines)
    except Exception as e:
        log.warning("Could not sync .env file: %s", e)

    return {"connected": True, "ok": True}


@app.get("/admin/dhan")
def get_dhan_accounts(_: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    conns = db.query(DhanConnection).all()
    return [{
        "client_id": c.client_id,
        "status": c.status,
        "paused": c.paused,
    } for c in conns]


@app.post("/admin/dhan/{client_id}/pause")
def dhan_pause(client_id: str, paused: bool = True, _: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    conn = db.query(DhanConnection).filter(DhanConnection.client_id == client_id).one_or_none()
    if conn is None:
        raise HTTPException(status_code=404, detail="Dhan connection not found")
    conn.paused = paused
    db.commit()
    return {"paused": conn.paused}


@app.post("/admin/dhan/{client_id}/disconnect")
def dhan_disconnect(client_id: str, _: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    conn = db.query(DhanConnection).filter(DhanConnection.client_id == client_id).one_or_none()
    if conn is not None:
        db.delete(conn)
        db.commit()
    return {"status": "deleted"}


@app.get("/tradejini/webhook")
def tradejini_webhook_verify(challenge: str = ""):
    """Tradejini app-registration handshake: on Submit it GETs this URL with
    ?challenge=<x> and the endpoint must echo <x> back as plain text, else app
    registration fails. (Registering a User Based app for client onboarding.)"""
    return PlainTextResponse(challenge)


@app.post("/tradejini/webhook")
async def tradejini_webhook_event(request: Request):
    """Order-event postbacks (open/rejected/cancelled/completed) keyed by client
    code (userId). Signed HMAC-SHA256(api_secret, body) in X-SIGNATURE. We only
    LOG for now (no state mutation), so we accept (200) even if a signature does
    not verify — keeping the webhook healthy — but record the verify result."""
    raw = await request.body()
    sig = request.headers.get("x-signature")
    verified = tradejini.verify_webhook_signature(raw, sig)
    import json
    try:
        evt = json.loads(raw.decode() or "{}")
    except Exception:
        evt = {}
    log.info(
        "tradejini webhook evt=%s status=%s sym=%s orderId=%s client=%s verified=%s",
        evt.get("evntType"), evt.get("status"), evt.get("trdSym"),
        evt.get("orderId"), evt.get("userId"), verified)
    return {"received": True}


# ── subscriptions / payments ───────────────────────────────
@app.get("/packages")
def packages():
    return {pid: {"name": p.name, "price_inr": p.price_inr} for pid, p in PACKAGES.items()}



class AdminApprovePaymentRequest(BaseModel):
    approve: bool
    note: str | None = None

@app.get("/admin/approvals")
def admin_get_approvals(admin: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    # Fetch all registered non-admin users so admin can view, approve, and revoke access for any client
    all_users = db.query(User).filter(User.role != "admin").order_by(User.created_at.desc()).all()
    
    screenshots = db.query(PaymentScreenshot).order_by(PaymentScreenshot.created_at.desc()).all()
    ss_map = {s.user_id: s for s in screenshots}
    
    res = []
    for u in all_users:
        s = ss_map.get(u.id)
        res.append({
            "user_id": u.id,
            "name": u.name,
            "email": u.email,
            "phone": u.phone,
            "client_id": u.client_id,
            "payment_status": u.payment_status,
            "screenshot": {
                "status": s.status,
                "mime_type": s.mime_type,
                "image_b64": s.image_b64,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "reviewed_at": s.reviewed_at.isoformat() if s.reviewed_at else None,
                "review_note": s.review_note
            } if s else None
        })
        
    return res

@app.post("/admin/approve-payment/{user_id}")
def admin_approve_payment(
    user_id: str,
    req: AdminApprovePaymentRequest,
    admin: User = Depends(auth.require_admin),
    db: Session = Depends(get_db)
):
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
        
    screenshot = db.query(PaymentScreenshot).filter(PaymentScreenshot.user_id == user_id).first()
    
    status_str = "approved" if req.approve else "rejected"
    target_user.payment_status = status_str
    
    if screenshot:
        screenshot.status = status_str
        screenshot.reviewed_by = admin.id
        import datetime
        screenshot.reviewed_at = datetime.datetime.utcnow()
        screenshot.review_note = req.note
        
    db.commit()
    return {"ok": True, "status": status_str}


@app.post("/checkout")
def checkout(body: CheckoutRequest, user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    plan = db.query(PricingPlan).filter(PricingPlan.months == int(body.package), PricingPlan.is_active == True).one_or_none()
    if not plan:
        raise HTTPException(status_code=400, detail="unknown package")
    # Pre-create a pending subscription so the webhook can attribute it.
    sub = db.query(Subscription).filter(Subscription.user_id == user.id).one_or_none()
    if sub is None:
        sub = Subscription(user_id=user.id, package=body.package, status="pending")
        db.add(sub)
    else:
        sub.package = body.package
    db.flush()
    db.commit()
    return {"checkout_url": None, "status": "pending_manual_payment"}


@app.post("/webhooks/dodo")
async def dodo_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    if not dodo.verify_webhook(raw, headers):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad signature")
    import json

    dodo.handle_event(db, json.loads(raw.decode()))
    return {"received": True}


# ── admin ──────────────────────────────────────────────────
@app.get("/admin/stats")
def admin_stats(_: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    """How many clients bought, broken down by plan + MRR."""
    total_clients = db.query(func.count(User.id)).scalar() or 0
    rows = (
        db.query(Subscription.package, Subscription.status, func.count())
        .group_by(Subscription.package, Subscription.status)
        .all()
    )
    by_package = {
        pid: {"name": p.name, "price_inr": p.price_inr, "active": 0, "total": 0}
        for pid, p in PACKAGES.items()
    }
    active_subscribers = 0
    mrr_inr = 0
    for pkg, st, cnt in rows:
        if pkg not in by_package:
            continue
        by_package[pkg]["total"] += cnt
        if st == "active":
            by_package[pkg]["active"] += cnt
            active_subscribers += cnt
            mrr_inr += PACKAGES[pkg].price_inr * cnt
    return {
        "total_clients": total_clients,
        "active_subscribers": active_subscribers,
        "mrr_inr": mrr_inr,
        "by_package": by_package,
    }



@app.get("/admin/dhan/token-status")
def dhan_token_status(_: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    conn = db.query(DhanConnection).filter(DhanConnection.status == "connected").first()
    if not conn or not conn.access_token_encrypted:
        return {"is_valid": False}
    try:
        from .crypto import decrypt_secret
        token = decrypt_secret(conn.access_token_encrypted)
    except:
        token = None
        
    if not token:
        return {"is_valid": False}
        
    is_valid = True
    try:
        import httpx
        headers = {
            "access-token": token,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        if conn.client_id:
            headers["client-id"] = conn.client_id
        r = httpx.get("https://api.dhan.co/v2/orders", headers=headers, timeout=4.0)
        if r.status_code == 401:
            is_valid = False
    except Exception:
        pass

    return {
        "is_valid": is_valid,
        "client_id": conn.client_id,
        "access_token": token
    }

@app.get("/admin/clients")
def admin_clients(_: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    rows = (
        db.query(User, Subscription, DeltaConnection, TradejiniConnection)
        .outerjoin(Subscription, Subscription.user_id == User.id)
        .outerjoin(DeltaConnection, DeltaConnection.user_id == User.id)
        .outerjoin(TradejiniConnection, TradejiniConnection.user_id == User.id)
        .all()
    )

    client_list = []
    for (u, s, c, t) in rows:
        buyable_cash = None
        lot_mult = float(t.lot_multiplier) if (t and t.lot_multiplier is not None) else 1.0

        if t and tradejini_auth.has_auto_creds(t) and t.status == "connected":
            try:
                tok = tradejini_auth.ensure_client_token(db, t)
                tjc = tradejini.TradejiniClient(tok, api_key=t.api_key)
                buyable_cash = tjc.buyable_cash_inr()
            except Exception:
                buyable_cash = None

        client_list.append({
            "id": u.id,
            "email": u.email,
            "name": u.name,
            "phone": u.phone,
            "client_id": u.client_id,
            "package": s.package if s else "pro",
            "subscription": s.status if s else "active",
            "payment_status": u.payment_status or "approved",
            "connection": c.status if c else None,
            "paused": c.paused if c else None,
            "sandbox": c.sandbox if c else None,
            "tradejini": t.status if t else None,
            "lot_multiplier": lot_mult,
            "buyable_cash_inr": buyable_cash,
        })
    return client_list


class SetLotMultiplierRequest(BaseModel):
    lot_multiplier: float

@app.post("/admin/clients/{user_id}/lot-multiplier")
def admin_set_lot_multiplier(
    user_id: str,
    body: SetLotMultiplierRequest,
    _: User = Depends(auth.require_admin),
    db: Session = Depends(get_db)
):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="Client not found")
    
    t = db.query(TradejiniConnection).filter_by(user_id=u.id).one_or_none()
    mult = max(0.1, round(float(body.lot_multiplier), 2))
    if t is None:
        t = TradejiniConnection(
            user_id=u.id,
            api_key=None,
            access_token_encrypted="",
            status="disconnected",
            lot_multiplier=mult
        )
        db.add(t)
    else:
        t.lot_multiplier = mult
        db.add(t)
    
    db.commit()
    return {
        "result": f"Lot multiplier updated to {mult}x for {u.email}",
        "lot_multiplier": mult
    }


def _admin_conn(db: Session, user_id: str) -> DeltaConnection:
    conn = db.query(DeltaConnection).filter(DeltaConnection.user_id == user_id).one_or_none()
    if conn is None:
        raise HTTPException(status_code=404, detail="client has no Delta connection")
    return conn


@app.get("/admin/clients/{user_id}")
def admin_client_detail(user_id: str, _: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="client not found")
    sub = db.query(Subscription).filter(Subscription.user_id == user_id).one_or_none()
    conn = db.query(DeltaConnection).filter(DeltaConnection.user_id == user_id).one_or_none()
    equity = 0.0
    positions: list = []
    live_error = None
    if conn is not None and conn.status == "connected":
        try:
            client = DeltaClient(conn.api_key, decrypt_secret(conn.api_secret_encrypted), sandbox=conn.sandbox)
            equity = client.equity_usd()
            positions = client.open_positions()
        except DeltaError as e:
            live_error = str(e)
    # Tradejini (Indian F&O) side, if connected
    tj_conn = db.query(TradejiniConnection).filter(TradejiniConnection.user_id == user_id).one_or_none()
    tj_block = None
    if tj_conn is not None:
        tj_expired = tj_conn.expires_at is not None and tj_conn.expires_at <= datetime.now(timezone.utc)
        tj_equity = 0.0
        tj_positions: list = []
        tj_error = None
        if tradejini_auth.has_auto_creds(tj_conn) and tj_conn.status != "disconnected" and not tj_conn.paused:
            try:
                tjc = tradejini.TradejiniClient(tradejini_auth.ensure_client_token(db, tj_conn), api_key=tj_conn.api_key)
                tj_equity = tjc.buyable_cash_inr()
                tj_positions = tjc.open_positions()
            except tradejini.TradejiniError as e:
                tj_error = str(e)
        tj_block = {
            "status": "expired" if (tj_expired and tj_conn.status == "connected") else tj_conn.status,
            "paused": tj_conn.paused,
            "expires_at": tj_conn.expires_at.isoformat() if tj_conn.expires_at else None,
            "equity_inr": tj_equity,
            "positions": tj_positions,
            "error": tj_error,
        }

    orders = (
        db.query(ClientOrder, Signal)
        .join(Signal, Signal.id == ClientOrder.signal_id)
        .filter(ClientOrder.user_id == user_id)
        .order_by(ClientOrder.created_at.desc())
        .limit(20)
        .all()
    )
    return {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "phone": u.phone,
        "client_id": u.client_id,
        "role": u.role,
        "subscription": None if sub is None else {
            "package": sub.package,
            "status": sub.status,
            "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
        },
        "connection": None if conn is None else {"status": conn.status, "paused": conn.paused, "sandbox": conn.sandbox},
        "equity": equity,
        "positions": positions,
        "live_error": live_error,
        "tradejini": tj_block,
        "orders": [
            {
                "symbol": s.symbol, "side": s.side, "status": co.status, "detail": co.detail,
                "size": co.size, "fill_px": co.fill_px,
                "at": co.created_at.isoformat() if co.created_at else None,
            }
            for (co, s) in orders
        ],
    }


def _both_conns(db: Session, user_id: str):
    """(delta_conn|None, tradejini_conn|None) — at least one must exist."""
    d = db.query(DeltaConnection).filter(DeltaConnection.user_id == user_id).one_or_none()
    t = db.query(TradejiniConnection).filter(TradejiniConnection.user_id == user_id).one_or_none()
    if d is None and t is None:
        raise HTTPException(status_code=404, detail="client has no connection")
    return d, t


@app.post("/admin/clients/{user_id}/pause")
def admin_pause(user_id: str, paused: bool = True, _: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    d, t = _both_conns(db, user_id)
    if d is not None:
        d.paused = paused
    if t is not None:
        t.paused = paused
    return {"paused": paused}


@app.post("/admin/clients/{user_id}/force-close")
def admin_force_close(user_id: str, _: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    """Close all of a client's open positions now, both venues (no disconnect)."""
    d, t = _both_conns(db, user_id)
    closed = 0
    errors = []
    if d is not None and d.status == "connected":
        try:
            client = DeltaClient(d.api_key, decrypt_secret(d.api_secret_encrypted), sandbox=d.sandbox)
            for p in client.open_positions():
                client.close_all(p["base"])
                closed += 1
        except DeltaError as e:
            errors.append(f"delta: {e}")
    if t is not None and tradejini_auth.has_auto_creds(t) and t.status != "disconnected":
        try:
            tjc = tradejini.TradejiniClient(tradejini_auth.ensure_client_token(db, t), api_key=t.api_key)
            for p in tjc.open_positions():
                tjc.close_position(p["sym_id"])
                closed += 1
        except tradejini.TradejiniError as e:
            errors.append(f"tradejini: {e}")
    if errors:
        raise HTTPException(status_code=502, detail=f"force-close partial ({closed} closed): {'; '.join(errors)}")
    return {"result": f"closed {closed} position(s)"}


@app.post("/admin/clients/{user_id}/disconnect")
def admin_disconnect(user_id: str, _: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    d, t = _both_conns(db, user_id)
    if d is not None:
        d.status = "disconnected"
        d.paused = True
    if t is not None:
        t.status = "disconnected"
        t.paused = True
    return {"status": "disconnected"}


class AdminChangePasswordRequest(BaseModel):
    new_password: str

@app.post("/admin/clients/{user_id}/change-password")
def admin_change_client_password(user_id: str, body: AdminChangePasswordRequest, _: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="client not found")
    if len(body.new_password or "") < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")
    u.password_hash = auth.hash_password(body.new_password)
    db.add(u)
    db.commit()
    return {"result": f"Password updated for client {u.email}"}

@app.delete("/admin/clients/{user_id}")
def admin_delete_client(user_id: str, _: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="client not found")
    
    email = u.email
    # Delete related rows across all tables safely with rollback protection
    for model_cls in [
        TradejiniConnection,
        DeltaConnection,
        CoinDCXConnection,
        Subscription,
        ClientOrder,
        PaymentScreenshot,
        EntitlementCounter,
        StraddlePosition,
    ]:
        try:
            db.query(model_cls).filter(model_cls.user_id == user_id).delete(synchronize_session=False)
            db.flush()
        except Exception:
            db.rollback()
            
    try:
        u = db.get(User, user_id)
        if u:
            db.delete(u)
            db.commit()
    except Exception:
        db.rollback()
        u = db.get(User, user_id)
        if u:
            for rel in [u.subscription, u.connection, u.payment_screenshot]:
                if rel:
                    try:
                        db.delete(rel)
                    except Exception:
                        pass
            db.delete(u)
            db.commit()

    return {"result": f"Client {email} permanently deleted"}


class RecordProfitLedgerRequest(BaseModel):
    user_id: str
    symbol: str
    side: str
    size: float = 0.0
    entry_price: float = 0.0
    exit_price: float | None = None
    realized_pnl_inr: float = 0.0
    realized_pnl_usd: float = 0.0
    fee_inr: float = 0.0
    fee_usd: float = 0.0
    venue: str = "tradejini"
    status: str = "closed"
    signal_id: str | None = None
    order_id: str | None = None
    notes: str | None = None

@app.post("/admin/profit-ledger/record")
def admin_record_profit_ledger(body: RecordProfitLedgerRequest, _: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    u = db.get(User, body.user_id)
    email = u.email if u else f"user_{body.user_id[:8]}"
    name = u.name if u else "Archived/Deleted Client"
    phone = u.phone if u else None
    client_id = u.client_id if u else None

    entry = ClientProfitLedger(
        user_id=body.user_id,
        email=email,
        name=name,
        phone=phone,
        client_id=client_id,
        venue=body.venue,
        symbol=body.symbol,
        side=body.side,
        size=body.size,
        entry_price=body.entry_price,
        exit_price=body.exit_price,
        realized_pnl_inr=body.realized_pnl_inr,
        realized_pnl_usd=body.realized_pnl_usd,
        fee_inr=body.fee_inr,
        fee_usd=body.fee_usd,
        status=body.status,
        signal_id=body.signal_id,
        order_id=body.order_id,
        notes=body.notes
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"result": "Recorded permanent profit ledger entry", "id": entry.id}

@app.get("/admin/ledger")
def admin_get_ledger_clients(_: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    users = db.query(User).all()

    # Auto-sync live broker positions for connected Tradejini clients
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(ist)
    today_start_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)

    for u in users:
        conn = db.query(TradejiniConnection).filter_by(user_id=u.id).one_or_none()
        if conn and tradejini_auth.has_auto_creds(conn) and conn.status == "connected":
            try:
                token = tradejini_auth.ensure_client_token(db, conn)
                tj_client = tradejini.TradejiniClient(token, api_key=conn.api_key)
                raw_pos = tj_client._get("/api/oms/positions", {"symDetails": "true"})
                pos_list = (raw_pos or {}).get("d", []) if isinstance(raw_pos, dict) else []
                for p in pos_list:
                    sym_obj = p.get("sym") or {}
                    disp_sym = sym_obj.get("dispSym") or sym_obj.get("trdSym") or p.get("symId") or "Unknown"
                    rpnl = float(p.get("realizedPnl") or p.get("dayPos", {}).get("dayRealizedPnl", 0.0) or 0.0)
                    buy_qty = float(p.get("buyQty", 0) or 0)
                    sell_qty = float(p.get("sellQty", 0) or 0)
                    buy_avg = float(p.get("buyAvgPrice", 0) or 0)
                    sell_avg = float(p.get("sellAvgPrice", 0) or 0)
                    net_qty = float(p.get("netQty", 0) or 0)
                    qty = max(buy_qty, sell_qty)
                    if qty > 0:
                        existing = (
                            db.query(ClientProfitLedger)
                            .filter(
                                ClientProfitLedger.user_id == u.id,
                                ClientProfitLedger.symbol == disp_sym,
                                ClientProfitLedger.executed_at >= today_start_ist.astimezone(timezone.utc)
                            )
                            .first()
                        )
                        if not existing:
                            rec = ClientProfitLedger(
                                user_id=u.id,
                                email=u.email,
                                name=u.name,
                                phone=u.phone,
                                client_id=u.client_id,
                                venue="tradejini",
                                symbol=disp_sym,
                                side="buy" if buy_qty >= sell_qty else "sell",
                                size=qty,
                                entry_price=buy_avg,
                                exit_price=sell_avg if sell_qty > 0 else None,
                                realized_pnl_inr=rpnl,
                                fee_inr=round(qty * 0.05 + 40.0, 2),
                                status="closed" if net_qty == 0 else "open",
                                executed_at=now_ist.astimezone(timezone.utc),
                                closed_at=now_ist.astimezone(timezone.utc) if net_qty == 0 else None
                            )
                            db.add(rec)
                        else:
                            existing.realized_pnl_inr = rpnl
                            existing.entry_price = buy_avg
                            existing.exit_price = sell_avg if sell_qty > 0 else existing.exit_price
                            existing.size = qty
                            existing.status = "closed" if net_qty == 0 else "open"
                db.commit()
            except Exception as e:
                db.rollback()
                print(f"[Ledger Global Sync] Client {u.email}: {e}")

    entries = db.query(ClientProfitLedger).order_by(ClientProfitLedger.executed_at.desc()).all()

    clients_dict = {}
    
    # 1. Active / Pending / Rejected Clients
    for u in users:
        conn = db.query(TradejiniConnection).filter_by(user_id=u.id).one_or_none()
        tj_conn = conn and conn.status == "connected" and not conn.paused
        clients_dict[u.id] = {
            "user_id": u.id,
            "email": u.email,
            "name": u.name or "Client",
            "phone": u.phone,
            "client_id": u.client_id,
            "status": u.payment_status or "pending",
            "tradejini_connected": bool(tj_conn),
            "total_trades": 0,
            "wins": 0,
            "booked_pnl_inr": 0.0,
            "total_fees_inr": 0.0,
            "net_pnl_inr": 0.0,
            "is_deleted": False,
        }

    # 2. Permanent Ledger Entries (including deleted/archived clients)
    for e in entries:
        uid = e.user_id
        if uid not in clients_dict:
            clients_dict[uid] = {
                "user_id": uid,
                "email": e.email,
                "name": e.name or "Archived Client",
                "phone": e.phone,
                "client_id": e.client_id,
                "status": "archived",
                "tradejini_connected": False,
                "total_trades": 0,
                "wins": 0,
                "booked_pnl_inr": 0.0,
                "total_fees_inr": 0.0,
                "net_pnl_inr": 0.0,
                "is_deleted": True,
            }
        
        c = clients_dict[uid]
        c["total_trades"] += 1
        pnl = e.realized_pnl_inr or 0.0
        fee = e.fee_inr or 0.0
        if pnl > 0 or (e.realized_pnl_usd and e.realized_pnl_usd > 0):
            c["wins"] += 1
        c["booked_pnl_inr"] = round(c["booked_pnl_inr"] + pnl, 2)
        c["total_fees_inr"] = round(c["total_fees_inr"] + fee, 2)
        c["net_pnl_inr"] = round(c["booked_pnl_inr"] - c["total_fees_inr"], 2)

    client_list = sorted(list(clients_dict.values()), key=lambda x: x["booked_pnl_inr"], reverse=True)
    return {"clients": client_list}


@app.get("/admin/ledger/{user_id}/profits")
def admin_get_client_profits(
    user_id: str,
    timeframe: str = "all",
    from_date: str | None = None,
    to_date: str | None = None,
    _: User = Depends(auth.require_admin),
    db: Session = Depends(get_db)
):
    u = db.get(User, user_id)
    if u is None:
        u_lower = (user_id or "").strip().lower()
        u = db.query(User).filter(
            or_(
                func.lower(User.client_id) == u_lower,
                func.lower(User.email) == u_lower,
                func.lower(User.name) == u_lower
            )
        ).first()
    
    query_id = u.id if u else user_id
    u_client_id = u.client_id if u else user_id
    u_email = u.email if u else user_id

    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(ist)
    today_start_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)

    tj_connected = False
    tj_today_realized_pnl = 0.0
    tj_positions = []
    tj_trades = []

    # 1. Direct Live Broker Synchronization (Tradejini)
    if u:
        conn = db.query(TradejiniConnection).filter_by(user_id=u.id).one_or_none()
        if conn and tradejini_auth.has_auto_creds(conn) and conn.status == "connected":
            try:
                token = tradejini_auth.ensure_client_token(db, conn)
                tj_client = tradejini.TradejiniClient(token, api_key=conn.api_key)
                
                # Fetch positions
                raw_pos = tj_client._get("/api/oms/positions", {"symDetails": "true"})
                pos_list = (raw_pos or {}).get("d", []) if isinstance(raw_pos, dict) else []
                
                # Fetch trade book
                raw_trades = tj_client._get("/api/oms/trades", {"symDetails": "true"})
                trades_list = (raw_trades or {}).get("d", []) if isinstance(raw_trades, dict) else []
                
                tj_connected = True
                
                # Parse positions & compute today's real booked PnL
                for p in pos_list:
                    sym_obj = p.get("sym") or {}
                    disp_sym = sym_obj.get("dispSym") or sym_obj.get("trdSym") or p.get("symId") or "Unknown"
                    rpnl = float(p.get("realizedPnl") or p.get("dayPos", {}).get("dayRealizedPnl", 0.0) or 0.0)
                    buy_qty = float(p.get("buyQty", 0) or 0)
                    sell_qty = float(p.get("sellQty", 0) or 0)
                    buy_avg = float(p.get("buyAvgPrice", 0) or 0)
                    sell_avg = float(p.get("sellAvgPrice", 0) or 0)
                    net_qty = float(p.get("netQty", 0) or 0)
                    qty = max(buy_qty, sell_qty)

                    tj_today_realized_pnl += rpnl
                    tj_positions.append({
                        "symbol": disp_sym,
                        "product": p.get("product", "normal"),
                        "net_qty": net_qty,
                        "buy_qty": buy_qty,
                        "sell_qty": sell_qty,
                        "buy_avg_price": buy_avg,
                        "sell_avg_price": sell_avg,
                        "realized_pnl": round(rpnl, 2),
                        "status": "closed" if net_qty == 0 else "open"
                    })

                    # Sync to ClientProfitLedger for today
                    if qty > 0:
                        existing = (
                            db.query(ClientProfitLedger)
                            .filter(
                                ClientProfitLedger.user_id == u.id,
                                ClientProfitLedger.symbol == disp_sym,
                                ClientProfitLedger.executed_at >= today_start_ist.astimezone(timezone.utc)
                            )
                            .first()
                        )
                        if not existing:
                            rec = ClientProfitLedger(
                                user_id=u.id,
                                email=u.email,
                                name=u.name,
                                phone=u.phone,
                                client_id=u.client_id,
                                venue="tradejini",
                                symbol=disp_sym,
                                side="buy" if buy_qty >= sell_qty else "sell",
                                size=qty,
                                entry_price=buy_avg,
                                exit_price=sell_avg if sell_qty > 0 else None,
                                realized_pnl_inr=rpnl,
                                fee_inr=round(qty * 0.05 + 40.0, 2),
                                status="closed" if net_qty == 0 else "open",
                                executed_at=now_ist.astimezone(timezone.utc),
                                closed_at=now_ist.astimezone(timezone.utc) if net_qty == 0 else None
                            )
                            db.add(rec)
                        else:
                            existing.realized_pnl_inr = rpnl
                            existing.entry_price = buy_avg
                            existing.exit_price = sell_avg if sell_qty > 0 else existing.exit_price
                            existing.size = qty
                            existing.status = "closed" if net_qty == 0 else "open"
                            if net_qty == 0 and not existing.closed_at:
                                existing.closed_at = now_ist.astimezone(timezone.utc)

                for t in trades_list:
                    sym_obj = t.get("sym") or {}
                    disp_sym = sym_obj.get("dispSym") or sym_obj.get("trdSym") or t.get("symId") or "Unknown"
                    tj_trades.append({
                        "order_id": t.get("orderId"),
                        "fill_id": t.get("fillId"),
                        "symbol": disp_sym,
                        "side": t.get("side"),
                        "fill_qty": t.get("fillQty"),
                        "fill_price": t.get("fillPrice"),
                        "fill_value": t.get("fillValue"),
                        "time": t.get("time"),
                    })

                db.commit()
            except Exception as e:
                db.rollback()
                print(f"[Ledger Profits Sync] Tradejini error: {e}")

    # 2. Query ClientProfitLedger with Date Range Filtering
    q = (
        db.query(ClientProfitLedger)
        .filter(
            or_(
                ClientProfitLedger.user_id == query_id,
                func.lower(ClientProfitLedger.client_id) == (u_client_id or "").lower(),
                func.lower(ClientProfitLedger.email) == (u_email or "").lower()
            )
        )
    )

    # Timeframe calculation (IST based)
    cutoff = None
    if timeframe == "today":
        cutoff = today_start_ist.astimezone(timezone.utc)
    elif timeframe == "7d":
        cutoff = (today_start_ist - timedelta(days=7)).astimezone(timezone.utc)
    elif timeframe == "1m":
        cutoff = (today_start_ist - timedelta(days=30)).astimezone(timezone.utc)
    elif timeframe == "3m":
        cutoff = (today_start_ist - timedelta(days=90)).astimezone(timezone.utc)
    elif timeframe == "1y":
        cutoff = (today_start_ist - timedelta(days=365)).astimezone(timezone.utc)
    
    if cutoff:
        q = q.filter(ClientProfitLedger.executed_at >= cutoff)
    
    if from_date:
        try:
            fd = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=ist).astimezone(timezone.utc)
            q = q.filter(ClientProfitLedger.executed_at >= fd)
        except Exception:
            pass
    if to_date:
        try:
            td = (datetime.strptime(to_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=ist)).astimezone(timezone.utc)
            q = q.filter(ClientProfitLedger.executed_at <= td)
        except Exception:
            pass

    entries = q.order_by(ClientProfitLedger.executed_at.desc()).all()

    # 3. Group by Day and Month
    daily_map = {}
    monthly_map = {}

    total_gross_pnl = 0.0
    total_fees = 0.0
    wins_count = 0
    losses_count = 0

    for e in entries:
        dt_ist = e.executed_at.astimezone(ist) if e.executed_at else now_ist
        day_key = dt_ist.strftime("%Y-%m-%d")
        month_key = dt_ist.strftime("%Y-%m")
        day_formatted = dt_ist.strftime("%d %b %Y")
        month_formatted = dt_ist.strftime("%B %Y")

        pnl = e.realized_pnl_inr or 0.0
        fee = e.fee_inr or 0.0
        total_gross_pnl += pnl
        total_fees += fee

        is_win = pnl > 0
        is_loss = pnl < 0
        if is_win:
            wins_count += 1
        elif is_loss:
            losses_count += 1

        entry_dict = {
            "id": e.id,
            "symbol": e.symbol,
            "side": e.side,
            "size": e.size,
            "entry_price": e.entry_price,
            "exit_price": e.exit_price,
            "realized_pnl_inr": round(pnl, 2),
            "fee_inr": round(fee, 2),
            "net_pnl_inr": round(pnl - fee, 2),
            "status": e.status,
            "executed_at": e.executed_at.isoformat() if e.executed_at else None,
            "time_ist": dt_ist.strftime("%H:%M:%S"),
        }

        # Daily breakdown accumulation
        if day_key not in daily_map:
            daily_map[day_key] = {
                "date": day_key,
                "formatted_date": day_formatted,
                "gross_pnl_inr": 0.0,
                "total_fees_inr": 0.0,
                "net_pnl_inr": 0.0,
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "trades": []
            }
        d = daily_map[day_key]
        d["gross_pnl_inr"] += pnl
        d["total_fees_inr"] += fee
        d["net_pnl_inr"] += (pnl - fee)
        d["total_trades"] += 1
        if is_win:
            d["wins"] += 1
        elif is_loss:
            d["losses"] += 1
        d["trades"].append(entry_dict)

        # Monthly breakdown accumulation
        if month_key not in monthly_map:
            monthly_map[month_key] = {
                "month": month_key,
                "formatted_month": month_formatted,
                "gross_pnl_inr": 0.0,
                "total_fees_inr": 0.0,
                "net_pnl_inr": 0.0,
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
            }
        m = monthly_map[month_key]
        m["gross_pnl_inr"] += pnl
        m["total_fees_inr"] += fee
        m["net_pnl_inr"] += (pnl - fee)
        m["total_trades"] += 1
        if is_win:
            m["wins"] += 1
        elif is_loss:
            m["losses"] += 1

    # Format lists sorted by newest date/month first
    daily_breakdown = sorted(
        [
            {
                **d,
                "gross_pnl_inr": round(d["gross_pnl_inr"], 2),
                "total_fees_inr": round(d["total_fees_inr"], 2),
                "net_pnl_inr": round(d["net_pnl_inr"], 2),
                "win_rate_pct": round((d["wins"] / d["total_trades"] * 100) if d["total_trades"] > 0 else 0.0, 1)
            }
            for d in daily_map.values()
        ],
        key=lambda x: x["date"],
        reverse=True
    )

    monthly_breakdown = sorted(
        [
            {
                **m,
                "gross_pnl_inr": round(m["gross_pnl_inr"], 2),
                "total_fees_inr": round(m["total_fees_inr"], 2),
                "net_pnl_inr": round(m["net_pnl_inr"], 2),
                "win_rate_pct": round((m["wins"] / m["total_trades"] * 100) if m["total_trades"] > 0 else 0.0, 1)
            }
            for m in monthly_map.values()
        ],
        key=lambda x: x["month"],
        reverse=True
    )

    total_count = len(entries)
    win_rate = round((wins_count / total_count * 100) if total_count > 0 else 0.0, 1)
    net_pnl = total_gross_pnl - total_fees

    client_info = {
        "user_id": u.id if u else query_id,
        "email": u.email if u else (entries[0].email if entries else "Unknown"),
        "name": u.name if u else (entries[0].name if entries else "Client"),
        "phone": u.phone if u else (entries[0].phone if entries else None),
        "client_id": u.client_id if u else (entries[0].client_id if entries else (user_id if not user_id.startswith("user_") else None)),
        "status": u.payment_status if u else "archived",
    }

    return {
        "client": client_info,
        "timeframe": timeframe,
        "tradejini_connected": tj_connected,
        "tradejini_today_realized_pnl": round(tj_today_realized_pnl, 2),
        "summary": {
            "gross_pnl_inr": round(total_gross_pnl, 2),
            "total_fees_inr": round(total_fees, 2),
            "net_pnl_inr": round(net_pnl, 2),
            "total_trades": total_count,
            "wins": wins_count,
            "losses": losses_count,
            "win_rate_pct": win_rate,
        },
        "booked_pnl_inr": round(total_gross_pnl, 2),
        "total_fees_inr": round(total_fees, 2),
        "total_trades": total_count,
        "tradejini_positions": tj_positions,
        "tradejini_trades": tj_trades,
        "daily_breakdown": daily_breakdown,
        "monthly_breakdown": monthly_breakdown,
        "entries": [
            {
                "id": e.id,
                "symbol": e.symbol,
                "side": e.side,
                "size": e.size,
                "entry_price": e.entry_price,
                "exit_price": e.exit_price,
                "realized_pnl_inr": round(e.realized_pnl_inr, 2),
                "realized_pnl_usd": round(e.realized_pnl_usd, 2),
                "fee_inr": round(e.fee_inr, 2),
                "net_pnl_inr": round((e.realized_pnl_inr or 0.0) - (e.fee_inr or 0.0), 2),
                "status": e.status,
                "executed_at": e.executed_at.isoformat() if e.executed_at else None,
            }
            for e in entries
        ]
    }


# ── admin: AI Vision brain log ─────────────────────────────
@app.get("/admin/brain/events")
def admin_brain_events(limit: int = 50, source: str | None = None,
                       _: User = Depends(auth.require_admin),
                       db: Session = Depends(get_db)):
    """Recent AI-Vision decisions (metadata only — the chart blob is fetched
    per-event via /chart so this list stays light). `source` filters to one brain
    (nifty-brain | crypto-brain) so the NIFTY and crypto logs stay separate and
    neither can crowd the other out of the newest-N window."""
    limit = max(1, min(limit, 200))
    q = db.query(
        BrainEvent.id, BrainEvent.source, BrainEvent.ts, BrainEvent.instrument,
        BrainEvent.tj_symbol, BrainEvent.side, BrainEvent.ref_price, BrainEvent.sl_price,
        BrainEvent.atr, BrainEvent.red_dots, BrainEvent.vision_evaluated,
        BrainEvent.congested, BrainEvent.vision_reason, BrainEvent.visual_sl,
        BrainEvent.action, BrainEvent.signal_id,
        BrainEvent.chart_b64.isnot(None).label("has_chart"),
    )
    if source:
        q = q.filter(BrainEvent.source == source)
    rows = q.order_by(BrainEvent.ts.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "source": r.source or "nifty-brain",
            "ts": r.ts.isoformat() if r.ts else None,
            "instrument": r.instrument,
            "tj_symbol": r.tj_symbol,
            "side": r.side,
            "ref_price": r.ref_price,
            "sl_price": r.sl_price,
            "atr": r.atr,
            "red_dots": r.red_dots,
            "vision_evaluated": r.vision_evaluated,
            "congested": r.congested,
            "vision_reason": r.vision_reason,
            "visual_sl": r.visual_sl,
            "action": r.action,
            "signal_id": r.signal_id,
            "has_chart": bool(r.has_chart),
        }
        for r in rows
    ]


@app.get("/admin/brain/events/{event_id}/chart")
def admin_brain_chart(event_id: str, _: User = Depends(auth.require_admin),
                      db: Session = Depends(get_db)):
    """The chart the model saw, as base64 PNG (fetched on demand for the lightbox)."""
    row = db.query(BrainEvent.chart_b64).filter(BrainEvent.id == event_id).one_or_none()
    if row is None or not row[0]:
        raise HTTPException(status_code=404, detail="no chart for this event")
    return {"chart_b64": row[0]}


# ── admin: market screener (Binance board + CoinAPI depth + cross counts) ──
def _touch_screener_heartbeat() -> None:
    """Tell the coinapi-feed sidecar the panel is being viewed so it streams
    (and only burns CoinAPI credits) while someone is actually looking. Best
    effort — a perms/FS hiccup must never break the screener response."""
    path = settings.screener_feed_heartbeat
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a"):
            os.utime(path, None)
    except OSError:
        pass


@app.get("/admin/screener")
def admin_screener(
    sort: str = "volume",
    dir: str = "desc",
    min_volume: float = 0.0,
    top: int = 100,
    _: User = Depends(auth.require_admin),
):
    """Crypto screener for the admin panel: Binance USDT-M futures board (symbol /
    last price / 24h change% / 24h quote volume, ranked) enriched with live
    CoinAPI order-book depth (spread / depth / imbalance) and the bot's 48h
    EMA-cross count. Public market data, cached ~4s. sort: volume | change |
    abs_change; dir: desc | asc. Any board failure surfaces as a 502 the admin
    panel renders as an error card; depth/cross enrichment degrades silently."""
    _touch_screener_heartbeat()
    try:
        rows, feed_live, movers = screener.fetch_screener(
            sort=sort,
            ascending=(dir.lower() == "asc"),
            min_volume=max(0.0, min_volume),
            top=max(0, min(top, 500)),
            feed_path=settings.screener_feed_snapshot,
            cross_db=settings.screener_cross_db,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"screener upstream unavailable: {e}")
    return {
        "rows": rows,
        "count": len(rows),
        "source": "binance-futures",
        "feed_live": feed_live,
        "movers": movers,  # top gainers/losers (24h%) + volume_rate — AI-consumable
    }


@app.get("/admin/orderbook")
def admin_orderbook(symbol: str = "SOL", _: User = Depends(auth.require_admin)):
    """Live order-book + trend snapshot for one coin (Binance USDT-M futures): depth,
    spread, imbalance, S/R walls, EMA9/150 trend + 48h congestion, and a cumulative-
    depth curve for the chart. Read-only context for the operator (not a bot signal).
    Any upstream failure → 502 the panel renders as an error card."""
    try:
        return orderbook.fetch_orderbook(symbol, cross_db=settings.screener_cross_db)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"orderbook unavailable: {e}")


# ── public: AAA candlestick-setup scanner (Indian stocks, 1D) ──
@app.get("/aaa/setups")
def aaa_setups():
    """PUBLIC. The day's candlestick setups for NSE stocks on the 1D timeframe,
    refreshed by the daily pre-open scan (systemd timer ~08:00 IST). The endpoint
    only READS the sidecar the scanner writes — it never calls Kite — so it stays
    fast and never hangs. Missing/unreadable sidecar → an empty, `stale` payload
    the UI renders as "scan hasn't run yet" rather than an error."""
    import json
    path = settings.aaa_setups_path
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {
            "generated_at": None, "generated_at_ist": None,
            "universe": "NSE equities · 1D", "scanned": 0, "errors": 0,
            "count": 0, "setups": [], "stale": True,
        }
    data.setdefault("stale", False)
    return data


# ── internal: brain publishes signals ──────────────────────
class SignalIn(BaseModel):
    symbol: str
    side: str
    sl_price: float | None = None
    ref_price: float | None = None
    venue: str = "delta"  # delta (crypto) | tradejini (Indian F&O)
    exit_only: bool = False  # take-profit only: close the opposite side, never open


@app.post("/internal/signals")
def publish(body: SignalIn, request: Request, db: Session = Depends(get_db)):
    token = request.headers.get("x-internal-token", "")
    if not token or token != os.environ.get("INTERNAL_SIGNAL_TOKEN", ""):
        raise HTTPException(status_code=401, detail="unauthorized")
    sig: Signal = signal_bus.publish_signal(
        db, body.symbol, body.side, body.sl_price, body.ref_price, source="brain",
        venue=body.venue, exit_only=body.exit_only,
    )
    return {"signal_id": sig.id}
