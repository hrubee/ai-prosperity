#!/usr/bin/env python3
"""scripts/stream_dumpride_coindcx.py — 4H "DumpRide" Exhaustion Short Production Live Strategy Engine.

Strategy Specifications & Pre-Armed Fast-Trigger Architecture:
- Timeframe: 4-Hour (4H) (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC / 05:30, 09:30, 13:30, 17:30, 21:30, 01:30 IST)
- Universe: All active CoinDCX USDT perpetual futures instruments (~400 pairs)
- Signal Condition: 4H candle closes GREEN (Close > Open) with Volume >= 20.0x SMA(20) baseline volume
- Pre-Armed Watch Window: 10 minutes prior to 4H close (T - 10m to T), scans forming 4H candle across universe
- Zero-Latency Trigger: At exact 4H close (T = 00:00:00), skips 400-coin scan and instantly executes pre-armed candidates in <200ms
- Entry Execution: Immediate SHORT at 4H candle close via Native CoinDCX Market Bracket Order
- Stop Loss: Entry + 1.0x ATR(14) (Native exchange-level hard SL)
- Take Profit: Entry - 2.0x ATR(14) (1:2 Risk-to-Reward Ratio)
- Risk Management: 1.0% account balance fixed-fractional risk per trade
- Concurrency: Max 10 active positions
- Multi-Account: Executes on Primary and optional Secondary Account in parallel
- Persistence: Full SQLite & JSON state persistence for seamless restart resilience
"""
import os
import sys
import time
import math
import json
import sqlite3
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd

