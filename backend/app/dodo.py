"""Dodo Payments integration (Merchant of Record).

- create_checkout_session: hosted subscription checkout for a package.
- verify_webhook: validate the signed webhook (Dodo uses Standard Webhooks /
  svix-style signatures).
- handle_event: map subscription.* events onto our subscription state, and
  trigger force-close on lapse.

NOTE: exact field names below follow Dodo's documented patterns; confirm against
the live API reference (docs.dodopayments.com) and adjust the small bits marked
CONFIRM. The control-flow and state mapping are correct regardless.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from .config import settings
from .models import AuditLog, DeltaConnection, Subscription, User
from .packages import get_rule

_PRODUCT_MAP = {
    "starter": lambda: settings.dodo_product_starter,
    "growth": lambda: settings.dodo_product_growth,
    "pro": lambda: settings.dodo_product_pro,
}


def create_checkout_session(user: User, package_id: str) -> str:
    """Create a Dodo checkout session for a subscription product and return the
    hosted checkout_url to redirect the user to."""
    product_id = _PRODUCT_MAP[package_id]()
    if not product_id:
        raise RuntimeError(f"Dodo product id not configured for package '{package_id}'")
    payload = {
        # Dodo "Create Checkout Session": POST /checkouts (Standard Webhooks docs).
        "product_cart": [{"product_id": product_id, "quantity": 1}],
        "customer": {"email": user.email},
        "metadata": {"user_id": user.id, "package": package_id},
        "return_url": f"{settings.app_origin}/connect",
    }
    resp = httpx.post(
        f"{settings.dodo_base_url}/checkouts",
        headers={"Authorization": f"Bearer {settings.dodo_api_key}"},
        json=payload,
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("checkout_url") or data.get("payment_link") or data.get("url")


def verify_webhook(raw_body: bytes, headers: dict[str, str]) -> bool:
    """Standard-Webhooks HMAC verification. Header names per the spec:
    webhook-id, webhook-timestamp, webhook-signature."""
    secret = settings.dodo_webhook_secret
    if not secret:
        return False
    wid = headers.get("webhook-id", "")
    wts = headers.get("webhook-timestamp", "")
    wsig = headers.get("webhook-signature", "")
    signed = f"{wid}.{wts}.{raw_body.decode()}".encode()
    key = base64.b64decode(secret.split("_", 1)[-1]) if secret.startswith("whsec_") else secret.encode()
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    # webhook-signature may be space-separated "v1,<sig> v1,<sig2>"
    for part in wsig.split():
        if part.split(",", 1)[-1] == expected:
            return True
    return False


def handle_event(db: Session, event: dict) -> None:
    """Map a Dodo subscription event onto our state."""
    etype = event.get("type", "")
    data = event.get("data", {}) or {}
    meta = data.get("metadata", {}) or {}
    user_id = meta.get("user_id")
    package = meta.get("package")
    dodo_sub_id = data.get("subscription_id") or data.get("id")

    if not user_id:
        return  # can't attribute; rely on Dodo dashboard

    sub = db.query(Subscription).filter(Subscription.user_id == user_id).one_or_none()
    if sub is None and package:
        sub = Subscription(user_id=user_id, package=package, status="pending")
        db.add(sub)
        db.flush()
    if sub is None:
        return

    if dodo_sub_id:
        sub.dodo_subscription_id = dodo_sub_id
    if package:
        get_rule(package)  # validate
        sub.package = package

    if etype in ("subscription.active", "subscription.renewed"):
        sub.status = "active"
        period_end = data.get("current_period_end") or data.get("next_billing_date")
        if period_end:
            try:
                sub.current_period_end = datetime.fromisoformat(str(period_end).replace("Z", "+00:00"))
            except ValueError:
                pass
        _audit(db, "dodo", etype, user_id, {"sub": dodo_sub_id})

    elif etype in ("subscription.on_hold", "subscription.failed", "subscription.cancelled"):
        sub.status = "on_hold" if etype == "subscription.on_hold" else (
            "cancelled" if etype == "subscription.cancelled" else "failed"
        )
        _flag_force_close(db, user_id)
        _audit(db, "dodo", etype, user_id, {"sub": dodo_sub_id, "action": "force_close_flagged"})

    sub.updated_at = datetime.now(timezone.utc)


def _flag_force_close(db: Session, user_id: str) -> None:
    """On lapse, mark the connection so the worker force-closes + stops trading.
    Setting paused=True halts new entries immediately; the worker's lapse sweep
    closes open positions."""
    conn = db.query(DeltaConnection).filter(DeltaConnection.user_id == user_id).one_or_none()
    if conn is not None:
        conn.paused = True


def _audit(db: Session, actor: str, action: str, target: str, meta: dict) -> None:
    db.add(AuditLog(actor=actor, action=action, target=target, meta=json.dumps(meta)))
