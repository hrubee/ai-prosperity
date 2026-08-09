"""Password auth + JWT sessions.

Flow: POST /auth/register {name, email, phone, password}
        - new email      -> create account (name/phone/password), issue JWT
        - existing email -> verify password, issue JWT (401 on mismatch)
There is deliberately NO "claim" path that sets a password on an existing
account — that would let anyone take over a pre-password (legacy) account by
guessing its email. Legacy rows are migrated server-side at deploy instead.
JWT is sent as a Bearer token; `current_user` / `require_admin` are deps.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import User

_bearer = HTTPBearer(auto_error=False)
_PBKDF2_ITERS = 200_000
MIN_PASSWORD_LEN = 8


# ── password hashing (stdlib PBKDF2-HMAC-SHA256, no extra dep) ──
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERS)
    return f"pbkdf2${_PBKDF2_ITERS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def _norm_phone(phone: str) -> str:
    """Digits only, last 10 (Indian mobile) — so '+91 98765 43210', '9876543210'
    and '+919876543210' all match. Used as the phone login key + uniqueness key."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    return digits[-10:] if len(digits) > 10 else digits


def register(db: Session, name: str, email: str, phone: str, password: str) -> User:
    """Create a NEW account. 409 if the email or phone already exists (returning
    users log in instead). No "claim" of existing rows."""
    email = (email or "").lower().strip()
    name = (name or "").strip()
    phone_n = _norm_phone(phone)
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="a valid email is required")
    if not name:
        raise HTTPException(status_code=400, detail="your name is required")
    if len(phone_n) < 7:
        raise HTTPException(status_code=400, detail="a valid phone number is required")
    if len(password or "") < MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail=f"password must be at least {MIN_PASSWORD_LEN} characters")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="an account with this email already exists — please log in")
    if db.query(User).filter(User.phone == phone_n).first():
        raise HTTPException(status_code=409, detail="an account with this phone already exists — please log in")
    user = User(email=email, name=name, phone=phone_n,
                password_hash=hash_password(password), role="user")
    db.add(user)
    db.flush()
    return user


def login(db: Session, identifier: str, password: str) -> User:
    """Returning users: look up by email (if it contains '@') or phone, then
    verify the password. One generic error to avoid account enumeration."""
    identifier = (identifier or "").strip()
    if not identifier:
        raise HTTPException(status_code=400, detail="enter your email or phone")
    if "@" in identifier:
        user = db.query(User).filter(User.email == identifier.lower()).one_or_none()
    else:
        user = db.query(User).filter(User.phone == _norm_phone(identifier)).one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="incorrect email/phone or password")
    return user


def change_password(db: Session, user: User, current_password: str, new_password: str) -> None:
    if not verify_password(current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="your current password is incorrect")
    if len(new_password or "") < MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail=f"new password must be at least {MIN_PASSWORD_LEN} characters")
    user.password_hash = hash_password(new_password)
    db.add(user)
    db.flush()


def make_jwt(user: User) -> str:
    payload = {
        "sub": user.id,
        "email": user.email,
        "role": user.role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=settings.jwt_ttl_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status_code=401, detail="missing token")
    try:
        payload = jwt.decode(creds.credentials, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid token")
    user = db.query(User).filter(User.id == payload.get("sub")).one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return user
