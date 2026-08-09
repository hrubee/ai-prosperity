"""Phase-4 executor — turns StraddleEngine actions into REAL Tradejini orders,
fanned out to EVERY connected client, each sized to their own wallet.

ONE engine decides ("buy CE 24000 @ 09:41", "sell CE @ 10:15"); each decision is
placed on every connected Tradejini account, sized to that account's margin
(~4 lots per ₹1L). Per-client error isolation: one client's reject never blocks
the others.

GATING (safe by construction):
  * ``settings.straddle_live`` is the master switch. Default 0 ⇒ no client list is
    ever built ⇒ the runner stays in pure shadow mode. Deploying the code places
    nothing until this is consciously turned on.
  * ``settings.straddle_canary_email`` (optional) restricts the fan-out to that ONE
    account — run on your own account first without touching clients. Blank ⇒ all
    connected clients.

SAFETY INVARIANTS (do not weaken):
  * Every SELL sizes from the broker's live ``netQty`` (close_position), NEVER from
    our bookkeeping. An oversell flips a long into a naked short (unlimited risk);
    a missed close is only bounded P&L drag on an already-funded long. THE
    invariant.
  * A durable ``StraddlePosition`` ``intent`` row is committed BEFORE each buy, so
    a crash mid-order can't orphan an untracked leg (square-off + reconcile read
    these rows).
  * Restart / idempotency guard: a BUY is skipped if an OPEN row already exists
    for that client+leg+day — the engine re-arms fresh after a restart and could
    otherwise stack a second position on one already held.
  * Per-client error isolation: a reject records an ``error`` row and is swallowed.
  * Reconcile is DB state-sync ONLY — it never places an order.
"""
from __future__ import annotations

import concurrent.futures
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from .config import settings
from .db import session_scope
from .models import StraddlePosition, TradejiniConnection, User
from . import tradejini, tradejini_auth
from .straddle_exec_helpers import limit_buy_price, limit_sell_price

