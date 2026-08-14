"""Per-client execution pipeline (Phase 3 core).

execute_signal_for_user() runs the full chain for ONE client and ONE signal:
  idempotency → subscription/connection gate → entitlement gate → equity →
  2% sizing → market order (idempotent) → stop-loss → record + consume quota.

force_close_user() closes all of a client's positions on lapse.

All Delta calls use the client's own decrypted key. Plaintext secrets stay in
local scope and are never logged.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from datetime import datetime, timezone

from . import entitlement, tradejini, tradejini_auth
from .crypto import decrypt_secret
from .delta import DeltaClient, DeltaError
from .models import ClientOrder, DeltaConnection, Signal, Subscription, TradejiniConnection, User
from .sizing import fno_lots, position_size_coins

log = logging.getLogger("executor")


def _client_for(conn: DeltaConnection) -> DeltaClient:
    return DeltaClient(conn.api_key, decrypt_secret(conn.api_secret_encrypted), sandbox=conn.sandbox)


def _coid(signal_id: str, user_id: str) -> str:
    # <=32 chars, deterministic → Delta-level idempotency on retries.
    return f"s{signal_id[:10]}u{user_id[:10]}"


def _record(db: Session, user_id: str, signal_id: str, coid: str, status: str,
            detail: str = "", size: float | None = None, fill_px: float | None = None,
            fee: float | None = None, delta_order_id: str | None = None) -> ClientOrder:
    co = ClientOrder(
        user_id=user_id, signal_id=signal_id, client_order_id=coid, status=status,
        detail=detail[:1000] if detail else None, size=size, fill_px=fill_px, fee=fee,
        delta_order_id=delta_order_id,
    )
    db.add(co)
    db.flush()

    if status in ("filled", "closed"):
        try:
            from .models import User, Signal, ClientProfitLedger
            u = db.get(User, user_id)
            s = db.get(Signal, signal_id)
            if u and s:
                ledger_entry = ClientProfitLedger(
                    user_id=u.id,
                    email=u.email,
                    name=u.name,
                    phone=u.phone,
                    client_id=u.client_id,
                    venue=s.venue or "delta",
                    symbol=s.symbol,
                    side=s.side,
                    size=float(size or 0.0),
                    entry_price=float(fill_px or s.ref_price or 0.0),
                    fee_usd=float(fee or 0.0),
                    status=status,
                    signal_id=signal_id,
                    order_id=delta_order_id or coid
                )
                db.add(ledger_entry)
                db.flush()
        except Exception as le:
            print(f"[Ledger] Error recording executor trade: {le}")

    return co


def execute_signal_for_user(db: Session, user: User, signal: Signal) -> str:
    """Returns a short status string. Idempotent per (signal, user)."""
    # 1. Idempotency — already attempted?
    existing = (
        db.query(ClientOrder)
        .filter(ClientOrder.signal_id == signal.id, ClientOrder.user_id == user.id)
        .one_or_none()
    )
    if existing is not None:
        return f"skip(dedupe:{existing.status})"

    coid = _coid(signal.id, user.id)

    # 2. Subscription + connection gate
    sub: Subscription | None = user.subscription
    conn: DeltaConnection | None = user.connection
    if sub is None:
        return "skip(no_sub)"
    if conn is None or conn.status != "connected" or conn.paused:
        return "skip(not_connected)"

    base = signal.symbol.split("/")[0] if "/" in signal.symbol else signal.symbol

    # 3. Read the client's CURRENT position on this symbol (their account is the
    # source of truth). Each signal is reconciled against the client's own state,
    # so entitlement-throttled divergence from the brain self-corrects:
    #   buy  → short:close · flat:open-long  · long:skip
    #   sell → long:close  · flat:open-short · short:skip
    #   close→ close whatever they hold
    try:
        client = _client_for(conn)
        positions = client.open_positions()
    except DeltaError as e:
        _record(db, user.id, signal.id, coid, "error", f"position read error: {e}")
        return "error(positions)"

    pos = next((p for p in positions if p["base"] == base), None)
    cur = None
    if pos is not None:
        s = (pos.get("side") or "").lower()
        cur = "long" if s in ("long", "buy") else "short" if s in ("short", "sell") else None

    # CLOSE signal, or buy/sell that reverses an existing position → reduce-only close.
    want = None if signal.side == "close" else ("long" if signal.side == "buy" else "short")
    if signal.side == "close" or (cur is not None and want is not None and cur != want):
        if cur is None:
            _record(db, user.id, signal.id, coid, "skipped_flat", "no position to close")
            return "skip(flat)"
        try:
            res = client.close_all(base)
            _record(db, user.id, signal.id, coid, "closed", f"close {base} ({cur}): {res}")
            return "closed"
        except DeltaError as e:
            _record(db, user.id, signal.id, coid, "error", f"close error: {e}")
            return "error(close)"

    # Already on the wanted side → nothing to do.
    if cur == want:
        _record(db, user.id, signal.id, coid, "skipped_have", f"already {cur}")
        return f"skip(already_{cur})"

    # exit_only take-profit: the opposite-position close above already cashed out
    # any holder. Reaching here means the client is FLAT — and an exit_only signal
    # (a vision-congested cross) must NEVER open a fresh position. Stop before the
    # open path, and don't consume entitlement.
    if signal.exit_only:
        _record(db, user.id, signal.id, coid, "skipped_exit_only", "exit-only: no opposite position to close")
        return "skip(exit_only_flat)"

    if not sub.is_active:
        _record(db, user.id, signal.id, coid, "skipped_expired", "subscription expired")
        return "skip(expired)"

    allowed, reason = entitlement.allows(db, user.id, sub.package, signal.side)
    if not allowed:
        _record(db, user.id, signal.id, coid, "skipped_entitlement", reason)
        return f"skip(entitlement:{reason})"

    try:
        equity = client.equity_usd()
    except DeltaError as e:
        _record(db, user.id, signal.id, coid, "error", f"equity error: {e}")
        return "error(equity)"

    entry = signal.ref_price or DeltaClient.ref_price(base)
    sl = signal.sl_price or 0.0
    size = position_size_coins(equity, entry, sl)
    if size <= 0:
        _record(db, user.id, signal.id, coid, "skipped_margin",
                f"size=0 (equity={equity:.2f} entry={entry} sl={sl})", size=0)
        return "skip(insufficient)"

    # 5. Place market order + stop-loss.
    try:
        fill = client.market_order(base, signal.side, size, reduce_only=False)
    except DeltaError as e:
        _record(db, user.id, signal.id, coid, "error", f"order error: {e}", size=size)
        return "error(order)"

    sl_detail = ""
    if sl > 0 and fill["filled"] > 0:
        try:
            sl_res = client.place_stop_loss(base, want, fill["filled"], sl)
            sl_detail = f" sl_oid={sl_res.get('delta_order_id')}"
        except DeltaError as e:
            sl_detail = f" sl_error={e}"

    # 6. Record + consume quota (only on a real open fill).
    _record(
        db, user.id, signal.id, coid, "filled",
        f"open {want} {base} sz={fill['filled']:.6f} @ {fill['avg_px']}{sl_detail}",
        size=fill["filled"], fill_px=fill["avg_px"], fee=fill["fee"],
        delta_order_id=fill["delta_order_id"],
    )
    entitlement.consume(db, user.id, sub.package, signal.side)
    return "filled"


def force_close_user(db: Session, conn: DeltaConnection) -> str:
    """Close every open position for a lapsed client (called by the worker sweep)."""
    try:
        client = _client_for(conn)
        positions = client.open_positions()
        for p in positions:
            client.close_all(p["base"])
        conn.status = "disconnected"
        return f"force_closed({len(positions)})"
    except DeltaError as e:
        return f"force_close_error({e})"


# ── Tradejini (Indian F&O) execution ───────────────────────────
def execute_tradejini_signal_for_user(db: Session, user: User, signal: Signal) -> str:
    """Per-client F&O execution. Same position-aware reconcile as Delta, but
    sized in whole LOTS from the client's INR margin."""
    existing = (
        db.query(ClientOrder)
        .filter(ClientOrder.signal_id == signal.id, ClientOrder.user_id == user.id)
        .one_or_none()
    )
    if existing is not None:
        return f"skip(dedupe:{existing.status})"
    coid = _coid(signal.id, user.id)

    sub = db.query(Subscription).filter(Subscription.user_id == user.id).one_or_none()
    conn = db.query(TradejiniConnection).filter(TradejiniConnection.user_id == user.id).one_or_none()
    if sub is None:
        return "skip(no_sub)"
    if conn is None or not tradejini_auth.has_auto_creds(conn) or conn.status == "disconnected" or conn.paused:
        return "skip(not_connected)"

    try:
        # auto-mint a fresh token if the current one lapsed — client connected once,
        # never re-auths; a refresh failure surfaces as a recorded error below.
        client = tradejini.TradejiniClient(tradejini_auth.ensure_client_token(db, conn), api_key=conn.api_key)
        meta = client.resolve(signal.symbol)
        positions = client.open_positions()
    except tradejini.TradejiniError as e:
        _record(db, user.id, signal.id, coid, "error", f"resolve/position error: {e}")
        return "error(positions)"

    sym_id, lot_size = meta["sym_id"], meta["lot_size"]
    pos = next((p for p in positions if p["sym_id"] == sym_id), None)
    cur = None
    if pos is not None:
        cur = "long" if pos["side"] == "buy" else "short"

    want = None if signal.side == "close" else ("long" if signal.side == "buy" else "short")
    # close / reverse → flatten
    if signal.side == "close" or (cur is not None and want is not None and cur != want):
        if cur is None:
            _record(db, user.id, signal.id, coid, "skipped_flat", "no position to close")
            return "skip(flat)"
        try:
            res = client.close_position(sym_id)
            _record(db, user.id, signal.id, coid, "closed", f"close {signal.symbol} ({cur}): {res}")
            return "closed"
        except tradejini.TradejiniError as e:
            _record(db, user.id, signal.id, coid, "error", f"close error: {e}")
            return "error(close)"
    if cur == want:
        _record(db, user.id, signal.id, coid, "skipped_have", f"already {cur}")
        return f"skip(already_{cur})"

    # exit_only take-profit (see Delta path): we only get here when FLAT. A
    # vision-congested cross must close opposite holders but never open. Skip.
    if signal.exit_only:
        _record(db, user.id, signal.id, coid, "skipped_exit_only", "exit-only: no opposite position to close")
        return "skip(exit_only_flat)"

    if not sub.is_active:
        _record(db, user.id, signal.id, coid, "skipped_expired", "subscription expired")
        return "skip(expired)"

    allowed, reason = entitlement.allows(db, user.id, sub.package, signal.side)
    if not allowed:
        _record(db, user.id, signal.id, coid, "skipped_entitlement", reason)
        return f"skip(entitlement:{reason})"
    try:
        equity = client.equity_inr()
    except tradejini.TradejiniError as e:
        _record(db, user.id, signal.id, coid, "error", f"equity error: {e}")
        return "error(equity)"

    entry = signal.ref_price or 0.0
    sl = signal.sl_price or 0.0
    lots, qty = fno_lots(equity, entry, sl, lot_size)
    if lots < 1 or qty < 1:
        _record(db, user.id, signal.id, coid, "skipped_margin",
                f"0 lots (margin=₹{equity:.0f} entry={entry} sl={sl} lot={lot_size})", size=0)
        return "skip(insufficient)"

    try:
        order_id = client.place_order(sym_id, signal.side, qty)
    except tradejini.TradejiniError as e:
        _record(db, user.id, signal.id, coid, "error", f"order error: {e}", size=qty)
        return "error(order)"

    # Protective stop-loss (mirrors Delta) — SL-M on the opposite side. NSE F&O
    # has no reduce-only flag, so close_position cancels this before flattening.
    sl_detail = ""
    if sl > 0:
        try:
            sl_oid = client.place_stop_loss(sym_id, want, qty, sl)
            sl_detail = f" sl_oid={sl_oid}"
        except tradejini.TradejiniError as e:
            sl_detail = f" sl_error={e}"

    _record(db, user.id, signal.id, coid, "filled",
            f"open {want} {signal.symbol} {lots}lot×{lot_size}={qty} oid={order_id}{sl_detail}",
            size=qty, delta_order_id=order_id)
    entitlement.consume(db, user.id, sub.package, signal.side)
    return "filled"


def force_close_tradejini(db: Session, conn: TradejiniConnection) -> str:
    try:
        client = tradejini.TradejiniClient(tradejini_auth.ensure_client_token(db, conn), api_key=conn.api_key)
        positions = client.open_positions()
        for p in positions:
            client.close_position(p["sym_id"])
        conn.status = "disconnected"
        return f"force_closed({len(positions)})"
    except tradejini.TradejiniError as e:
        return f"force_close_error({e})"
