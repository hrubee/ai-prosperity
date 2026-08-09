"""exit_only reconcile — the EMA opposite cross is ALWAYS the take-profit.

When the FNO/crypto brain emits a vision-CONGESTED cross it publishes the signal
`exit_only=True`: too choppy to OPEN a fresh position, but any client holding the
OPPOSITE side must still be cashed out on this opposite EMA cross. These tests pin
the executor invariant for BOTH venues (Delta + Tradejini):

    exit_only + holding opposite  -> CLOSE (take profit)
    exit_only + flat              -> SKIP  (never opens, never burns entitlement)
    exit_only + already aligned   -> SKIP  (leave the trend position alone)

and prove a normal (exit_only=False) signal still reaches the open path. No
network: clients are faked, DB is in-memory SQLite.

Run:  .venv/bin/python -m pytest tests/test_executor_exit_only.py   (or run the file)
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app import executor  # noqa: E402
from app.models import Base, ClientOrder, DeltaConnection, Signal, Subscription, TradejiniConnection, User  # noqa: E402


# ── in-memory DB ────────────────────────────────────────────────
def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)()


def _user_with_delta(db) -> User:
    u = User(email="c@example.com", role="user")
    db.add(u); db.flush()
    db.add(Subscription(user_id=u.id, package="pro", status="active"))
    db.add(DeltaConnection(user_id=u.id, api_key="k", api_secret_encrypted="enc",
                           status="connected", paused=False, sandbox=True))
    db.commit()
    return db.get(User, u.id)


def _user_with_tradejini(db) -> User:
    u = User(email="f@example.com", role="user")
    db.add(u); db.flush()
    db.add(Subscription(user_id=u.id, package="pro", status="active"))
    db.add(TradejiniConnection(user_id=u.id, access_token_encrypted="enc",
                               status="connected", paused=False))
    db.commit()
    return db.get(User, u.id)


def _signal(db, symbol, side, exit_only=False) -> Signal:
    sig = Signal(symbol=symbol, side=side, ref_price=100.0, sl_price=98.0,
                 venue="delta", exit_only=exit_only)
    db.add(sig); db.commit()
    return db.get(Signal, sig.id)


def _last_order(db, status_in=None) -> ClientOrder:
    rows = db.query(ClientOrder).all()
    return rows[-1] if rows else None


# ── fakes ───────────────────────────────────────────────────────
class FakeDelta:
    def __init__(self, positions):
        self._pos = positions
        self.closed, self.opened = [], []

    def open_positions(self):
        return self._pos

    def close_all(self, base):
        self.closed.append(base)
        return {"closed": base}

    def equity_usd(self):
        return 1000.0

    def market_order(self, base, side, size, reduce_only=False):
        self.opened.append((base, side, size))
        return {"filled": size, "avg_px": 100.0, "fee": 0.1, "delta_order_id": "oid"}

    def place_stop_loss(self, *a, **k):
        return {"delta_order_id": "sl"}


class FakeTJ:
    last = None

    def __init__(self, token):
        self.closed, self.placed = [], []
        FakeTJ.last = self
        self._pos = FakeTJ._next_positions

    def resolve(self, symbol):
        return {"sym_id": "SYM1", "lot_size": 50}

    def open_positions(self):
        return self._pos

    def close_position(self, sym_id):
        self.closed.append(sym_id)
        return {"closed": sym_id}

    def equity_inr(self):
        return 500000.0

    def place_order(self, sym_id, side, qty):
        self.placed.append((sym_id, side, qty))
        return "tj-oid"

    def place_stop_loss(self, *a, **k):
        return "tj-sl"


FakeTJ._next_positions = []


# ── Delta ───────────────────────────────────────────────────────
def _run_delta(monkeypatch_positions, side, exit_only):
    db = _session()
    user = _user_with_delta(db)
    fake = FakeDelta(monkeypatch_positions)
    orig = executor._client_for
    executor._client_for = lambda conn: fake
    try:
        sig = _signal(db, "BTC/USDT", side, exit_only=exit_only)
        result = executor.execute_signal_for_user(db, user, sig)
        db.commit()
    finally:
        executor._client_for = orig
    return result, fake, _last_order(db)


def test_delta_exit_only_closes_opposite_long():
    # bearish cross (sell) while client is LONG → take profit (close), never reverse.
    result, fake, order = _run_delta([{"base": "BTC", "side": "long"}], "sell", exit_only=True)
    assert result == "closed", result
    assert fake.closed == ["BTC"], fake.closed
    assert fake.opened == [], fake.opened
    assert order.status == "closed", order.status


def test_delta_exit_only_closes_opposite_short():
    # bullish cross (buy) while client is SHORT → take profit (close).
    result, fake, order = _run_delta([{"base": "BTC", "side": "short"}], "buy", exit_only=True)
    assert result == "closed", result
    assert fake.closed == ["BTC"]
    assert fake.opened == []


def test_delta_exit_only_flat_never_opens():
    # congested cross, client FLAT → must NOT open a fresh whipsaw position.
    result, fake, order = _run_delta([], "sell", exit_only=True)
    assert result == "skip(exit_only_flat)", result
    assert fake.opened == [], fake.opened
    assert fake.closed == []
    assert order.status == "skipped_exit_only", order.status


def test_delta_exit_only_aligned_left_alone():
    # exit_only sell while already SHORT (aligned with the bearish cross) → no-op.
    result, fake, order = _run_delta([{"base": "BTC", "side": "short"}], "sell", exit_only=True)
    assert result == "skip(already_short)", result
    assert fake.closed == [] and fake.opened == []


def test_delta_normal_signal_still_reaches_open(monkeypatch=None):
    # Control: a NON-exit_only flat signal must fall through to the open path.
    # Deny at entitlement so we don't need to fake equity/orders — proves the
    # exit_only guard did NOT short-circuit it.
    import app.entitlement as entitlement
    orig_allows = entitlement.allows
    executor.entitlement.allows = lambda *a, **k: (False, "test-block")
    try:
        result, fake, order = _run_delta([], "sell", exit_only=False)
    finally:
        executor.entitlement.allows = orig_allows
    assert result == "skip(entitlement:test-block)", result
    assert order.status == "skipped_entitlement", order.status


# ── Tradejini ───────────────────────────────────────────────────
def _run_tj(positions, side, exit_only):
    db = _session()
    user = _user_with_tradejini(db)
    FakeTJ._next_positions = positions
    orig_client = executor.tradejini.TradejiniClient
    orig_decrypt = executor.decrypt_secret
    executor.tradejini.TradejiniClient = FakeTJ
    executor.decrypt_secret = lambda x: x
    try:
        sig = Signal(symbol="NIFTY26JUNFUT", side=side, ref_price=23700.0,
                     sl_price=23600.0, venue="tradejini", exit_only=exit_only)
        db.add(sig); db.commit()
        sig = db.get(Signal, sig.id)
        result = executor.execute_tradejini_signal_for_user(db, user, sig)
        db.commit()
    finally:
        executor.tradejini.TradejiniClient = orig_client
        executor.decrypt_secret = orig_decrypt
    return result, FakeTJ.last, _last_order(db)


def test_tradejini_exit_only_closes_opposite_long():
    # bearish cross (sell) while client holds a LONG future → take profit.
    result, fake, order = _run_tj([{"sym_id": "SYM1", "side": "buy"}], "sell", exit_only=True)
    assert result == "closed", result
    assert fake.closed == ["SYM1"], fake.closed
    assert fake.placed == [], fake.placed
    assert order.status == "closed"


def test_tradejini_exit_only_flat_never_opens():
    # congested cross, client FLAT → never enter a fresh whole-lot F&O position.
    result, fake, order = _run_tj([], "sell", exit_only=True)
    assert result == "skip(exit_only_flat)", result
    assert fake.placed == [], fake.placed
    assert fake.closed == []
    assert order.status == "skipped_exit_only", order.status


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
