"""Per-client Tradejini AUTO-LOGIN — clients connect ONCE; we mint a fresh daily
token on demand from their stored api_key + password + TOTP seed (Fernet-encrypted).
Mirrors the operator data account's `individual_data_token`, but per client. No
manual daily reconnect — `ensure_client_token` transparently refreshes."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from . import tradejini
from .crypto import decrypt_secret, encrypt_secret
from .models import TradejiniConnection

log = logging.getLogger("tj-auth")

# Refresh a little before the broker's hard expiry so a trade never races a dead token.
_REFRESH_SKEW = timedelta(minutes=5)


def _next_token_cutoff(now_utc: datetime) -> datetime:
    """Tradejini access tokens die at a DAILY cutoff (~06:00 IST), NOT 24h after
    minting (same as Zerodha/Kite). Return the next 06:00-IST cutoff in UTC, minus
    a small margin — so `ensure_client_token` re-mints every morning instead of
    serving yesterday's dead token. (Verified 2026-06-02: 24h `expires_in` led to
    401s on day-old tokens; a fresh mint fixed it.)"""
    ist = now_utc + timedelta(hours=5, minutes=30)
    cutoff_ist = ist.replace(hour=6, minute=0, second=0, microsecond=0)
    if ist >= cutoff_ist:
        cutoff_ist += timedelta(days=1)
    return (cutoff_ist - timedelta(hours=5, minutes=30)) - timedelta(minutes=5)


def has_auto_creds(conn: TradejiniConnection) -> bool:
    return bool(conn and conn.api_key and conn.password_encrypted and conn.totp_seed_encrypted)


def mint_and_store(db, conn: TradejiniConnection) -> str:
    """Mint a fresh token from the stored auto-login creds and persist it. Raises
    TradejiniError on failure (caller surfaces it; status left untouched on error)."""
    if not has_auto_creds(conn):
        raise tradejini.TradejiniError("Tradejini auto-login not configured — reconnect your account")
    pw = decrypt_secret(conn.password_encrypted)
    seed = decrypt_secret(conn.totp_seed_encrypted)
    code = tradejini._totp_now(seed)  # fresh 6-digit TOTP from the stored seed
    tok, expires_in = tradejini.mint_client_token(conn.api_key, pw, code, "totp")
    now = datetime.now(timezone.utc)
    conn.access_token_encrypted = encrypt_secret(tok)
    # Cap at the daily cutoff: the token dies at ~06:00 IST regardless of the
    # broker's optimistic 24h `expires_in`, so never trust it past the cutoff.
    conn.expires_at = min(now + timedelta(seconds=int(expires_in or 86400)),
                          _next_token_cutoff(now))
    conn.status = "connected"
    db.commit()
    return tok


def prewarm_all(db) -> list:
    """PRE-MARKET WARM-UP: force a fresh login for EVERY connected client so all
    tokens are valid BEFORE the 09:35 entry (never mint lazily mid-trade). Returns
    [(email, ok, detail)] so the caller can alert the operator on any failure."""
    from sqlalchemy import select
    from .models import User
    out = []
    for c in db.execute(select(TradejiniConnection)).scalars().all():
        u = db.get(User, c.user_id)
        email = (u.email if u else c.user_id) or c.user_id
        if not has_auto_creds(c) or c.status == "disconnected":
            continue
        try:
            mint_and_store(db, c)
            cl = __import__("app.tradejini", fromlist=["TradejiniClient"]).TradejiniClient(
                decrypt_secret(c.access_token_encrypted), api_key=c.api_key)
            out.append((email, True, f"equity Rs.{cl.equity_inr():,.0f}"))
        except Exception as e:
            out.append((email, False, str(e)[:160]))
    return out


def run_prewarm():
    """Entrypoint for the 08:55-IST pre-market timer: warm all clients + alert."""
    from .db import session_scope
    from . import telegram_notify
    with session_scope() as db:
        results = prewarm_all(db)
    ok = [r for r in results if r[1]]
    fail = [r for r in results if not r[1]]
    msg = f"\U0001f511 Tradejini pre-market login: {len(ok)}/{len(results)} clients authorized for today."
    if fail:
        msg += "\n⚠️ FAILED — fix before 09:35:\n" + "\n".join(
            f"  {e}: {d}" for (e, _, d) in fail)
    for (e, _, d) in ok:
        msg += f"\n  ✓ {e} ({d})"
    log.info("prewarm: %d ok, %d failed", len(ok), len(fail))
    telegram_notify.send_message(msg)
    print(msg)


def ensure_client_token(db, conn: TradejiniConnection) -> str:
    """Return a usable access token for this client, auto-minting a fresh one if
    the current token is missing or within the refresh skew of expiry. This is
    what makes the connection "set once, runs forever" — clients never re-auth."""
    now = datetime.now(timezone.utc)
    if (conn.access_token_encrypted and conn.expires_at
            and conn.expires_at > now + _REFRESH_SKEW):
        return decrypt_secret(conn.access_token_encrypted)
    return mint_and_store(db, conn)