log = logging.getLogger("straddle-exec")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ist_today() -> str:
    return (_utcnow() + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


def _live_conns(db) -> list[TradejiniConnection]:
    """The connections this executor may act on. Empty unless live OR dry-run is
    enabled (dry-run still builds the list to exercise resolution+sizing, but
    places nothing). All connected+funded clients, OR — when
    ``straddle_canary_email`` is set — only those account(s). The setting may be a
    COMMA-SEPARATED list to restrict to several specific accounts. Skips
    disconnected/paused/no-creds."""
    if not (settings.straddle_live or settings.straddle_dry_run):
        return []
    q = select(TradejiniConnection)
    restrict = (settings.straddle_canary_email or "").strip().lower()
    if restrict:
        emails = [e.strip() for e in restrict.split(",") if e.strip()]
        users = db.execute(
            select(User).where(func.lower(User.email).in_(emails))).scalars().all()
        if not users:
            return []
        q = q.where(TradejiniConnection.user_id.in_([u.id for u in users]))
    out = []
    for c in db.execute(q).scalars().all():
        if not tradejini_auth.has_auto_creds(c) or c.status == "disconnected" or c.paused:
            continue
        out.append(c)
    return out


def _email_of(db, user_id: str) -> str:
    u = db.get(User, user_id)
    return (u.email if u else user_id) or user_id


class StraddleClientExecutor:
    """Callable matching the runner's ``executor(action, meta)`` hook. Fans every
    engine action to all live clients, sized per wallet. Construct AFTER the weekly
    chain is known (needs the expiry to resolve the exact Tradejini contract)."""

    def __init__(self, expiry: str, dry_run: bool = False):
        self.expiry = str(expiry)[:10]
        self.dry_run = dry_run
        self._symcache: dict = {}                 # (leg, strike) -> {sym_id, symbol, lot}
        self.sized: list[dict] = self._size_clients()   # [{user_id, email, lots}]
        self.enabled = bool(self.sized)
        # Phase 3: per-client OrderFeeds for instant fill detection (lazy init)
        self._order_feeds: dict[str, object] = {}  # user_id -> OrderFeed

    # ── per-client sizing (once, at session start) ────────────
    def _size_clients(self) -> list[dict]:
        sized = []
        with session_scope() as db:
            conns = _live_conns(db)
            if not conns:
                log.warning("straddle: no live clients (straddle_live=%s, canary=%r) — SHADOW",
                            settings.straddle_live, settings.straddle_canary_email)
                return []
            cap = settings.straddle_capital_per_lot_inr
            maxlots = settings.straddle_max_lots
            for c in conns:
                email = _email_of(db, c.user_id)
                try:
                    cl = tradejini.TradejiniClient(
                        tradejini_auth.ensure_client_token(db, c), api_key=c.api_key)
                    # Size by BUYABLE CASH, not total margin — the straddle BUYS
                    # options, and option-buy premium can't be paid from pledged
                    # collateral. A collateral-only account has cash=0 and every
                    # buy would reject, so it must be skipped (with a clear alert),
                    # never sized off its margin.
                    cash = cl.buyable_cash_inr()
                except Exception as e:
                    log.warning("straddle sizing skip %s: %s", email, str(e)[:120])
                    continue
                lots = int(cash // cap) if cap > 0 else 0
                if maxlots > 0:
                    lots = min(lots, maxlots)
                if lots < 1:
                    log.warning("straddle sizing %s: buyable cash Rs.%.0f < 1 lot (Rs.%.0f) "
                                "— SKIPPING (option buying needs cash, not collateral)",
                                email, cash, cap)
                    continue
                sized.append({"user_id": c.user_id, "email": email, "lots": lots})
        if sized:
            summary = " | ".join(f"{s['email']}: {s['lots']} lot" for s in sized)
            log.info("straddle ARMED — %d client(s): %s%s",
                     len(sized), summary, " [DRY-RUN]" if self.dry_run else "")
        return sized

    # ── ownership primitive (for the guardian heartbeat) ──────
    def managed_keys(self, open_legs: set) -> list[str]:
        """The set of positions THIS runner is actively managing right now, as
        ``"<user_id>|<sym_id>"`` strings — one per (sized client × engine-open leg).

        Per (user_id, sym_id), NOT per contract: one engine fans the same two
        strikes to every sized client, but a client SKIPPED in sizing (e.g. cash
        tied up in an existing position after a restart) is genuinely not being
        managed — so it must NOT appear here, even though its sym_id is. The
        guardian keys off (conn.user_id, sym_id) so a skipped client's leftover is
        correctly an orphan. Only resolved (already-traded) legs have a sym_id in
        the cache, which is exactly the set that can have a live broker position."""
        syms = {v["sym_id"] for (leg, _strike), v in self._symcache.items()
                if leg in open_legs and v.get("sym_id")}
        return sorted(f"{s['user_id']}|{sym}" for s in self.sized for sym in syms)

    # ── Phase 3: OrderFeed helpers ────────────────────────────
    def _get_order_feed(self, db, user_id: str):
        """Get or create an OrderFeed for this client (lazy)."""
        if not hasattr(self, "_order_feeds"):
            self._order_feeds = {}
        if user_id in self._order_feeds:
            return self._order_feeds[user_id]
        conn = self._conn_for(db, user_id)
        if conn is None:
            return None
        try:
            token = tradejini_auth.ensure_client_token(db, conn)
        except Exception:
            # conn mock may not be a real TradejiniConnection (e.g. in tests)
            return None
        from .tradejini_data import OrderFeed
        feed = OrderFeed(conn.api_key, token)
        try:
            feed.start()
        except Exception as e:
            log.warning("straddle OrderFeed start failed for %s: %s", user_id, e)
            return None
        self._order_feeds[user_id] = feed
        return feed

    def _wait_for_fill_via_ws(self, feed, order_id: str, timeout: float = 10.0) -> dict | None:
        """Wait for order fill via WebSocket (instant) or return None on timeout."""
        if feed is None:
            return None
        try:
            return feed.wait_for_fill(order_id, timeout_sec=timeout)
        except Exception as e:
            log.warning("straddle wait_for_fill error: %s", e)
            return None

    # ── the runner hook ───────────────────────────────────────
    def __call__(self, action: dict, meta: dict) -> None:
        if not self.enabled:
            return
        leg, strike = action["leg"], float(meta["strike"])
        try:
            sym = self._resolve_sym(leg, strike)
        except Exception as e:
            log.warning("straddle resolve %s %s failed (no orders this action): %s",
                        leg, strike, str(e)[:140])
            return
        lot_size = int(meta["lot"])

        # Parallel fan-out: concurrent order placement across all sized clients
        # Using ThreadPoolExecutor to eliminate sequential per-client latency
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.sized)) as ex:
            futures = {}
            for s in self.sized:
                if action.get("side") == "buy":
                    futures[ex.submit(self._buy_one, s, leg, strike, sym, lot_size, action)] = s
                else:
                    futures[ex.submit(self._sell_one, s, leg, strike, sym, action)] = s
            for fut in concurrent.futures.as_completed(futures):
                s = futures[fut]
                try:
                    fut.result()
                except Exception as e:
                    log.warning("straddle %s %s %s error: %s",
                                action.get("side"), leg, s["email"], str(e)[:140])

    # ── helpers ───────────────────────────────────────────────
    def _client(self, db, conn) -> tradejini.TradejiniClient:
        return tradejini.TradejiniClient(
            tradejini_auth.ensure_client_token(db, conn), api_key=conn.api_key)

    def _resolve_sym(self, leg: str, strike: float) -> dict:
        key = (leg, round(float(strike), 2))
        cached = self._symcache.get(key)
        if cached is not None:
            return cached
        with session_scope() as db:
            conns = _live_conns(db)
            if not conns:
                raise tradejini.TradejiniError("no live client to resolve with")
            meta = self._client(db, conns[0]).resolve_weekly_option(
                "NIFTY", self.expiry, strike, leg)
        val = {"sym_id": meta["sym_id"], "symbol": meta.get("symbol"), "lot": meta.get("lot_size")}
        self._symcache[key] = val
        return val

    def _conn_for(self, db, user_id: str) -> TradejiniConnection | None:
        c = db.execute(
            select(TradejiniConnection).where(TradejiniConnection.user_id == user_id)
        ).scalars().first()
        if c is None or not tradejini_auth.has_auto_creds(c) or c.status == "disconnected":
            return None
        return c

    def _day_realized_proxy(self, db, user_id: str, today: str) -> float:
        """Sum (exit-entry)*qty over today's CLOSED rows for the client — a proxy
        for booked P&L used only by the daily-loss circuit breaker."""
        rows = db.execute(
            select(StraddlePosition).where(
                StraddlePosition.user_id == user_id,
                StraddlePosition.trade_date == today,
                StraddlePosition.status == "closed",
            )).scalars().all()
        tot = 0.0
        for r in rows:
            if r.entry_px is not None and r.exit_px is not None:
                tot += (r.exit_px - r.entry_px) * (r.qty or 0)
        return tot

    # ── buy (open / re-entry) ─────────────────────────────────
    def _buy_one(self, s: dict, leg: str, strike: float, sym: dict, lot_size: int,
                 action: dict) -> None:
        user_id, email = s["user_id"], s["email"]
        # Size off the BROKER's lot (Tradejini-resolved sym["lot"]) — the venue we
        # actually trade — not the Kite-derived meta lot. Today both are 65; if a
        # lot-size revision ever lands on one feed before the other, the Kite lot
        # would build an invalid Tradejini qty. Alert + use the broker's lot.
        broker_lot = int(sym.get("lot") or 0)
        if broker_lot and broker_lot != int(lot_size):
            log.warning("straddle BUY %s %s: LOT MISMATCH Kite=%d Tradejini=%d — using broker lot",
                        leg, email, int(lot_size), broker_lot)
        qty = (broker_lot or int(lot_size)) * int(s["lots"])
        today = _ist_today()

        # IOC limit price: engine trigger*(1+buffer) rounded UP to tick
        buffer_pct = settings.straddle_ioc_buffer_pct if hasattr(settings, "straddle_ioc_buffer_pct") else 0.01
        ref_premium = action.get("premium")
        limit_px = limit_buy_price(ref_premium, buffer_pct) if ref_premium else 0.0

        # Get OrderFeed for this client (lazy init)
        with session_scope() as db:
            feed = self._get_order_feed(db, user_id)

        # Single transaction: intent + order + outcome (dry-run still fast-path)
        with session_scope() as db:
            conn = self._conn_for(db, user_id)
            if conn is None:
                return
            # idempotency / restart guard: already holding this leg ⇒ don't stack
            existing = db.execute(
                select(StraddlePosition.id).where(
                    StraddlePosition.user_id == user_id,
                    StraddlePosition.trade_date == today,
                    StraddlePosition.leg == leg,
                    StraddlePosition.sym_id == sym["sym_id"],
                    StraddlePosition.status == "open",
                )).first()
            if existing is not None:
                log.info("straddle BUY %s %s skipped — already open (idempotent)", leg, email)
                return
            # per-client daily-loss circuit breaker
            cap = settings.straddle_daily_loss_cap_inr
            if cap > 0 and self._day_realized_proxy(db, user_id, today) <= -abs(cap):
                log.warning("straddle BUY %s %s skipped — daily loss cap hit", leg, email)
                return
            row = StraddlePosition(
                user_id=user_id, trade_date=today, leg=leg, strike=strike,
                expiry=self.expiry, sym_id=sym["sym_id"], symbol=sym["symbol"],
                qty=qty, status="intent", reason=action.get("reason"),
                entry_px=action.get("premium"))
            db.add(row)
            db.flush()
            rid = row.id

            if self.dry_run:
                row.status, row.entry_order_id, row.detail = "open", "DRY", "dry_run"
                log.info("straddle DRY BUY %s %s strike=%.0f sym=%s qty=%d (%d lots) — NO ORDER",
                         leg, email, strike, sym["symbol"], qty, int(s["lots"]))
                return

            # Place IOC LIMIT order (bounded slippage by construction)
            try:
                oid = self._client(db, conn).place_order(
                    sym["sym_id"], "buy", qty, product=settings.straddle_product,
                    order_type="limit", limit_price=limit_px)
                # Phase 3: Wait for fill via WebSocket (instant confirmation)
                fill_info = self._wait_for_fill_via_ws(feed, oid, timeout=10.0)
                if fill_info and fill_info["status"] in ("complete", "completed", "filled", "executed"):
                    row.status, row.entry_order_id, row.opened_at = "open", str(oid), _utcnow()
                    row.detail = f"filled_qty={fill_info.get('fill_qty', qty)};avg_px={fill_info.get('avg_px', limit_px)}"
                    log.info("straddle BUY %s %s qty=%d oid=%s limit=%.2f FILLED (WS) qty=%d avg=%.2f",
                             leg, email, qty, oid, limit_px, fill_info.get('fill_qty', qty), fill_info.get('avg_px', limit_px))
                elif fill_info and fill_info["status"] in ("cancelled", "canceled", "rejected"):
                    row.status, row.detail = "error", f"order {fill_info['status']}: {fill_info.get('reason','')}"
                    log.warning("straddle BUY %s %s REJECTED: %s", leg, email, row.detail)
                else:
                    # Timeout or unknown — mark open but unknown fill status for reconcile
                    row.status, row.entry_order_id, row.opened_at = "open", str(oid), _utcnow()
                    row.detail = "ws_timeout_or_unknown"
                    log.warning("straddle BUY %s %s oid=%s limit=%.2f — fill status UNKNOWN (WS timeout), will reconcile",
                                leg, email, oid, limit_px)
            except Exception as e:
                err = str(e)[:160]
                row.status, row.detail = "error", f"buy: {err}"
                log.warning("straddle BUY %s %s FAILED: %s", leg, email, err)

    # ── sell (stop-out / square-off / trail exit) ─────────────
    def _sell_one(self, s: dict, leg: str, strike: float, sym: dict, action: dict) -> None:
        user_id, email = s["user_id"], s["email"]
        today = _ist_today()

        # IOC limit price: engine trigger*(1-buffer) rounded DOWN to tick
        buffer_pct = settings.straddle_ioc_buffer_pct if hasattr(settings, "straddle_ioc_buffer_pct") else 0.01
        ref_premium = action.get("premium")
        limit_px = limit_sell_price(ref_premium, buffer_pct) if ref_premium else 0.0

        # Get OrderFeed for this client (lazy init)
        with session_scope() as db:
            feed = self._get_order_feed(db, user_id)

        # Single transaction: find open row + place limit order + update outcome
        with session_scope() as db:
            conn = self._conn_for(db, user_id)
            if conn is None:
                return
            row = db.execute(
                select(StraddlePosition).where(
                    StraddlePosition.user_id == user_id,
                    StraddlePosition.trade_date == today,
                    StraddlePosition.leg == leg,
                    StraddlePosition.sym_id == sym["sym_id"],
                    StraddlePosition.status == "open",
                ).order_by(StraddlePosition.opened_at.desc())
            ).scalars().first()

            if self.dry_run:
                if row is not None:
                    row.status, row.exit_px, row.exit_order_id, row.closed_at = (
                        "closed", action.get("premium"), "DRY", _utcnow())
                log.info("straddle DRY SELL %s %s sym=%s — NO ORDER", leg, email, sym["symbol"])
                return

            # BROKER-QTY INVARIANT: close_position reads live netQty and sells
            # EXACTLY that — never our `qty`. No position ⇒ no order (a failed buy
            # therefore never fires a phantom sell). A REJECTED close (e.g. market
            # protection tripping in a fast move) RAISES — we must NOT mark the row
            # closed, or the engine/books read "flat" while the broker is still
            # LONG and unmanaged. Keep it open, note it, alert; the guardian + the
            # 15:25 square-off re-scan live netQty and retry.
            close_failed = None
            try:
                # Use IOC LIMIT order for bounded slippage (mirrors buy side)
                res = self._client(db, conn).close_position(
                    sym["sym_id"], product=settings.straddle_product,
                    limit_price=limit_px if limit_px > 0 else None)
                order_id = str(res.get("order_id") or "")
                # Phase 3: Wait for fill via WebSocket (instant confirmation)
                fill_info = self._wait_for_fill_via_ws(feed, order_id, timeout=10.0) if order_id else None
                if fill_info and fill_info["status"] in ("complete", "completed", "filled", "executed"):
                    if row is not None:
                        row.status, row.exit_px = "closed", action.get("premium")
                        row.exit_order_id, row.closed_at = order_id, _utcnow()
                        row.detail = f"filled_qty={fill_info.get('fill_qty', 0)};avg_px={fill_info.get('avg_px', 0)}"
                    log.info("straddle SELL %s %s oid=%s limit=%.2f FILLED (WS) qty=%d avg=%.2f",
                             leg, email, order_id, limit_px, fill_info.get('fill_qty', 0), fill_info.get('avg_px', 0))
                elif fill_info and fill_info["status"] in ("cancelled", "canceled", "rejected"):
                    close_failed = f"order {fill_info['status']}: {fill_info.get('reason','')}"
                    if row is not None:
                        row.detail = ((row.detail or "") + f";sell-FAILED:{close_failed}")[:480]
                    log.warning("straddle SELL %s %s REJECTED: %s", leg, email, close_failed)
                else:
                    # Timeout or unknown — don't mark closed, let guardian/15:25 square-off handle
                    if row is not None:
                        row.detail = ((row.detail or "") + ";sell:ws_timeout_or_unknown")[:480]
                    log.warning("straddle SELL %s %s oid=%s limit=%.2f — fill status UNKNOWN (WS timeout), will retry via guardian/15:25",
                                leg, email, order_id, limit_px)
                    res = None
            except Exception as e:
                close_failed = str(e)[:120]
                if row is not None:
                    row.detail = ((row.detail or "") + f";sell-FAILED:{close_failed}")[:480]
                res = None
        if close_failed is not None:   # alert AFTER releasing the DB session
            log.warning("straddle SELL %s %s CLOSE FAILED: %s", leg, email, close_failed)
            try:
                from . import telegram_notify
                telegram_notify.send_message(
                    f"\U0001f6d1 {email}: CLOSE FAILED {leg} {sym.get('symbol')} — {close_failed} "
                    "(leg may still be OPEN; 15:25 square-off + guardian will retry)")
            except Exception:
                pass
        else:
            log.info("straddle SELL %s %s -> %s", leg, email, res)


