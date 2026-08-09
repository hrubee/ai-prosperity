"""Signal bus — the brain publishes direction-only signals here; the worker
consumes them. Keeping this as a thin function means the brain integration is a
single call (HTTP POST /internal/signals, or a direct DB insert in-process)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .models import Signal

# Both brain wallets (Delta + Binance) run the same orchestrator and emit the
# same cross each cycle. Collapse identical (symbol, side) signals published
# within one 5m candle so the executor fans each market event out exactly once.
DEDUP_WINDOW_SECONDS = 240


def publish_signal(
    db: Session,
    symbol: str,
    side: str,
    sl_price: float | None = None,
    ref_price: float | None = None,
    source: str = "brain",
    venue: str = "delta",
    exit_only: bool = False,
) -> Signal:
    side = side.lower()
    if side not in ("buy", "sell", "close"):
        raise ValueError(f"invalid side: {side}")
    venue = (venue or "delta").lower()

    # exit_only is part of the dedup key: a vision-congested cross publishes an
    # exit_only take-profit, but a later clean cross on the same candle (or a
    # full entry signal) must NOT be collapsed into it — they do different work.
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=DEDUP_WINDOW_SECONDS)
    dup = (
        db.query(Signal)
        .filter(Signal.symbol == symbol, Signal.side == side, Signal.venue == venue,
                Signal.exit_only == exit_only, Signal.created_at >= cutoff)
        .order_by(Signal.created_at.desc())
        .first()
    )
    if dup is not None:
        return dup  # already published this candle — reuse, do not duplicate

    sig = Signal(
        symbol=symbol, side=side, sl_price=sl_price, ref_price=ref_price,
        source=source, venue=venue, exit_only=exit_only,
    )
    db.add(sig)
    db.flush()
    return sig
