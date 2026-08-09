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
from pydantic import BaseModel, EmailStr
from sqlalchemy import func
from sqlalchemy.orm import Session

from .dhan_poller import run_poller_async
import asyncio
from . import auth, dodo, orderbook, screener, signal_bus, tradejini, tradejini_auth, vol2b2t, copier
from .config import settings
from .crypto import decrypt_secret, encrypt_secret
from .db import get_db
from .delta import DeltaClient, DeltaError
from .models import BrainEvent, ClientOrder, DeltaConnection, DhanConnection, PaymentScreenshot, PricingPlan, Signal, Subscription, TradejiniConnection, User
from .packages import PACKAGES

log = logging.getLogger("api")
app = FastAPI(title="AI Prosperity API", version="0.1.0")

app.include_router(vol2b2t.router, prefix="/vol2b2t")
app.include_router(copier.router)

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
    client_id: str
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
    """Sign up a NEW user (name + email + phone + password; no OTP/email).
    409 if the email or phone is already registered — they should log in."""
    user = auth.register(db, body.name, body.email, body.phone, body.password)
    return {"token": auth.make_jwt(user), "email": user.email, "role": user.role, "name": user.name}


@app.post("/auth/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    """Returning user: email OR phone + password."""
    user = auth.login(db, body.identifier, body.password)
    return {"token": auth.make_jwt(user), "email": user.email, "role": user.role, "name": user.name}


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
    return {
        "email": user.email,
        "name": user.name,
        "phone": user.phone,
        "role": user.role,
        "subscription": None if sub is None else {"package": sub.package, "status": sub.status},
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
    if connected_once and not conn.paused:
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
def dhan_connect(body: DhanConnectRequest, _: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    client_id = body.client_id.strip()
    access_token = body.access_token.strip()
    
    if not client_id or not access_token:
        raise HTTPException(status_code=400, detail="Client ID and Access Token are required")
    
    # We use client_id in the api_key column to keep schema backwards compatible for now
    conn = db.query(DhanConnection).filter(DhanConnection.api_key == client_id).one_or_none()
    if conn is None:
        conn = DhanConnection(api_key=client_id, client_id=client_id, access_token_encrypted="")
        db.add(conn)
        
    conn.access_token_encrypted = encrypt_secret(access_token)
    conn.status = "connected"
    conn.paused = False
    db.commit()
    return {"connected": True}


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
    users = db.query(User).filter(User.payment_status.in_(["pending", "pending_verification"])).all()
    # also fetch users with a screenshot even if their status is approved or rejected, to show history
    # Or actually let's just join the screenshot table.
    
    # We'll fetch all screenshots and the corresponding users.
    screenshots = db.query(PaymentScreenshot).order_by(PaymentScreenshot.created_at.desc()).all()
    
    # Let's map user_ids
    user_ids = [s.user_id for s in screenshots]
    users_with_ss = db.query(User).filter(User.id.in_(user_ids)).all()
    user_map = {u.id: u for u in users_with_ss}
    
    res = []
    # Add users with screenshots
    for s in screenshots:
        u = user_map.get(s.user_id)
        if not u:
            continue
        res.append({
            "user_id": u.id,
            "name": u.name,
            "email": u.email,
            "phone": u.phone,
            "payment_status": u.payment_status,
            "screenshot": {
                "status": s.status,
                "mime_type": s.mime_type,
                "image_b64": s.image_b64,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "reviewed_at": s.reviewed_at.isoformat() if s.reviewed_at else None,
                "review_note": s.review_note
            }
        })
        
    # Add users who are pending but have NO screenshot uploaded yet
    pending_users = db.query(User).filter(
        User.payment_status.in_(["pending", "pending_verification"]),
        ~User.id.in_(user_ids)
    ).all()
    
    for u in pending_users:
        res.append({
            "user_id": u.id,
            "name": u.name,
            "email": u.email,
            "phone": u.phone,
            "payment_status": u.payment_status,
            "screenshot": None
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
    if not conn:
        return {"is_valid": False}
    try:
        from .crypto import decrypt_secret
        token = decrypt_secret(conn.access_token_encrypted)
    except:
        token = None
    return {
        "is_valid": True,
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
    return [
        {
            "id": u.id,
            "email": u.email,
            "package": s.package if s else None,
            "subscription": s.status if s else None,
            "connection": c.status if c else None,
            "paused": c.paused if c else None,
            "sandbox": c.sandbox if c else None,
            "tradejini": t.status if t else None,  # F&O venue connection status
        }
        for (u, s, c, t) in rows
    ]


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
                tj_equity = tjc.equity_inr()
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
