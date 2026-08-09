"""Zerodha Kite market-data source for the NIFTY brain.

Data and execution are decoupled: Kite supplies NIFTY candles (it has the most
reliable Indian data API and — crucially — NO IP whitelist, so the daily-token
auto-refresh runs unattended from our server). Signals still execute on each
client's Tradejini account; only the brain's candle source changes.

Unattended login (the standard algo approach — Kite's official flow needs a
daily browser login, so we drive the web login endpoints with TOTP):
  1. POST /api/login        {user_id, password}            -> request_id
  2. POST /api/twofa        {user_id, request_id, TOTP}    -> session cookies
  3. GET  /connect/login?api_key&v=3  (cookie)             -> 302 ...?request_token=
  4. POST api.kite.trade/session/token {api_key, request_token, checksum}
       checksum = sha256(api_key + request_token + api_secret)            -> access_token
Then GET api.kite.trade/instruments/historical/<token>/<interval> for candles.

Kite access tokens expire ~06:00 IST daily; we cache and re-login on demand.
Requires a Kite Connect app (api_key/secret) + the account's user_id/password
and an external-TOTP seed (Zerodha → external 2FA).
"""
from __future__ import annotations

import hashlib
import http.cookiejar
import json
import logging
import re
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .config import settings
from .tradejini import _totp_now  # reuse the RFC6238 TOTP generator

log = logging.getLogger("kite-data")

KITE_WEB = "https://kite.zerodha.com"
KITE_API = "https://api.kite.trade"
_IST = timezone(timedelta(hours=5, minutes=30))


def _ctx() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        try:
            return ssl.create_default_context()
        except Exception:
            return ssl._create_unverified_context()


_CTX = _ctx()
# Zerodha's login WAF 403s non-browser user-agents — present as a browser.
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/120.0.0.0 Safari/537.36")
# native Kite intervals (minutes -> Kite interval string)
_KITE_INTERVAL = {1: "minute", 3: "3minute", 5: "5minute", 10: "10minute",
                  15: "15minute", 30: "30minute", 60: "60minute"}
_KITE_DAILY = "day"  # Kite's daily interval (one candle per trading session)
_cache: dict = {"token": None, "exp": 0.0, "fail_until": 0.0}
_LOGIN_FAIL_COOLDOWN = 1800  # 30 min — don't hammer Kite login on repeated failure


class KiteError(Exception):
    pass


def configured() -> bool:
    s = settings
    return bool(s.kite_api_key and s.kite_api_secret and s.kite_user_id
               and s.kite_password and s.kite_totp_secret)


