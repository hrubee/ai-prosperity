"""Database models. Mirrors the data model in ../BUILD_PLAN.md."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(16), default="user")  # user | admin
    # Self-service signup profile + password (PBKDF2 string "pbkdf2$iters$salt$hash").
    # Nullable so pre-password (legacy OTP) rows load; new signups always set them.
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Payment approval status for offline/UPI flow
    payment_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | approved | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    subscription: Mapped["Subscription | None"] = relationship(back_populates="user", uselist=False)
    connection: Mapped["DeltaConnection | None"] = relationship(back_populates="user", uselist=False)
    payment_screenshot: Mapped["PaymentScreenshot | None"] = relationship(back_populates="user", uselist=False)


class OtpCode(Base):
    __tablename__ = "otp_codes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), index=True)
    code_hash: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PricingPlan(Base):
    __tablename__ = "pricing_plans"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    months: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    price_inr: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    dodo_subscription_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    package: Mapped[str] = mapped_column(String(16))  # starter | growth | pro
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending|active|on_hold|cancelled|failed|approved_waiting_connection
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    user: Mapped[User] = relationship(back_populates="subscription")

    @property
    def is_active(self) -> bool:
        if self.status != "active":
            return False
        if self.current_period_end and self.current_period_end < datetime.now(timezone.utc):
            return False
        return True


class DeltaConnection(Base):
    __tablename__ = "delta_connections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    api_key: Mapped[str] = mapped_column(String(128))
    # Fernet-encrypted secret. NEVER store/log plaintext.
    api_secret_encrypted: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="connected")  # connected|invalid|revoked|disconnected
    paused: Mapped[bool] = mapped_column(Boolean, default=False)  # user-initiated pause
    # Per-client exchange mode. False = live (real Delta mainnet, real money) —
    # the default for real client connections. True = testnet (a validation
    # canary). Real keys only work live; testnet keys only work in sandbox.
    sandbox: Mapped[bool] = mapped_column(Boolean, default=False)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="connection")


class TradejiniConnection(Base):
    """A client's Tradejini (Indian F&O) account — per-client "bring your own API
    key" model (Tradejini has NO multi-client OAuth/SSO; confirmed by their SDK).

    The client creates their own app in the TJ developer portal, whitelists our
    backend IP, and gives us their `api_key` (stored once). Each trading day they
    re-auth with password + TOTP; we mint that day's access token via
    individual-token-v2 and store it (encrypted) + `expires_at`. We NEVER store
    the password or TOTP seed. `status` flips to `expired` once the token lapses.
    """
    __tablename__ = "tradejini_connections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    client_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The client's own Tradejini API key (used as `Bearer <api_key>` for login and
    # as `<api_key>:<token>` for all calls).
    api_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # AUTO-LOGIN creds (Fernet-encrypted): the client supplies these ONCE; we
    # auto-mint a fresh daily token from api_key + password + TOTP(seed), exactly
    # like the operator's own data account (individual_data_token). NEVER log plaintext.
    password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_seed_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Fernet-encrypted current access token ("" until first mint). NEVER log plaintext.
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="connected")  # connected|expired|disconnected
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    user: Mapped[User] = relationship()


class DhanConnection(Base):
    """A client's Dhan account credentials.
    The client provides their Client ID and Access Token (JWT) from DhanHQ.
    Tokens are long-lived and we store them encrypted.
    """
    __tablename__ = "dhan_connections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    client_id: Mapped[str | None] = mapped_column(String(32), nullable=True) # Kept for backward compatibility if ever needed
    api_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    api_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_seed_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Fernet-encrypted long-lived DhanHQ access token OR daily minted token
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="connected")  # connected|invalid|disconnected
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class StraddlePosition(Base):
    """Durable per-leg record for the NIFTY options straddle (Phase 4 CANARY).

    One row per leg we OPEN on a trading day. Written as an ``intent`` row BEFORE
    the broker order so a runner crash can never orphan an untracked naked leg;
    then updated to ``open`` with the fill. The crash-safe square-off and the
    restart reconcile read THESE rows (durable, survive a process death) — not
    the engine's in-memory state — to know exactly which Tradejini ``sym_id``s
    are ours.

    NOT an authority on size: every SELL sizes from the broker's live ``netQty``
    (TradejiniClient.close_position), never from ``qty`` here. An oversell would
    flip a long into a naked short (unlimited risk); a missed close is only
    bounded P&L drag. This row is bookkeeping + attribution, nothing more.
    """
    __tablename__ = "straddle_positions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)   # YYYY-MM-DD (IST)
    leg: Mapped[str] = mapped_column(String(4))                       # CE | PE
    strike: Mapped[float] = mapped_column(Float)
    expiry: Mapped[str] = mapped_column(String(10))                   # weekly expiry YYYY-MM-DD
    sym_id: Mapped[str | None] = mapped_column(String(64), nullable=True)   # Tradejini symId — what square-off acts on
    symbol: Mapped[str | None] = mapped_column(String(48), nullable=True)  # tradSymbol (alerts/display)
    qty: Mapped[int] = mapped_column(Integer, default=0)              # lot_size * lots (reference only)
    entry_px: Mapped[float | None] = mapped_column(Float, nullable=True)   # engine ref premium at buy
    entry_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exit_px: Mapped[float | None] = mapped_column(Float, nullable=True)    # engine ref premium at sell
    exit_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # intent (about to order) | open (filled) | closed (flat) | error
    status: Mapped[str] = mapped_column(String(12), default="intent", index=True)
    reason: Mapped[str | None] = mapped_column(String(48), nullable=True)  # engine entry/exit reason
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    symbol: Mapped[str] = mapped_column(String(32), index=True)   # BTC/USDT (crypto) or NIFTY26JUNFUT (F&O)
    side: Mapped[str] = mapped_column(String(8))                  # buy | sell | close
    # Which client venue this signal targets: crypto → Delta, Indian F&O → Tradejini.
    venue: Mapped[str] = mapped_column(String(16), default="delta", index=True)  # delta | tradejini
    sl_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    ref_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # brain's reference entry
    # exit_only: a directional take-profit. Closes any client holding the OPPOSITE
    # position (the EMA opposite cross = take profit) but never opens a new one.
    # The FNO brain publishes these on a vision-CONGESTED cross: the whipsaw risk
    # makes a fresh entry unwise, yet an existing winner must still be cashed out.
    exit_only: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(32), default="brain")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    fanned_out: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class ClientOrder(Base):
    """One row per (signal, client) attempt — also the idempotency record."""
    __tablename__ = "client_orders"
    __table_args__ = (UniqueConstraint("signal_id", "user_id", name="uq_signal_user"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    signal_id: Mapped[str] = mapped_column(ForeignKey("signals.id"), index=True)
    client_order_id: Mapped[str] = mapped_column(String(32))  # Delta idempotency key (<=32 chars)
    delta_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    # pending|filled|skipped_entitlement|skipped_margin|error|closed
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    size: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_px: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EntitlementCounter(Base):
    """Per-user weekly counters that the package gate enforces."""
    __tablename__ = "entitlement_counters"
    __table_args__ = (UniqueConstraint("user_id", "week_start", name="uq_user_week"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    week_start: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD (week boundary)
    trades_count: Mapped[int] = mapped_column(Integer, default=0)
    trading_days: Mapped[str] = mapped_column(Text, default="[]")    # JSON list of YYYY-MM-DD


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(128), nullable=True)
    meta: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentScreenshot(Base):
    """User-uploaded payment proof screenshot for offline/UPI payments.
    One per user (latest screenshot). Persists forever after first upload.
    Admin reviews and approves/rejects via /admin/approvals."""
    __tablename__ = "payment_screenshots"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    # Base64-encoded image data (PNG/JPEG). Stored inline since screenshots are small (~100-500KB).
    image_b64: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(String(32))  # image/png, image/jpeg
    # Admin review status
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | approved | rejected
    reviewed_by: Mapped[str | None] = mapped_column(String(32), nullable=True)  # admin user_id
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    user: Mapped[User] = relationship()


class BrainEvent(Base):
    """One row per FRESH EMA cross the F&O brain evaluated — the AI Vision log
    surfaced on the admin panel. Captures the verdict (congestion veto vs.
    approved), the model's reasoning, the resulting action, and the exact chart
    the model saw (PNG as base64; Postgres TOASTs it out-of-line). Written in its
    own transaction by brain_log.record() — secondary to the trade, so a logging
    failure never affects signal publication."""
    __tablename__ = "brain_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # nifty-brain (F&O) | crypto-brain (bridged from the go-trader vision log)
    source: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    instrument: Mapped[str] = mapped_column(String(16), index=True)      # NIFTY, BANKNIFTY, BTC, …
    tj_symbol: Mapped[str] = mapped_column(String(32))                   # NIFTY26JUNFUT | BTC/USDT
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)  # buy | sell (null = crypto veto, no side)
    ref_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    sl_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    atr: Mapped[float | None] = mapped_column(Float, nullable=True)
    red_dots: Mapped[int | None] = mapped_column(Integer, nullable=True)  # crossovers drawn on the chart
    vision_evaluated: Mapped[bool] = mapped_column(Boolean, default=False)
    congested: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # vision verdict
    vision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    visual_sl: Mapped[float | None] = mapped_column(Float, nullable=True)   # model's SL (logged, not placed)
    # published | vetoed | fallback (vision failed → published w/ ATR) | hold
    action: Mapped[str] = mapped_column(String(16), index=True)
    signal_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # the Signal it published, if any
    chart_b64: Mapped[str | None] = mapped_column(Text, nullable=True)        # PNG bytes, base64
