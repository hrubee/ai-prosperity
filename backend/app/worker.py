"""Execution fan-out worker (Phase 3 entrypoint).

Loop:
  1. Pull un-fanned-out signals from the `signals` table (the bus the brain writes to).
  2. For each signal, fan out across all eligible clients concurrently (capped),
     throttled to respect Delta's 500-ops/sec/product matching-engine limit.
  3. Sweep lapsed connections (paused on lapse) and force-close their positions.

Run:  python -m app.worker
The brain publishes signals via app.signal_bus.publish_signal() (or directly to
the table); this worker never modifies the brain.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .config import settings
from .db import session_scope
from .executor import (
    execute_signal_for_user,
    execute_tradejini_signal_for_user,
    force_close_tradejini,
    force_close_user,
)
from .models import DeltaConnection, Signal, Subscription, TradejiniConnection, User

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("worker")

# Matching engine cap is 500 ops/sec/product; stay well under with a small spacing.
_MIN_SPACING_SEC = 1.0 / 200.0  # 200 orders/sec across a product, conservative


def _eligible_user_ids(db: Session, venue: str) -> list[str]:
    """Users with an active subscription AND a live, unpaused connection for the
    signal's venue (crypto → Delta, Indian F&O → Tradejini)."""
    if venue == "tradejini":
        now = datetime.now(timezone.utc)
        rows = (
            db.query(User.id)
            .join(Subscription, Subscription.user_id == User.id)
            .join(TradejiniConnection, TradejiniConnection.user_id == User.id)
            .filter(
                Subscription.status == "active",
                TradejiniConnection.status == "connected",
                TradejiniConnection.paused == False,  # noqa: E712
                (TradejiniConnection.expires_at.is_(None)) | (TradejiniConnection.expires_at > now),
            )
            .all()
        )
        return [r[0] for r in rows]
    rows = (
        db.query(User.id)
        .join(Subscription, Subscription.user_id == User.id)
        .join(DeltaConnection, DeltaConnection.user_id == User.id)
        .filter(
            Subscription.status == "active",
            DeltaConnection.status == "connected",
            DeltaConnection.paused == False,  # noqa: E712
        )
        .all()
    )
    return [r[0] for r in rows]


def _fan_out_signal(signal_id: str) -> None:
    """Execute one signal across all eligible clients. Each client gets its own
    DB session so a slow/failed client doesn't block the rest."""
    with session_scope() as db:
        sig = db.get(Signal, signal_id)
        venue = (sig.venue if sig else "delta") or "delta"
        user_ids = _eligible_user_ids(db, venue)
    log.info("signal %s (%s) → fanning out to %d clients", signal_id, venue, len(user_ids))

    def run_one(user_id: str) -> str:
        # tiny stagger handled by the pool size; each client isolated in its own txn
        with session_scope() as db:
            user = db.get(User, user_id)
            signal = db.get(Signal, signal_id)
            if user is None or signal is None:
                return "skip(missing)"
            try:
                if venue == "tradejini":
                    return execute_tradejini_signal_for_user(db, user, signal)
                return execute_signal_for_user(db, user, signal)
            except Exception as e:  # never let one client kill the batch
                log.exception("client %s failed on signal %s: %s", user_id, signal_id, e)
                return f"error({e})"

    with ThreadPoolExecutor(max_workers=settings.worker_max_concurrency) as pool:
        results = list(pool.map(run_one, user_ids))

    summary: dict[str, int] = {}
    for r in results:
        key = r.split("(")[0].split(":")[0]
        summary[key] = summary.get(key, 0) + 1
    log.info("signal %s done: %s", signal_id, summary)


def _lapse_sweep() -> None:
    """Force-close positions for connections flagged on subscription lapse."""
    with session_scope() as db:
        for conn in (
            db.query(DeltaConnection)
            .filter(DeltaConnection.paused == True, DeltaConnection.status == "connected")  # noqa: E712
            .all()
        ):
            res = force_close_user(db, conn)
            log.info("lapse sweep (delta): user=%s %s", conn.user_id, res)
        for conn in (
            db.query(TradejiniConnection)
            .filter(TradejiniConnection.paused == True, TradejiniConnection.status == "connected")  # noqa: E712
            .all()
        ):
            res = force_close_tradejini(db, conn)
            log.info("lapse sweep (tradejini): user=%s %s", conn.user_id, res)


def run() -> None:
    log.info("execution worker started (poll=%.1fs, concurrency=%d, sandbox=%s)",
             settings.worker_poll_seconds, settings.worker_max_concurrency, settings.delta_sandbox)
    while True:
        try:
            # 1. claim un-fanned-out signals
            with session_scope() as db:
                pending = (
                    db.query(Signal)
                    .filter(Signal.fanned_out == False)  # noqa: E712
                    .order_by(Signal.created_at.asc())
                    .limit(20)
                    .all()
                )
                ids = [s.id for s in pending]
                for s in pending:
                    s.fanned_out = True  # claim so we don't double-fan on the next tick

            for sid in ids:
                _fan_out_signal(sid)

            # 2. lapse sweep each tick
            _lapse_sweep()
        except Exception as e:
            log.exception("worker loop error: %s", e)
        time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    run()