# ── unattended login ───────────────────────────────────────────
def _auto_login() -> str:
    """Return a valid Kite access_token, logging in via TOTP if needed."""
    now = time.time()
    if _cache["token"] and now < _cache["exp"]:
        return _cache["token"]
    if now < _cache.get("fail_until", 0):
        raise KiteError("in login cooldown after recent failure")

    s = settings
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar), urllib.request.HTTPSHandler(context=_CTX))
    opener.addheaders = [("User-Agent", _UA), ("X-Kite-Version", "3"),
                         ("Referer", f"{KITE_WEB}/")]

    def post(url, data):
        body = urllib.parse.urlencode(data).encode()
        return opener.open(urllib.request.Request(url, data=body, method="POST"), timeout=20)

    try:
        # 1. user_id + password -> request_id
        r1 = json.loads(post(f"{KITE_WEB}/api/login",
                             {"user_id": s.kite_user_id, "password": s.kite_password}).read())
        if r1.get("status") != "success":
            raise KiteError(f"login step1: {r1}")
        request_id = (r1.get("data") or {}).get("request_id")
        if not request_id:
            raise KiteError(f"login step1 no request_id: {r1}")
        # 2. TOTP 2FA
        r2 = json.loads(post(f"{KITE_WEB}/api/twofa", {
            "user_id": s.kite_user_id, "request_id": request_id,
            "twofa_value": _totp_now(s.kite_totp_secret), "twofa_type": "totp"}).read())
        if r2.get("status") != "success":
            raise KiteError(f"twofa step2: {r2}")
        # 3. connect/login -> capture request_token from the redirect
        request_token = _capture_request_token(jar, s.kite_api_key)
        # 4. exchange for access_token
        checksum = hashlib.sha256(
            (s.kite_api_key + request_token + s.kite_api_secret).encode()).hexdigest()
        body = urllib.parse.urlencode({
            "api_key": s.kite_api_key, "request_token": request_token, "checksum": checksum}).encode()
        req = urllib.request.Request(f"{KITE_API}/session/token", data=body, method="POST",
                                     headers={"X-Kite-Version": "3", "User-Agent": _UA})
        sess = json.loads(urllib.request.urlopen(req, timeout=20, context=_CTX).read())
        tok = (sess.get("data") or {}).get("access_token")
        if not tok:
            raise KiteError(f"session/token no access_token: {sess}")
    except KiteError:
        _cache["fail_until"] = now + _LOGIN_FAIL_COOLDOWN
        raise
    except urllib.error.HTTPError as e:
        _cache["fail_until"] = now + _LOGIN_FAIL_COOLDOWN
        raise KiteError(f"auto-login HTTP {e.code} at {e.url}: {e.read().decode()[:160]}")
    except Exception as e:
        _cache["fail_until"] = now + _LOGIN_FAIL_COOLDOWN
        raise KiteError(f"auto-login error: {e}")

    # Kite tokens die ~06:00 IST; expire at the next 06:00 IST (minus a margin).
    nxt = datetime.now(_IST).replace(hour=6, minute=0, second=0, microsecond=0)
    if datetime.now(_IST) >= nxt:
        nxt += timedelta(days=1)
    _cache.update(token=tok, exp=nxt.timestamp() - 300, fail_until=0.0)
    log.info("kite auto-login ok (valid until ~%s IST)", nxt.strftime("%Y-%m-%d 06:00"))
    return tok


def _capture_request_token(jar, api_key: str) -> str:
    """GET /connect/login with the session cookie; pull request_token from the
    redirect (Location header) or final URL. Follows redirects but scans every
    hop for request_token, since it appears on the redirect_uri hop."""
    captured = {}

    class _Scan(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            rt = urllib.parse.parse_qs(urllib.parse.urlparse(newurl).query).get("request_token")
            if rt:
                captured["rt"] = rt[0]
                return None  # got it — stop following
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar), _Scan(),
        urllib.request.HTTPSHandler(context=_CTX))
    opener.addheaders = [("User-Agent", _UA), ("X-Kite-Version", "3")]
    url = f"{KITE_WEB}/connect/login?api_key={urllib.parse.quote(api_key)}&v=3"
    try:
        resp = opener.open(url, timeout=20)
        loc = resp.headers.get("Location", "") or resp.geturl()
        rt = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query).get("request_token")
        if rt:
            captured["rt"] = rt[0]
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "") or getattr(e, "url", "") or ""
        rt = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query).get("request_token")
        if rt:
            captured["rt"] = rt[0]
    except Exception:
        pass  # _Scan may abort the chain after capturing; that's fine
    if "rt" not in captured:
        raise KiteError("could not capture request_token — the app likely needs a "
                        "one-time manual authorization (log in once at the Kite connect URL)")
    return captured["rt"]


# ── instruments dump (for near-month future resolution) ─────────
# Kite publishes the full tradable-instrument list as CSV per exchange. We need
# it to map each underlying (NIFTY, BANKNIFTY, …) to its near-month FUTURE
# instrument_token — futures roll monthly, so the token changes every expiry and
# must be resolved dynamically, never hardcoded.
_instr_cache: dict[str, dict] = {}  # exchange -> {"rows": [...], "exp": ts}