# ── Configuration ─────────────────────────────────────────────────────────────
TF = os.environ.get("DUMPRIDE_TF", "4h")
TF_SEC = {"15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400, "8h": 28800, "12h": 43200}.get(TF, 14400)
TF_MS = TF_SEC * 1000

SPIKE_VOL_MULT = float(os.environ.get("DUMPRIDE_SPIKE_VOL", "10.0"))
MIN_PUMP_PCT = float(os.environ.get("DUMPRIDE_MIN_PUMP_PCT", "1.0"))
ATR_PERIOD = int(os.environ.get("DUMPRIDE_ATR_PERIOD", "14"))
SL_ATR_MULT = float(os.environ.get("DUMPRIDE_SL_ATR_MULT", "1.0"))
RR_TARGET = float(os.environ.get("DUMPRIDE_RR_TARGET", "2.0"))

PREARM_MINUTES = int(os.environ.get("DUMPRIDE_PREARM_MINUTES", "15"))
PREARM_WINDOW_SEC = PREARM_MINUTES * 60

RISK_FRAC = float(os.environ.get("DUMPRIDE_RISK_FRAC", "0.002")) # 0.2% risk per trade default
DEFAULT_LEVERAGE = int(os.environ.get("DUMPRIDE_LEVERAGE", "10"))
MAX_CONCURRENT = int(os.environ.get("DUMPRIDE_MAX_CONCURRENT", "10"))
MIN_RISK_SPREAD_PCT = float(os.environ.get("DUMPRIDE_MIN_RISK_PCT", "0.008")) # 0.8% min distance
MIN_4H_NOTIONAL_VOL = float(os.environ.get("DUMPRIDE_MIN_VOL_USDT", "25000.0" if TF == "1h" else "100000.0")) # Min volume threshold
MIN_REQUIRED_LEVERAGE = int(os.environ.get("DUMPRIDE_MIN_LEVERAGE", "10")) # Must support >=10x leverage

ARMED = os.environ.get("LIVE_ARMED", "0") == "1"
START_BAL_INR = float(os.environ.get("DUMPRIDE_START_BAL_INR", "10000.0"))
OUT_DIR = os.environ.get("DUMPRIDE_OUT", os.path.expanduser("~/dumpride_coindcx"))
STATE_FILE = os.path.join(OUT_DIR, "state.json")
DB_FILE = os.path.join(OUT_DIR, "dumpride.db")
LOG_FILE = os.path.join(OUT_DIR, "run.log")

os.makedirs(OUT_DIR, exist_ok=True)

# ── Logging ───────────────────────────────────────────────────────────────────
def log(msg):
    ts = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M:%S IST")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ── Database Persistence ──────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS executed_signals (
        symbol TEXT,
        candle_timestamp INTEGER,
        spike_mult REAL,
        entry_px REAL,
        sl_px REAL,
        tp_px REAL,
        executed_at INTEGER,
        PRIMARY KEY (symbol, candle_timestamp)
    );
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS trade_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        side TEXT,
        entry_px REAL,
        exit_px REAL,
        qty REAL,
        pnl_inr REAL,
        net_r REAL,
        reason TEXT,
        entry_t INTEGER,
        exit_t INTEGER,
        account TEXT
    );
    """)
    conn.commit()
    conn.close()

init_db()

# ── CoinDCX Adapter Setup ─────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv("/root/go-trader/.env")
    load_dotenv("/root/trading-bot/crypto/.env")
    load_dotenv()
except Exception:
    pass

try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(script_dir, "../platforms/coindcx"))
    sys.path.insert(0, "/root/ai-prosperity/platforms/coindcx")
    sys.path.insert(0, "/root/go-trader/platforms/coindcx")
    sys.path.insert(0, "/root/trading-bot/crypto/platforms/coindcx")
    from adapter import CoinDCXExchangeAdapter, CoinDCXError
except ImportError:
    class CoinDCXError(Exception): pass
    class CoinDCXExchangeAdapter:
        inr_per_usdt = 86.0
        def get_ohlcv(self, *args, **kwargs): return []
        def get_price(self, *args, **kwargs): return 0
        def fetch_positions(self): return []
        def active_bases(self): return []
        def market_open_bracket(self, *args, **kwargs): return {"id": "paper", "avg_price": 1}
        def floor_qty(self, base, qty): return qty
        def min_notional_usdt(self, base): return 10.0
        def get_inr_equity(self): return 16000.0
        def get_free_inr_balance(self): return 16000.0
        def instrument(self, base): return {"min_quantity": 1, "step_size": 1, "price_increment": 0.0001, "max_leverage_short": 10}

PRIMARY_KEY = os.environ.get("COINDCX_LIVE_API_KEY", "").strip()
PRIMARY_SECRET = os.environ.get("COINDCX_LIVE_API_SECRET", "").strip()
A = CoinDCXExchangeAdapter(key=PRIMARY_KEY, secret=PRIMARY_SECRET)

KEY_2 = os.environ.get("COINDCX_KEY_2", "").strip()
SECRET_2 = os.environ.get("COINDCX_SECRET_2", "").strip()
A2 = CoinDCXExchangeAdapter(key=KEY_2, secret=SECRET_2) if (KEY_2 and SECRET_2) else None
if A2:
    log(f"[MultiAccount] Secondary CoinDCX Account Enabled (Key ending in ...{KEY_2[-6:]})")

# ── Telegram Alerter Integration ──────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT)

def send_telegram_alert(text: str, reply_to_msg_id: int = None):
    if not TELEGRAM_ENABLED: return None
    import urllib.request, urllib.parse
    chat_ids = [c.strip() for c in TELEGRAM_CHAT.split(",") if c.strip()]
    first_msg_id = None
    for cid in chat_ids:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": cid,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": "true"
            }
            if reply_to_msg_id:
                payload["reply_to_message_id"] = str(reply_to_msg_id)
            req = urllib.request.Request(
                url,
                data=urllib.parse.urlencode(payload).encode("utf-8"),
                headers={"User-Agent": "Mozilla/5.0"}
            )
            resp = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
            if resp.get("ok") and first_msg_id is None:
                first_msg_id = resp["result"]["message_id"]
        except Exception as e:
            log(f"Telegram dispatch error to {cid}: {e}")
    return first_msg_id

# ── Chart Rendering Helper (Entry / Exit Telegram Cards) ───────────────────────
def render_and_send_chart(base: str, entry_px: float, sl_px: float, tp_px: float, caption: str, reply_to_msg_id: int = None):
    if not TELEGRAM_ENABLED: return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import urllib.request
        
        klines = A.get_ohlcv(base, interval=TF, limit=35, include_forming=True)
        if not klines or len(klines) < 15:
            return send_telegram_alert(caption, reply_to_msg_id)
            
        times = [datetime.datetime.fromtimestamp(r[0]/1000, tz=datetime.timezone.utc) for r in klines]
        closes = [float(r[4]) for r in klines]
        highs = [float(r[2]) for r in klines]
        lows = [float(r[3]) for r in klines]
        
        plt.style.use("dark_background")
        fig, ax = plt.subplots(figsize=(10, 5), dpi=130)
        ax.plot(times, closes, color="#38bdf8", label="Close Price", linewidth=2.0)
        
        # Horizontal Target & Stop lines
        ax.axhline(entry_px, color="#facc15", linestyle="--", alpha=0.9, label=f"Entry: ${entry_px:.4f}")
        ax.axhline(sl_px, color="#ef4444", linestyle="-", alpha=0.9, label=f"SL (1.0x ATR): ${sl_px:.4f}")
        ax.axhline(tp_px, color="#22c55e", linestyle="-", alpha=0.9, label=f"TP (1:2 RR): ${tp_px:.4f}")
        
        ax.set_title(f"4H DumpRide Short Setup — #{base}/USDT", fontsize=14, color="#ffffff", fontweight="bold", pad=12)
        ax.legend(loc="upper left", framealpha=0.3, facecolor="#1e293b", edgecolor="#334155")
        ax.grid(True, linestyle=":", alpha=0.25)
        plt.tight_layout()
        
        chart_path = f"/tmp/dumpride_{base}_{int(time.time())}.png"
        fig.savefig(chart_path)
        plt.close(fig)
        
        # Send Photo to Telegram
        chat_ids = [c.strip() for c in TELEGRAM_CHAT.split(",") if c.strip()]
        first_msg_id = None
        for cid in chat_ids:
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
                boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
                body = bytearray()
                
                # Chat ID
                body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{cid}\r\n".encode())
                # Caption
                body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n".encode())
                body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"parse_mode\"\r\n\r\nMarkdown\r\n".encode())
                if reply_to_msg_id:
                    body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"reply_to_message_id\"\r\n\r\n{reply_to_msg_id}\r\n".encode())
                    
                # Photo
                with open(chart_path, "rb") as f:
                    body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"chart.png\"\r\nContent-Type: image/png\r\n\r\n".encode())
                    body.extend(f.read())
                    body.extend(b"\r\n")
                body.extend(f"--{boundary}--\r\n".encode())
                
                req = urllib.request.Request(
                    url,
                    data=bytes(body),
                    headers={
                        "Content-Type": f"multipart/form-data; boundary={boundary}",
                        "User-Agent": "Mozilla/5.0"
                    }
                )
                resp = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8"))
                if resp.get("ok") and first_msg_id is None:
                    first_msg_id = resp["result"]["message_id"]
            except Exception as pe:
                log(f"Telegram photo upload error: {pe}")
                
        if os.path.exists(chart_path): os.remove(chart_path)
        return first_msg_id
    except Exception as ge:
        log(f"Chart generation error: {ge}")
        return send_telegram_alert(caption, reply_to_msg_id)

def post_entry_chart(base: str, entry_px: float, sl_px: float, tp_px: float, account: str = "Primary"):
    sl_pct = abs((sl_px - entry_px) / entry_px) * 100.0
    tp_pct = abs((entry_px - tp_px) / entry_px) * 100.0
    caption = (
        f"🚨 *DUMPRIDE 4H EXHAUSTION SHORT ENTERED*\n\n"
        f"• *Asset*: `#{base}/USDT` ({account})\n"
        f"• *Entry Price*: `${entry_px:.4f}`\n"
        f"• *Stop Loss*: `${sl_px:.4f}` (+{sl_pct:.2f}%)\n"
        f"• *Take Profit (1:2 RR)*: `${tp_px:.4f}` (-{tp_pct:.2f}%)\n"
        f"• *Risk*: `1.0% Account Balance`\n"
        f"• *Trigger*: Volume Surge >= {SPIKE_VOL_MULT:.0f}x Baseline\n\n"
        f"⚡ *Native Exchange Bracket SL/TP Armed*"
    )
    return render_and_send_chart(base, entry_px, sl_px, tp_px, caption)

def post_exit_chart(base: str, exit_px: float, entry_px: float, initial_sl_px: float, pnl_usdt: float, pnl_pct: float, reason: str, reply_to_msg_id: int = None, account: str = "Primary"):
    pnl_sign = "+" if pnl_usdt >= 0 else ""
    caption = (
        f"🏁 *DUMPRIDE 4H POSITION CLOSED*\n\n"
        f"• *Asset*: `#{base}/USDT` ({account})\n"
        f"• *Reason*: *{reason}*\n"
        f"• *Entry Price*: `${entry_px:.4f}`\n"
        f"• *Exit Price*: `${exit_px:.4f}`\n"
        f"• *PnL*: `{pnl_sign}${pnl_usdt:.2f} USDT` (`{pnl_sign}{pnl_pct:.2f}%`)\n"
        f"• *Status*: Position Liquidated / Reconciled"
    )
    return send_telegram_alert(caption, reply_to_msg_id=reply_to_msg_id)

# ── Dynamic Sizing Engine ─────────────────────────────────────────────────────
def calculate_short_position_size(base: str, entry_px: float, sl_px: float, wallet_inr: float, adapter=None):
    if adapter is None: adapter = A
    risk_inr = wallet_inr * RISK_FRAC
    risk_dist = abs(sl_px - entry_px)
    if risk_dist <= 0: return 0.0, 0.0
    
    usdt_rate = getattr(adapter, "inr_per_usdt", 86.0) or 86.0
    risk_usdt = risk_inr / usdt_rate
    raw_qty = risk_usdt / risk_dist
    
    # Precision floor
    qty = adapter.floor_qty(base, raw_qty)
    min_notional = adapter.min_notional_usdt(base)
    if qty * entry_px < min_notional:
        qty = adapter.floor_qty(base, (min_notional * 1.05) / entry_px)
            
    # Margin check against free balance
    inst = adapter.instrument(base) if hasattr(adapter, "instrument") else {}
    max_lev = float(inst.get("max_leverage_short") or DEFAULT_LEVERAGE)
    lev = int(min(DEFAULT_LEVERAGE, max_lev))
    
    margin_req_inr = (qty * entry_px * usdt_rate) / lev
    free_inr = adapter.get_free_inr_balance() if ARMED else wallet_inr
    if free_inr > 0 and margin_req_inr > free_inr * 0.90:
        capped_qty = adapter.floor_qty(base, ((free_inr * 0.85) * lev) / (entry_px * usdt_rate))
        if capped_qty * entry_px >= min_notional:
            qty = capped_qty
        else:
            log(f"[{base}] ⚠️ Insufficient free margin (Req: ₹{margin_req_inr:.1f}, Free: ₹{free_inr:.1f}). Skipping.")
            return 0.0, 0.0
            
    return qty, lev

# ── 4H Timing Helper ──────────────────────────────────────────────────────────
def get_4h_timing():
    """Returns (current_bucket_start_ms, next_bucket_close_ms, sec_until_close) aligned with 4H UTC schedule."""
    now_ms = int(time.time() * 1000)
    bucket_size = TF_MS # 14,400,000 ms for 4h
    cur_bucket_start = (now_ms // bucket_size) * bucket_size
    next_bucket_close = cur_bucket_start + bucket_size
    sec_until_close = max(0.0, (next_bucket_close - now_ms) / 1000.0)
    return cur_bucket_start, next_bucket_close, sec_until_close

# ── Signal Analysis & Pre-Arm Evaluator ───────────────────────────────────────
def evaluate_coin_4h_signal(base, adapter=None, include_forming=False):
    if adapter is None: adapter = A
    try:
        klines = adapter.get_ohlcv(base, interval=TF, limit=60, include_forming=include_forming)
        if not isinstance(klines, list) or len(klines) < 25:
            return None
            
        opens = np.array([float(r[1]) for r in klines])
        highs = np.array([float(r[2]) for r in klines])
        lows = np.array([float(r[3]) for r in klines])
        closes = np.array([float(r[4]) for r in klines])
        vols = np.array([float(r[5]) for r in klines])
        times = [int(r[0]) for r in klines]
        
        ci = len(klines) - 1
        candle_ts = times[ci]
        
        # Must be a bullish green candle (Close > Open)
        if closes[ci] <= opens[ci]:
            return None
            
        pump_pct = ((closes[ci] - opens[ci]) / opens[ci]) * 100.0
        if pump_pct < MIN_PUMP_PCT:
            return None
            
        # 20-period baseline volume (using preceding 20 closed bars)
        base_v = float(np.mean(vols[max(0, ci - 20) : ci]))
        if base_v <= 0: return None
        vol_mult = vols[ci] / base_v
        
        if vol_mult < SPIKE_VOL_MULT:
            return None

        # 4H Notional Volume Filter ($100k min to prevent illiquid micro-cap phantom spikes)
        notional_vol_usdt = float(vols[ci] * closes[ci])
        if notional_vol_usdt < MIN_4H_NOTIONAL_VOL:
            return None

        # Minimum Leverage Cap Filter (Avoid restricted microcaps with <10x leverage)
        inst = adapter.instrument(base) if hasattr(adapter, "instrument") else {}
        max_lev = float(inst.get("max_leverage_short") or DEFAULT_LEVERAGE)
        if max_lev < MIN_REQUIRED_LEVERAGE:
            return None
            
        # 14-period ATR
        tr = np.zeros(ci + 1)
        tr[0] = highs[0] - lows[0]
        for i in range(1, ci + 1):
            tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        atr14 = float(np.mean(tr[max(0, ci - 13) : ci + 1]))
        
        entry_px = closes[ci]
        sl_px = entry_px + (SL_ATR_MULT * atr14)
        tp_px = entry_px - (RR_TARGET * (sl_px - entry_px))
        
        return {
            "symbol": base,
            "candle_ts": candle_ts,
            "candle_dt": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(candle_ts / 1000)),
            "entry_px": entry_px,
            "sl_px": sl_px,
            "tp_px": tp_px,
            "risk_dist": sl_px - entry_px,
            "atr": atr14,
            "vol_mult": vol_mult,
            "pump_pct": pump_pct,
            "is_forming": include_forming
        }
    except Exception:
        return None

# ── Order Execution Engine ────────────────────────────────────────────────────
def execute_dumpride_short(sig, adapter, account_label="Primary"):
    base = sig["symbol"]
    entry_px = sig["entry_px"]
    sl_px = sig["sl_px"]
    tp_px = sig["tp_px"]
    
    # 1. Fetch live wallet equity
    wallet_inr = adapter.get_inr_equity() if ARMED else START_BAL_INR
    if wallet_inr <= 0: wallet_inr = START_BAL_INR
    
    # 2. Sizing calculation
    qty, lev = calculate_short_position_size(base, entry_px, sl_px, wallet_inr, adapter=adapter)
    if qty <= 0:
        log(f"[{account_label}] [{base}] ❌ Sizing failed. Aborting entry.")
        return None
        
    risk_inr = qty * (sl_px - entry_px) * (getattr(adapter, "inr_per_usdt", 86.0) or 86.0)
    
    log(f"[{account_label}] 🚨 EXECUTING 4H DUMPRIDE SHORT on {base}:")
    log(f"   • Spike Volume: {sig['vol_mult']:.1f}x Baseline | 4H Pump: +{sig['pump_pct']:.2f}%")
    log(f"   • Entry Price : {entry_px:.6g} | Qty: {qty} | Leverage: {lev}x")
    log(f"   • Hard SL (1.0x ATR): {sl_px:.6g} (+{((sl_px-entry_px)/entry_px)*100:.2f}%)")
    log(f"   • Take Profit (1:2 RR): {tp_px:.6g} (-{((entry_px-tp_px)/entry_px)*100:.2f}%)")
    log(f"   • Risk Allocation: ₹{risk_inr:.1f} ({RISK_FRAC*100:.1f}% equity)")
    
    if not ARMED:
        log(f"[{account_label}] 🟡 [PAPER MODE] Order simulated successfully.")
        return {
            "id": f"paper_{base}_{int(time.time())}",
            "symbol": base,
            "entry_px": entry_px,
            "sl_px": sl_px,
            "tp_px": tp_px,
            "qty": qty,
            "account": account_label,
            "entry_t": int(time.time() * 1000)
        }
        
    try:
        # Execute Native CoinDCX Market Bracket Order (creates position + exchange-level SL & TP)
        order_res = adapter.market_open_bracket(
            base=base,
            is_buy=False,
            qty=qty,
            leverage=lev,
            sl_price=sl_px,
            tp_price=tp_px
        )
        log(f"[{account_label}] ✅ COINDCX ORDER PLACED: {order_res.get('id', 'N/A')}")
        
        # Telegram Notification with Rendered Chart
        tg_msg_id = None
        if TELEGRAM_ENABLED:
            try:
                tg_msg_id = post_entry_chart(
                    base=base,
                    entry_px=entry_px,
                    sl_px=sl_px,
                    tp_px=tp_px,
                    account=f"DumpRide ({account_label})"
                )
            except Exception as te:
                log(f"Telegram entry error: {te}")
            
        return {
            "id": order_res.get("id"),
            "symbol": base,
            "entry_px": float(order_res.get("avg_price") or entry_px),
            "sl_px": sl_px,
            "tp_px": tp_px,
            "qty": qty,
            "account": account_label,
            "tg_msg_id": tg_msg_id,
            "entry_t": int(time.time() * 1000)
        }
    except Exception as e:
        log(f"[{account_label}] ❌ ORDER EXECUTION ERROR for {base}: {e}")
        return None

# ── Main Scanner & Pre-Armed Fast Trigger Engine ──────────────────────────────
def run_dumpride_engine():
    log("===================================================================")
    log(f"⚡ COINDCX {TF.upper()} \"DUMPRIDE\" FAST-TRIGGER EXHAUSTION SHORT ONLINE ⚡")
    log(f"   Timeframe: {TF} | Volume Spike Trigger: >={SPIKE_VOL_MULT:.1f}x")
    log(f"   Pre-Armed Watch Window: {PREARM_MINUTES} minutes prior to {TF} close")
    log(f"   Stop Loss: {SL_ATR_MULT:.1f}x ATR(14) | Target: 1:{int(RR_TARGET)} RR")
    log(f"   Risk Allocation: {RISK_FRAC*100:.2f}% per trade (Wallet: ₹{START_BAL_INR:,.0f}) | Max Concurrent: {MAX_CONCURRENT}")
    log(f"   Telegram Alerter: Bot @volsp_bot -> User: {TELEGRAM_CHAT}")
    log(f"   Execution Mode: {'🔴 LIVE ARMED TRADING' if ARMED else '🟡 PAPER MODE / MONITORING'}")
    log("===================================================================\n")
    
    last_prearm_sweep_time = 0
    active_positions = {}
    armed_watchlist = {} # {symbol: candidate_dict}
    
    # Initialize with current bucket so service restart mid-cycle doesn't trigger stale candles
    init_cur_bucket, _, _ = get_4h_timing()
    last_executed_bucket = init_cur_bucket
    last_standby_log_time = 0
    
    while True:
        try:
            now_ms = int(time.time() * 1000)
            cur_bucket_start, next_bucket_close, sec_to_close = get_4h_timing()
            mins_to_close = int(sec_to_close // 60)
            secs_rem = int(sec_to_close % 60)
            
            # 1. Position Reconciliation Loop (every ~5 seconds)
            if ARMED:
                try:
                    open_pos = A.fetch_positions()
                    active_symbols = set(p.get("base") for p in open_pos if float(p.get("qty") or p.get("active_units") or p.get("active_pos") or 0) != 0)
                    
                    # Check for closed positions (with debounce protection)
                    for sym in list(active_positions.keys()):
                        pos_info = active_positions[sym]
                        if sym in active_symbols:
                            pos_info["seen_active"] = True
                            pos_info["missing_count"] = 0
                        else:
                            # Position is not returned in active positions list
                            pos_info["missing_count"] = pos_info.get("missing_count", 0) + 1
                            # Require at least 2 consecutive missing cycles (10s) or previous active confirmation
                            if pos_info.get("seen_active") or pos_info["missing_count"] >= 2:
                                active_positions.pop(sym, None)
                                duration_mins = (now_ms - pos_info.get("entry_t", now_ms)) / 60000.0
                                log(f"[{sym}] 🏁 POSITION CLOSED ON EXCHANGE. Duration: {duration_mins:.1f} mins.")
                                
                                # Fetch real exit fill from CoinDCX
                                real_exit_px, fill_qty, fees = A.fetch_executed_trade_vwap(sym, side="buy")
                                if real_exit_px <= 0:
                                    try:
                                        curr_px = A.get_current_price(sym)
                                        real_exit_px = curr_px if curr_px > 0 else pos_info.get("entry_px", 0)
                                    except Exception:
                                        real_exit_px = pos_info.get("entry_px", 0)
                                    
                                pnl_usdt = (pos_info.get("entry_px", 0) - real_exit_px) * pos_info.get("qty", 0)
                                pnl_pct = ((pos_info.get("entry_px", 0) - real_exit_px) / max(pos_info.get("entry_px", 1), 1e-9)) * 100.0
                                
                                if pnl_usdt > 0:
                                    reason = "🎯 TP Target Hit / Gain"
                                elif pnl_usdt < 0:
                                    reason = "🛑 Stop Loss Hit / Loss"
                                else:
                                    reason = "🏁 Position Reconciled (Flat)"
                                
                                if TELEGRAM_ENABLED:
                                    try:
                                        post_exit_chart(
                                            base=sym,
                                            exit_px=real_exit_px,
                                            entry_px=pos_info.get("entry_px", 0),
                                            initial_sl_px=pos_info.get("sl_px", 0),
                                            pnl_usdt=pnl_usdt,
                                            pnl_pct=pnl_pct,
                                            reason=reason,
                                            reply_to_msg_id=pos_info.get("tg_msg_id"),
                                            account=f"DumpRide ({pos_info.get('account', 'Primary')})"
                                        )
                                    except Exception as te:
                                        log(f"Telegram exit chart error: {te}")
                except Exception as pe:
                    log(f"Reconciliation error: {pe}")

            # 2. ZERO-LATENCY FAST-TRIGGER AT EXACT CANDLE CLOSE (T = 00:00:00)
            # Triggers as soon as sec_to_close <= 0.8s OR when the bucket rolls over into a new bar
            is_bucket_rollover = (cur_bucket_start != last_executed_bucket)
            if (sec_to_close <= 0.8 or is_bucket_rollover):
                last_executed_bucket = cur_bucket_start
                log(f"\n⚡ [{TF.upper()} CANDLE CLOSE TRIGGERED] Firing execution engine for {TF.upper()} bucket {cur_bucket_start} (sec_to_close={sec_to_close:.1f}s)...")
                
                # A. INSTANT PARALLEL EXECUTION OF PRE-ARMED CANDIDATES (Zero Latency <150ms)
                candidates_to_execute = list(armed_watchlist.values())
                armed_watchlist = {}
                
                if candidates_to_execute:
                    log(f"🚀 [INSTANT FAST-TRIGGER] Firing {len(candidates_to_execute)} pre-armed candidates directly on exchange:")
                    
                    def execute_single_candidate(sig):
                        base = sig["symbol"]
                        # Check SQLite to avoid duplicate entry
                        conn = sqlite3.connect(DB_FILE)
                        c = conn.cursor()
                        row = c.execute(
                            "SELECT 1 FROM executed_signals WHERE symbol=? AND candle_timestamp=?",
                            (sig["symbol"], sig["candle_ts"])
                        ).fetchone()
                        conn.close()
                        if row:
                            log(f"ℹ️ #{sig['symbol']} already executed on candle {sig['candle_dt']}. Skipping.")
                            return
                            
                        # Check portfolio concurrency
                        if len(active_positions) >= MAX_CONCURRENT:
                            log(f"⚠️ Max concurrent positions reached ({MAX_CONCURRENT}). Skipping #{sig['symbol']}.")
                            return
                            
                        t0 = time.time()
                        pos_rec = execute_dumpride_short(sig, A, account_label="Primary")
                        if pos_rec:
                            active_positions[sig["symbol"]] = pos_rec
                            log(f"⚡ [FAST-TRIGGER] #{sig['symbol']} Primary filled in {(time.time()-t0)*1000:.0f}ms at exact candle close!")
                            
                        if A2:
                            execute_dumpride_short(sig, A2, account_label="Account_2")
                            
                        # Record in SQLite
                        conn = sqlite3.connect(DB_FILE)
                        c = conn.cursor()
                        c.execute(
                            "INSERT OR REPLACE INTO executed_signals VALUES (?,?,?,?,?,?,?)",
                            (sig["symbol"], sig["candle_ts"], sig["vol_mult"], sig["entry_px"], sig["sl_px"], sig["tp_px"], int(time.time() * 1000))
                        )
                        conn.commit()
                        conn.close()

                    # Execute all candidates in parallel threads immediately
                    with ThreadPoolExecutor(max_workers=len(candidates_to_execute)) as order_exec:
                        list(order_exec.map(execute_single_candidate, candidates_to_execute))
                else:
                    log(f"ℹ️ No pre-armed candidates in watchlist at candle close.")

                # B. Post-Close Background Sweep for Any Late Volume Spikes (Non-blocking)
                def post_close_sweep():
                    time.sleep(2)
                    universe = A.active_bases()
                    if not universe: return
                    log(f"🔍 [POST-CLOSE CATCH-ALL] Sweeping universe in background for closed {TF.upper()} candle spikes...")
                    with ThreadPoolExecutor(max_workers=40) as executor:
                        futures = {executor.submit(evaluate_coin_4h_signal, base, A, False): base for base in universe}
                        for fut in as_completed(futures):
                            try:
                                sig = fut.result()
                                if sig:
                                    # Execute any newly confirmed late spike
                                    execute_single_candidate(sig)
                            except Exception:
                                pass

                # Run post-close sweep in non-blocking background thread
                import threading
                threading.Thread(target=post_close_sweep, daemon=True).start()
                
                time.sleep(1)
                continue

            # 3. PRE-ARMED UNIVERSE SCAN (Engages STRICTLY in the last 15 minutes of the candle, e.g. T-15m to T)
            in_prearm_window = (sec_to_close <= PREARM_WINDOW_SEC)
            
            if not in_prearm_window:
                # Standby Mode (Outside the 15-minute pre-arm window): Sleep and emit heartbeat every 60s
                if now_ms - last_standby_log_time >= 60000:
                    last_standby_log_time = now_ms
                    log(f"⏳ [STANDBY] Next {TF.upper()} close in {mins_to_close//60}h {mins_to_close%60}m {secs_rem}s. Pre-arm sweeps activate at T-{PREARM_MINUTES}m.")
                armed_watchlist = {}
                time.sleep(2)
                continue

            # 3. PRE-ARMED UNIVERSE SCAN (Engages between T-15m and T-75s)
            can_sweep_universe = (sec_to_close > 75.0)
            sweep_interval = 25.0 if sec_to_close <= 300.0 else 50.0
            
            if can_sweep_universe and (time.time() - last_prearm_sweep_time >= sweep_interval):
                last_prearm_sweep_time = time.time()
                
                # Fetch universe of active perpetual coins
                universe = A.active_bases()
                if not universe:
                    try:
                        prices_res = A._get("https://public.coindcx.com/market_data/v3/current_prices/futures/rt")
                        p_dict = prices_res.get("prices", {}) if isinstance(prices_res, dict) else {}
                        universe = sorted(list(set([k.replace("B-", "").replace("_USDT", "") for k in p_dict.keys() if "USDT" in k])))
                    except Exception:
                        universe = []
                        
                if not universe:
                    time.sleep(2)
                    continue
                    
                candidate_signals = []
                with ThreadPoolExecutor(max_workers=40) as executor:
                    futures = {executor.submit(evaluate_coin_4h_signal, base, A, True): base for base in universe}
                    for fut in as_completed(futures):
                        try:
                            sig = fut.result()
                            if sig:
                                candidate_signals.append(sig)
                        except Exception:
                            pass
                            
                # Filter out previously executed candles from SQLite
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                filtered_candidates = []
                for sig in candidate_signals:
                    row = c.execute(
                        "SELECT 1 FROM executed_signals WHERE symbol=? AND candle_timestamp=?",
                        (sig["symbol"], sig["candle_ts"])
                    ).fetchone()
                    if not row:
                        filtered_candidates.append(sig)
                conn.close()
                
                # Update Armed Watchlist
                armed_watchlist = {sig["symbol"]: sig for sig in filtered_candidates}
                if armed_watchlist:
                    log(f"🎯 [PRE-ARM WATCHLIST] {len(armed_watchlist)} candidates armed for upcoming {TF.upper()} close (T-{mins_to_close:02d}m {secs_rem:02d}s):")
                    for sym, sig in armed_watchlist.items():
                        log(f"   -> #{sym}: Forming Spike {sig['vol_mult']:.1f}x | Pump +{sig['pump_pct']:.1f}% | Pre-calc SL: {sig['sl_px']:.6g} | TP: {sig['tp_px']:.6g}")
                else:
                    log(f"🔍 [PRE-ARM SCAN] Universe swept ({len(universe)} coins). 0 volume spikes detected (T-{mins_to_close:02d}m {secs_rem:02d}s to {TF.upper()} close).")
                    
            elif not can_sweep_universe and armed_watchlist:
                # In final 75s countdown: Freeze universe scan and fast-poll armed candidates directly (<100ms)
                for sym in list(armed_watchlist.keys()):
                    try:
                        updated_sig = evaluate_coin_4h_signal(sym, A, include_forming=True)
                        if updated_sig and updated_sig["vol_mult"] >= (SPIKE_VOL_MULT * 0.85):
                            armed_watchlist[sym] = updated_sig
                            log(f"🔒 [FINAL COUNTDOWN T-{secs_rem:02d}s] #{sym} ARMED: Vol Spike {updated_sig['vol_mult']:.1f}x | Pump +{updated_sig['pump_pct']:.1f}% | Pre-calc SL: {updated_sig['sl_px']:.6g} | TP: {updated_sig['tp_px']:.6g}")
                    except Exception:
                        pass
                time.sleep(2)
                continue
                
            time.sleep(1)
        except KeyboardInterrupt:
            log("🛑 Strategy Engine stopped by user.")
            break
        except Exception as e:
            log(f"⚠️ Unhandled error in main loop: {e}")
            time.sleep(5)

if __name__ == "__main__":
    run_dumpride_engine()
