"""AI Vision activity log — persists one BrainEvent per fresh cross the F&O brain
evaluates, so the admin panel can show what the model saw and decided.

Secondary to trading: record() runs in its OWN transaction and swallows every
error, so a chart-encode or DB hiccup can never roll back or block the signal
that was already published in a separate transaction.
"""
from __future__ import annotations

import base64
import logging
import os

from .db import session_scope
from .models import BrainEvent

log = logging.getLogger("brain-log")

MAX_EVENTS = 300  # keep the newest N; older rows pruned after each insert


def _read_chart_b64(path: str | None) -> str | None:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    except Exception as e:
        log.warning("could not read chart %s: %s", path, e)
        return None


def existing_ids(ids: list[str]) -> set[str]:
    """Which of `ids` are already recorded — lets the crypto bridge dedup before
    insert (deterministic ids make tailing idempotent without an offset file)."""
    if not ids:
        return set()
    try:
        with session_scope() as db:
            return {r[0] for r in db.query(BrainEvent.id).filter(BrainEvent.id.in_(ids)).all()}
    except Exception as e:
        log.warning("brain_log.existing_ids failed: %s", e)
        return set()


def record(*, instrument: str, tj_symbol: str, side: str | None, ref_price: float | None,
           sl_price: float | None, atr: float | None, red_dots: int | None,
           vision_evaluated: bool, congested: bool | None, vision_reason: str | None,
           visual_sl: float | None, action: str, signal_id: str | None,
           chart_path: str | None, source: str = "nifty-brain",
           event_id: str | None = None, ts: object = None) -> None:
    """Insert a BrainEvent (own txn) + prune. Best-effort — never raises. Pass an
    explicit event_id (deterministic hash) for idempotent crypto-bridge inserts."""
    try:
        chart_b64 = _read_chart_b64(chart_path)
        kwargs = dict(
            source=source, instrument=instrument, tj_symbol=tj_symbol, side=(side or None),
            ref_price=ref_price, sl_price=sl_price or None, atr=atr, red_dots=red_dots,
            vision_evaluated=vision_evaluated, congested=congested,
            vision_reason=(vision_reason or None), visual_sl=(visual_sl or None),
            action=action, signal_id=signal_id, chart_b64=chart_b64)
        if event_id:
            kwargs["id"] = event_id
        if ts is not None:
            kwargs["ts"] = ts
        with session_scope() as db:
            db.add(BrainEvent(**kwargs))
            # prune: keep the newest MAX_EVENTS by ts
            ids_keep = [r[0] for r in db.query(BrainEvent.id)
                        .order_by(BrainEvent.ts.desc()).limit(MAX_EVENTS).all()]
            if len(ids_keep) >= MAX_EVENTS:
                db.query(BrainEvent).filter(BrainEvent.id.notin_(ids_keep)).delete(
                    synchronize_session=False)
    except Exception as e:
        log.warning("brain_log.record failed (non-fatal): %s", e)