def instruments(exchange: str) -> list[dict]:
    """Return Kite's instrument list for an exchange (e.g. 'NFO', 'BFO') as a list
    of dicts. Cached until the next ~06:00 IST (instruments refresh daily)."""
    exchange = exchange.upper()
    now = time.time()
    cached = _instr_cache.get(exchange)
    if cached and now < cached["exp"]:
        return cached["rows"]
    tok = _auto_login()
    url = f"{KITE_API}/instruments/{urllib.parse.quote(exchange)}"
    req = urllib.request.Request(url, headers={
        "X-Kite-Version": "3", "User-Agent": _UA,
        "Authorization": f"token {settings.kite_api_key}:{tok}"})
    try:
        raw = urllib.request.urlopen(req, timeout=30, context=_CTX).read()
    except urllib.error.HTTPError as e:
        raise KiteError(f"instruments/{exchange} HTTP {e.code}: {e.read().decode()[:160]}")
    except Exception as e:
        raise KiteError(f"instruments/{exchange} error: {e}")
    if raw[:2] == b"\x1f\x8b":  # gzip
        import gzip
        raw = gzip.decompress(raw)
    import csv
    import io
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))))
    nxt = datetime.now(_IST).replace(hour=6, minute=0, second=0, microsecond=0)
    if datetime.now(_IST) >= nxt:
        nxt += timedelta(days=1)
    _instr_cache[exchange] = {"rows": rows, "exp": nxt.timestamp()}
    log.info("kite instruments/%s loaded: %d rows", exchange, len(rows))
    return rows


# ── candles ────────────────────────────────────────────────────
def _hist_request(token_id: str, interval: str, from_d: datetime, to_d: datetime,
                  timeout: float = 25.0) -> list[list]:
    """Low-level Kite historical fetch → [[ts_ms, o, h, l, c, v], …] (volume
    included). Shared by the intraday and daily wrappers. Raises KiteError on any
    transport/parse failure or an empty result so callers can fail-soft."""
    tok = _auto_login()
    qs = urllib.parse.urlencode({
        "from": from_d.strftime("%Y-%m-%d %H:%M:%S"),
        "to": to_d.strftime("%Y-%m-%d %H:%M:%S")})
    url = f"{KITE_API}/instruments/historical/{token_id}/{interval}?{qs}"
    req = urllib.request.Request(url, headers={
        "X-Kite-Version": "3", "User-Agent": _UA,
        "Authorization": f"token {settings.kite_api_key}:{tok}"})
    try:
        j = json.loads(urllib.request.urlopen(req, timeout=timeout, context=_CTX).read())
    except urllib.error.HTTPError as e:
        raise KiteError(f"historical HTTP {e.code}: {e.read().decode()[:160]}")
    except Exception as e:
        raise KiteError(f"historical error: {e}")
    candles = (j.get("data") or {}).get("candles", [])
    out = []
    for c in candles:  # [ts_iso, o, h, l, c, v]
        try:
            ts = int(datetime.fromisoformat(c[0]).timestamp() * 1000)
            v = float(c[5]) if len(c) > 5 and c[5] is not None else 0.0
            out.append([ts, float(c[1]), float(c[2]), float(c[3]), float(c[4]), v])
        except Exception:
            continue
    if not out:
        raise KiteError(f"no candles returned for instrument {token_id} ({interval})")
    return out