# ── restart reconcile — DB STATE-SYNC ONLY, NEVER places an order ──
def reconcile_on_restart() -> dict:
    """After a process restart, make today's rows agree with each client's live
    broker positions so square-off + reporting are correct. Pure bookkeeping — it
    NEVER places an order (only the engine and the crash-safe square-off may)."""
    today = _ist_today()
    total_rows, total_fixed, clients = 0, 0, 0
    with session_scope() as db:
        conns = _live_conns(db)
        for conn in conns:
            rows = db.execute(
                select(StraddlePosition).where(
                    StraddlePosition.user_id == conn.user_id,
                    StraddlePosition.trade_date == today,
                    StraddlePosition.status.in_(["intent", "open", "error"]),
                )).scalars().all()
            if not rows:
                continue
            clients += 1
            total_rows += len(rows)
            try:
                cl = tradejini.TradejiniClient(
                    tradejini_auth.ensure_client_token(db, conn), api_key=conn.api_key)
                live = {p["sym_id"] for p in cl.open_positions()}
            except Exception as e:
                log.warning("reconcile %s: positions read failed: %s",
                            _email_of(db, conn.user_id), str(e)[:120])
                continue
            for r in rows:
                at_broker = r.sym_id in live
                if r.status == "intent" and at_broker:
                    r.status, r.detail = "open", ((r.detail or "") + ";reconciled-fill")[:480]
                    total_fixed += 1
                elif r.status == "intent" and not at_broker:
                    r.status, r.detail = "error", ((r.detail or "") + ";intent-never-filled")[:480]
                    total_fixed += 1
                elif r.status == "open" and not at_broker:
                    r.status, r.closed_at = "closed", _utcnow()
                    r.detail = ((r.detail or "") + ";reconciled-flat")[:480]
                    total_fixed += 1
                elif r.status == "error" and at_broker:
                    # accept-then-read-failure: the order was marked `error` but it
                    # actually FILLED — recover it to `open` so it is managed/squared.
                    r.status, r.detail = "open", ((r.detail or "") + ";reconciled-error-filled")[:480]
                    total_fixed += 1
    return {"reconciled": total_fixed, "rows": total_rows, "clients": clients}
