"""Central configuration, loaded from environment.

In production the secrets here live in the VPS .env (or a secret manager), never
in git. See .env.example for the full list.
"""
import os
from dataclasses import dataclass
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(_env_path):
        load_dotenv(dotenv_path=_env_path)
    else:
        load_dotenv()
except ImportError:
    pass


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    # Database
    database_url: str = _get(
        "DATABASE_URL", "postgresql+psycopg2://aiprosperity:aiprosperity@localhost:5432/aiprosperity"
    )

    # Auth
    jwt_secret: str = _get("JWT_SECRET", "dev-insecure-change-me")
    jwt_ttl_hours: int = int(_get("JWT_TTL_HOURS", "168"))  # 7 days
    otp_ttl_minutes: int = int(_get("OTP_TTL_MINUTES", "10"))
    # When no SMTP is configured, echo the OTP in the API response so the flow is
    # testable. MUST be 0 in production once email is wired (security footgun).
    auth_dev_echo_otp: bool = _get("AUTH_DEV_ECHO_OTP", "0") == "1"

    # Secret encryption (urlsafe base64 32-byte Fernet key). In prod, source from KMS/Vault.
    secret_encryption_key: str = _get("SECRET_ENCRYPTION_KEY", "")

    # Delta Exchange (India)
    delta_base_url: str = _get("DELTA_BASE_URL", "https://api.india.delta.exchange")
    delta_sandbox: bool = _get("DELTA_SANDBOX", "0") == "1"

    # Tradejini (Indian F&O) — OAuth app for multi-tenant client onboarding.
    # Clients log in on Tradejini (cubeplus) and we receive a per-client token;
    # we never hold their password. App key/secret + redirect are from the
    # Tradejini developer-portal app registration.
    tradejini_base_url: str = _get("TRADEJINI_BASE_URL", "https://api.tradejini.com/v2")
    tradejini_app_key: str = _get("TRADEJINI_APP_KEY", "")
    tradejini_app_secret: str = _get("TRADEJINI_APP_SECRET", "")
    tradejini_redirect_uri: str = _get("TRADEJINI_REDIRECT_URI", "https://app.diffraction.in/api/tradejini/callback")
    # Dedicated DATA account for the NIFTY brain — auto-logs in (api_key +
    # password + permanent TOTP seed) so the data feed never goes dark when the
    # daily token expires. If password/seed are blank the brain falls back to a
    # connected client token.
    # Market-order protection % — Tradejini MANDATES this on every API market
    # order ("Market protection mandatory for all market order. for API users").
    # It caps how far from LTP a market order may fill (a slippage guard). Sent on
    # every market/stopmarket order. 5% reliably fills liquid NIFTY options while
    # still bounding slippage; tune via env if entries get rejected in fast moves.
    tradejini_mkt_prot_pct: float = float(_get("TRADEJINI_MKT_PROT_PCT", "5"))
    tradejini_data_api_key: str = _get("TRADEJINI_DATA_API_KEY", "")
    tradejini_data_password: str = _get("TRADEJINI_DATA_PASSWORD", "")
    tradejini_data_totp_secret: str = _get("TRADEJINI_DATA_TOTP_SECRET", "")

    # Zerodha Kite — DATA source for the NIFTY brain (no IP whitelist, so the
    # daily-token auto-refresh runs unattended). Execution stays on Tradejini.
    # Needs a Kite Connect app (key/secret) + the account's login + external
    # TOTP seed. Default instrument = NIFTY 50 index (256265).
    kite_api_key: str = _get("KITE_API_KEY", "")
    kite_api_secret: str = _get("KITE_API_SECRET", "")
    kite_user_id: str = _get("KITE_USER_ID", "")
    kite_password: str = _get("KITE_PASSWORD", "")
    kite_totp_secret: str = _get("KITE_TOTP_SECRET", "")
    kite_instrument_token: str = _get("KITE_INSTRUMENT_TOKEN", "256265")  # NIFTY 50 index

    # AAA candlestick-pattern scanner (daily, pre-open). Scans the NSE equity
    # universe on the 1D timeframe for the patterns in candle pattern.md and writes
    # a sidecar JSON the public /aaa page reads. The scan runs once/day via a
    # systemd timer; the API only READS this file (never blocks on Kite).
    aaa_setups_path: str = _get("AAA_SETUPS_PATH", "/root/aiprosperity/backend/aaa_setups.json")
    # Universe: "nifty200" (top ~200 by market cap — official NSE index, the default)
    # or "nse_all" (every NSE EQ stock, debt series filtered out).
    aaa_universe: str = _get("AAA_UNIVERSE", "nifty200")
    aaa_history_days: int = int(_get("AAA_HISTORY_DAYS", "60"))  # daily candles fetched per stock
    # Kite historical API is rate-limited (~3 req/s); throttle between stocks so a
    # ~2000-symbol sweep stays under the cap and never gets the data account banned.
    aaa_throttle_sec: float = float(_get("AAA_THROTTLE_SEC", "0.34"))
    aaa_max_symbols: int = int(_get("AAA_MAX_SYMBOLS", "0"))  # 0 = scan all NSE equities

    # Options straddle (NIFTY) — live multi-client execution.
    # MASTER SWITCH: places REAL Tradejini orders on connected client accounts only
    # when this is 1. Default 0 = SHADOW (the runner computes/logs actions but
    # places nothing) — so deploying the code is always safe until consciously
    # turned on. There is no other way to go live.
    straddle_live: bool = _get("STRADDLE_LIVE", "0") == "1"
    # DRY-RUN verification: builds the live client list and exercises the FULL
    # order path (Tradejini strike/expiry resolution + per-client margin sizing +
    # the durable-row lifecycle) but places ZERO real orders. Run this once in the
    # real environment and eyeball the logged resolved symbols + lot sizes BEFORE
    # flipping straddle_live on — it is the pre-live check that the right contract
    # resolves on every account. Takes precedence over straddle_live (both set ⇒
    # still dry), so it can never accidentally place an order.
    straddle_dry_run: bool = _get("STRADDLE_DRY_RUN", "0") == "1"
    # Optional single-account RESTRICT (test mode): when set AND straddle_live=1,
    # fan-out is limited to the ONE account whose user email equals this — run the
    # strategy on your own account first without touching clients. BLANK = fan out
    # to every connected client (the full multi-client mode).
    straddle_canary_email: str = _get("STRADDLE_CANARY_EMAIL", "")
    # Per-client sizing: lots = floor(available_margin / this). ₹1L ⇒ 4 lots at
    # ₹25k. Buffer is intentional — momentum re-entries can buy above the ₹50 ref.
    straddle_capital_per_lot_inr: float = float(_get("STRADDLE_CAPITAL_PER_LOT_INR", "25000"))
    # Per-client RUNAWAY guard on lots. Default 500 (≈ ₹1.25cr margin at ₹25k/lot)
    # — high enough never to clip a realistic client, low enough to stop an
    # outsized position if equity_inr() ever returns a garbage-large value on an
    # unattended fan-out. Raise it for a genuine HNI account; 0 = uncapped.
    straddle_max_lots: int = int(_get("STRADDLE_MAX_LOTS", "500"))
    # Per-client intraday realized-loss circuit breaker in INR (0 = off). Once a
    # client's booked loss for the day exceeds this, no new entries fire for them.
    straddle_daily_loss_cap_inr: float = float(_get("STRADDLE_DAILY_LOSS_CAP_INR", "0"))
    # Independent intraday GUARDIAN (watchdog teeth). When on, the watchdog
    # force-closes a NIFTY position WE opened (a row exists today) that NO live
    # runner is managing — the safety net for a dead/restarted runner. It is gated
    # by the runner HEARTBEAT + managed-set so it can never race the runner (see
    # straddle_watchdog). Deliberately NOT gated on straddle_live: an orphan must
    # still be flattened after the master switch is turned off with a position
    # open (exactly the 2026-06-03 incident). Default ON.
    straddle_guardian_enabled: bool = _get("STRADDLE_GUARDIAN_ENABLED", "1") == "1"
    # Heartbeat staleness threshold (seconds). The runner rewrites its heartbeat at
    # the top of every ~10s loop iteration; older than this ⇒ runner is presumed
    # dead and the guardian flattens our orphans immediately. Must exceed the
    # worst-case tick wall-time (grows with client fan-out) — revisit before
    # scaling past the canary.
    straddle_heartbeat_stale_secs: int = int(_get("STRADDLE_HEARTBEAT_STALE_SECS", "180"))
    # Tradejini product for the option legs. "normal" (NRML) carries overnight if
    # every closer fails; "intraday" (MIS) adds a broker auto-square-off ~15:20 as
    # a last-resort net. Open AND close use this same value (they must match to
    # net the position). Default "normal" preserves current behavior; switch to
    # "intraday" only after confirming Tradejini's MIS product code for options.
    straddle_product: str = _get("STRADDLE_PRODUCT", "normal")
    # IOC limit buffer % for BUY entries: engine trigger*(1+buffer) rounded UP to
    # tick (0.05). Tradejini IOC LIMIT caps slippage — fills at/below limit if
    # liquidity exists, otherwise no-fill (clean skip, engine reverts). 1% default
    # handles NIFTY option spreads (~1–2 pts on ₹50 leg = 2–4%) with room.
    straddle_ioc_buffer_pct: float = float(_get("STRADDLE_IOC_BUFFER_PCT", "0.01"))

    # F&O brain — instrument universe (comma-separated keys from
    # fno_instruments.UNIVERSE; blank = all). Each is driven off its near-month
    # FUTURE's Kite data so the signal/SL domain matches what clients trade.
    fno_instruments: str = _get("FNO_INSTRUMENTS", "NIFTY,BANKNIFTY,FINNIFTY,MIDCPNIFTY,SENSEX")
    # AI Vision congestion veto (NVIDIA NIM Llama-3.2-vision). Veto-only: it can
    # block a choppy cross but never sets the stop price. Needs NVIDIA_API_KEY +
    # matplotlib; auto-disabled if either is missing.
    fno_vision_enabled: bool = _get("FNO_VISION_ENABLED", "1") == "1"
    fno_chart_bars: int = int(_get("FNO_CHART_BARS", "240"))  # candles shown to the model
    nvidia_api_key: str = _get("NVIDIA_API_KEY", "")

    # Risk / sizing
    risk_per_trade: float = float(_get("RISK_PER_TRADE", "0.02"))  # 2% global

    # Dodo Payments
    dodo_api_key: str = _get("DODO_API_KEY", "")
    dodo_webhook_secret: str = _get("DODO_WEBHOOK_SECRET", "")
    dodo_base_url: str = _get("DODO_BASE_URL", "https://api.dodopayments.com")
    # Map our package ids -> Dodo product ids (created in the Dodo dashboard).
    dodo_product_starter: str = _get("DODO_PRODUCT_STARTER", "")
    dodo_product_growth: str = _get("DODO_PRODUCT_GROWTH", "")
    dodo_product_pro: str = _get("DODO_PRODUCT_PRO", "")

    # Offline/UPI Payment (QR code + screenshot verification)
    upi_qr_base64: str = _get("UPI_QR_BASE64", "")  # Base64-encoded PNG of UPI QR code
    upi_qr_amount_inr: int = int(_get("UPI_QR_AMOUNT_INR", "5000"))  # Amount shown in QR (default Starter)
    upi_qr_name: str = _get("UPI_QR_NAME", "AI Prosperity")  # Payee name in QR
    upi_qr_upi_id: str = _get("UPI_QR_UPI_ID", "")  # UPI ID for the QR

    # Frontend origin (CORS + checkout redirect)
    app_origin: str = _get("APP_ORIGIN", "https://app.diffraction.in")

    # Worker
    worker_poll_seconds: float = float(_get("WORKER_POLL_SECONDS", "2"))
    worker_max_concurrency: int = int(_get("WORKER_MAX_CONCURRENCY", "16"))
    week_start_tz_offset_minutes: int = int(_get("WEEK_TZ_OFFSET_MIN", "330"))  # IST = +5:30

    # Admin crypto screener — order-book depth + EMA-cross enrichment. The backend
    # only READS these files; the heavy CoinAPI streaming lives in the separate
    # coinapi-feed sidecar (which holds COINAPI_KEY). Touching the heartbeat tells
    # that sidecar the panel is being viewed so it only burns credits then.
    screener_feed_snapshot: str = _get("SCREENER_FEED_SNAPSHOT", "/root/coinapi-feed/screener_snapshot.json")
    screener_feed_heartbeat: str = _get("SCREENER_FEED_HEARTBEAT", "/root/coinapi-feed/panel_heartbeat")
    # EMA-cross counts come from the cross-alerter sidecar's SQLite DB (48h window,
    # the bot's congestion gate). Only the ~5 bot-tracked symbols populate.
    screener_cross_db: str = _get("SCREENER_CROSS_DB", "/root/cross-alerter/crosses.db")


settings = Settings()