def bars_for_token(instrument_token: str | int, timeframe_min: int, count: int) -> list[list]:
    """Return up to ~count candles [[ts_ms, o, h, l, c], …] for any Kite
    instrument_token (index OR future). The brain drives off the near-month
    FUTURE token so the signal/SL price domain matches what clients actually
    trade — see fno_instruments.resolve_near_month()."""
    interval = _KITE_INTERVAL.get(timeframe_min, "5minute")
    per_day = max(1, 375 // max(1, timeframe_min))
    days = max(7, int((count / per_day) * 1.8) + 5)
    to_d = datetime.now(_IST)
    from_d = to_d - timedelta(days=days)
    rows = _hist_request(str(instrument_token), interval, from_d, to_d)
    # Backward-compatible: callers of bars_for_token expect 5-tuples (no volume).
    return [r[:5] for r in rows][-int(count):]


def daily_bars_for_token(instrument_token: str | int, count: int) -> list[list]:
    """Return up to ~count DAILY candles [[ts_ms, o, h, l, c, v], …] for any Kite
    instrument_token. Used by the AAA candlestick-pattern scanner — daily candles
    carry volume so pattern reliability can be volume-weighted. Reaches back enough
    calendar days to clear weekends/holidays for `count` trading sessions."""
    days = int(count * 1.6) + 12  # ~5 trading days / 7 calendar + holiday slack
    to_d = datetime.now(_IST)
    from_d = to_d - timedelta(days=days)
    rows = _hist_request(str(instrument_token), _KITE_DAILY, from_d, to_d)
    return rows[-int(count):]


# Kite lumps everything cash-settled on NSE into segment "NSE", instrument_type
# "EQ" — including ~6000 DEBT instruments (State Development Loans `-SG`, G-Secs
# `-GS`, sovereign gold bonds `-GB`, T-bills `-TB`, and NCD series `-N<x>`/`-Y<x>`).
# Those have no daily candle history (every fetch errors) and would balloon the
# sweep from ~3700 real stocks to ~9700, pushing it past the pre-open window. We
# deny the debt series by trading-symbol suffix; genuine equities — including
# hyphenated names (NAM-INDIA), Trade-to-Trade (`-BE`/`-BZ`) and SME (`-SM`/`-ST`)
# — are kept. A stray debt symbol that slips through just fails-soft in the scan.
_NSE_DEBT_SERIES = re.compile(r"-(SG|GS|GB|GC|GP|TB|SF|N[0-9A-Z]|Y[A-Z]|Z[0-9])$", re.I)


def nse_equities() -> list[dict]:
    """Tradable NSE EQ-segment equities (debt/govt series excluded) from Kite's
    instrument dump: [{"tradingsymbol", "instrument_token", "name"}, …]. The AAA
    scanner's universe. Cached daily via instruments()."""
    out = []
    for r in instruments("NSE"):
        if (r.get("instrument_type") or "").upper() != "EQ":
            continue
        if (r.get("segment") or "").upper() != "NSE":
            continue
        ts = (r.get("tradingsymbol") or "").strip()
        tok = (r.get("instrument_token") or "").strip()
        if not ts or not tok:
            continue
        if _NSE_DEBT_SERIES.search(ts):  # drop SDLs / G-Secs / NCDs / gold bonds
            continue
        out.append({"tradingsymbol": ts, "instrument_token": tok, "name": (r.get("name") or "").strip()})
    return out


def nifty200_equities() -> list[dict]:
    """The NIFTY 200 constituents (top ~200 NSE names by market cap), resolved to
    Kite instrument tokens. Symbols come from nifty200.fetch_symbols() (live NSE CSV
    with a baked-in fallback); we match them against the live NSE EQ dump so only
    genuinely tradable tokens are returned. Logs how many symbols resolved so a
    rename/delisting shows up as reduced coverage rather than a silent gap."""
    from .nifty200 import fetch_symbols
    want = {s.upper() for s in fetch_symbols()}
    out: list[dict] = []
    seen: set[str] = set()
    for r in instruments("NSE"):
        if (r.get("instrument_type") or "").upper() != "EQ":
            continue
        if (r.get("segment") or "").upper() != "NSE":
            continue
        ts = (r.get("tradingsymbol") or "").strip()
        tok = (r.get("instrument_token") or "").strip()
        u = ts.upper()
        if not ts or not tok or u not in want or u in seen:
            continue
        seen.add(u)
        out.append({"tradingsymbol": ts, "instrument_token": tok, "name": (r.get("name") or "").strip()})
    missing = sorted(want - seen)
    if missing:
        log.warning("nifty200: %d/%d symbols unresolved in Kite NSE dump (e.g. %s)",
                    len(missing), len(want), ", ".join(missing[:8]))
    log.info("nifty200 universe resolved: %d of %d symbols", len(out), len(want))
    return out


def nifty_bars(timeframe_min: int, count: int) -> list[list]:
    """Backward-compatible wrapper: candles for the configured Kite instrument
    (default NIFTY 50 index). New code should use bars_for_token()."""
    return bars_for_token(settings.kite_instrument_token, timeframe_min, count)
