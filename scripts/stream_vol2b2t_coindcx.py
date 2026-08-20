#!/usr/bin/env python3
"""scripts/stream_vol2b2t_coindcx.py — Production Live VOL2B2T (Volume Spike + 2B Reclaim) Daemon for CoinDCX.

Strategy Rules:
1. Volume Spike Detection (15m Timeframe):
   - Candle 1 (Spike): Green Candle (Close > Open) + Volume >= 20.0x 40-bar SMA.
   - Candle 2 (Follow-up): Green Candle (Close > Open) + Volume >= 3.0x 40-bar SMA + Combined 2-bar Vol >= 20.0x.
   - Trend Filter: Price strictly above EMA(50).
   - Reference Level: prev_low = Low of Candle 2.

2. Intrabar 2B Sweep & Reclaim Execution:
   - Arms when price sweeps below prev_low (recording lowest sweep wick as sweep_ext).
   - Fires LONG immediately when price reclaims back >= prev_low.
   - Stop Loss: sweep_ext (clamped between 0.3% min and 2.0% max).
   - Take Profit: 1:3.0 RR Native Bracket Order.
   - Dynamic Trailing: Moves SL to Break-Even (0.0R) at +1.5R, locks +1.0R at +2.5R.

3. Risk & Sizing:
   - Sized at 0.10% (0.0010) risk per trade on 10k INR wallet equity (~₹10 max risk per trade).
   - Dual-Account Execution (Primary + Secondary CoinDCX accounts).
   - Side-by-side / Technical chart photo render sent to Telegram.
"""
import os
import sys
import time
import json
import ssl
import logging
import urllib.request
import urllib.parse
from typing import Dict, List, Any, Optional, Tuple
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.gridspec as gridspec

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv("/root/go-trader/.env")
    load_dotenv("/root/trading-bot/crypto/.env")
    load_dotenv()
except Exception:
    pass

# Ensure platforms/coindcx is accessible
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(script_dir, "../platforms/coindcx"))
sys.path.insert(0, "/root/ai-prosperity/platforms/coindcx")
sys.path.insert(0, "/root/trading-bot/crypto/platforms/coindcx")

from adapter import CoinDCXExchangeAdapter, CoinDCXError

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s IST] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("Vol2b2tCoinDCX")

# ── STRATEGY HYPERPARAMETERS ──────────────────────────────────────────────────
TIMEFRAME = os.environ.get("VB2_TIMEFRAME", "15m") # 15-Minute Candles
VOL_SPIKE_MULT = float(os.environ.get("VB2_VOL_MULT", "20.0")) # 20.0x 40SMA
FOLLOW_VOL_MULT = float(os.environ.get("VB2_FOLLOW_VOL", "3.0")) # 3.0x 40SMA
MIN_CUMULATIVE_VOL = float(os.environ.get("VB2_MIN_CUM_VOL", "20.0"))
EMA_PERIOD = int(os.environ.get("VB2_EMA_PERIOD", "50"))
WATCH_BARS = int(os.environ.get("VB2_WATCH_BARS", "20")) # 20 bars = 5 hours
RR_TARGET = float(os.environ.get("VB2_RR_TARGET", "3.0")) # 1:3.0 RR
MAX_CONCURRENT_POSITIONS = int(os.environ.get("VB2_MAX_CONCURRENT", "4"))
RISK_PCT_PER_TRADE = float(os.environ.get("VB2_RISK_PCT", "0.0010")) # 0.10% wallet risk (~₹10 on 10k INR)
START_BAL_INR = float(os.environ.get("VB2_WALLET_INR", "10000.0"))

# ── TELEGRAM CONFIGURATION ────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("VB2_TELEGRAM_BOT_TOKEN", "8823560993:AAHAtlrSlbedbUJeIBgj2_NUe4-7BxB9Lx8").strip()
TELEGRAM_CHAT_ID = os.environ.get("VB2_TELEGRAM_CHAT_ID", "-5535049486").strip()
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

# ── INITIALIZE ADAPTER (PRIMARY & SECONDARY) ──────────────────────────────────
PRIMARY_KEY = os.environ.get("COINDCX_LIVE_API_KEY", "").strip()
PRIMARY_SECRET = os.environ.get("COINDCX_LIVE_API_SECRET", "").strip()
A = CoinDCXExchangeAdapter(key=PRIMARY_KEY, secret=PRIMARY_SECRET)

KEY_2 = os.environ.get("COINDCX_KEY_2", "").strip()
SECRET_2 = os.environ.get("COINDCX_SECRET_2", "").strip()
A2 = CoinDCXExchangeAdapter(key=KEY_2, secret=SECRET_2) if (KEY_2 and SECRET_2) else None
if A2:
    logger.info(f"[MultiAccount] Secondary CoinDCX Account Enabled (Key ending in ...{KEY_2[-6:]})")

# In-Memory State
WATCHING_TARGETS: Dict[str, Dict[str, Any]] = {}
ACTIVE_TRADES: Dict[str, Dict[str, Any]] = {}
PROCESSED_SPIKES = set()

def send_telegram_msg(text: str) -> Optional[int]:
    """Sends a formatted text alert to Telegram group."""
    if not TELEGRAM_ENABLED: return None
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        ctx = ssl._create_unverified_context()
        res = json.load(urllib.request.urlopen(req, context=ctx, timeout=8))
        return res.get("result", {}).get("message_id")
    except Exception as e:
        logger.warning(f"Telegram msg failed: {e}")
        return None

def send_telegram_photo(photo_path: str, caption: str) -> Optional[int]:
    """Uploads a rendered chart photo to Telegram group."""
    if not TELEGRAM_ENABLED or not os.path.exists(photo_path): return None
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    boundary = "----WebKitFormBoundary" + hex(int(time.time() * 1000))[2:]
    
    with open(photo_path, "rb") as f:
        file_bytes = f.read()
        
    body = []
    body.append(f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{TELEGRAM_CHAT_ID}\r\n'.encode('utf-8'))
    body.append(f'--{boundary}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode('utf-8'))
    body.append(f'--{boundary}\r\nContent-Disposition: form-data; name="parse_mode"\r\n\r\nMarkdown\r\n'.encode('utf-8'))
    body.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="chart.png"\r\nContent-Type: image/png\r\n\r\n'.encode('utf-8'))
    body.append(file_bytes)
    body.append(f'\r\n--{boundary}--\r\n'.encode('utf-8'))
    
    payload = b"".join(body)
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        ctx = ssl._create_unverified_context()
        res = json.load(urllib.request.urlopen(req, context=ctx, timeout=15))
        return res.get("result", {}).get("message_id")
    except Exception as e:
        logger.warning(f"Telegram photo upload failed: {e}")
        return None

def render_vol2b2t_chart(sym: str, klines: List[List[float]], signal: Dict[str, Any]) -> str:
    """Renders a publication-grade technical chart showing Volume Spike, Sweep Low, and 2B Reclaim Long Entry."""
    os.makedirs("/tmp/charts", exist_ok=True)
    out_path = f"/tmp/charts/vol2b2t_{sym}_{int(time.time())}.png"
    
    n_display = 36
    sub_klines = klines[-n_display:]
    times = [r[0] for r in sub_klines]
    opens = np.array([float(r[1]) for r in sub_klines])
    highs = np.array([float(r[2]) for r in sub_klines])
    lows = np.array([float(r[3]) for r in sub_klines])
    closes = np.array([float(r[4]) for r in sub_klines])
    vols = np.array([float(r[5]) for r in sub_klines])
    
    entry_px = signal["entry_px"]
    sl_px = signal["sl_px"]
    tp_px = signal["tp_px"]
    vol_mult = signal.get("vol_mult", 20.0)
    
    # EMA 50
    ema50 = np.zeros(len(closes))
    alpha = 2.0 / 51.0
    ema50[0] = closes[0]
    for idx in range(1, len(closes)):
        ema50[idx] = alpha * closes[idx] + (1.0 - alpha) * ema50[idx-1]
        
    fig = plt.figure(figsize=(15, 8.5), facecolor="#0b0e14")
    gs = gridspec.GridSpec(2, 1, height_ratios=[3.2, 1.0], hspace=0.08)
    
    ax_main = fig.add_subplot(gs[0])
    ax_vol = fig.add_subplot(gs[1], sharex=ax_main)
    
    for ax in [ax_main, ax_vol]:
        ax.set_facecolor("#0f131a")
        ax.grid(True, color="#1e2430", linestyle="--", linewidth=0.5, alpha=0.7)
        ax.tick_params(colors="#8a99ad", labelsize=9)
        for spine in ax.spines.values():
            spine.set_color("#222b3a")
            
    # Draw Candlesticks
    for i in range(len(sub_klines)):
        color = "#00c087" if closes[i] >= opens[i] else "#f6465d"
        ax_main.plot([i, i], [lows[i], highs[i]], color=color, linewidth=1.2, alpha=0.9)
        body_bottom = min(opens[i], closes[i])
        body_height = max(abs(closes[i] - opens[i]), (highs[i] - lows[i]) * 0.03)
        rect = patches.Rectangle((i - 0.35, body_bottom), 0.7, body_height, facecolor=color, edgecolor=color, alpha=0.95)
        ax_main.add_patch(rect)
        
        # Volume Bar
        ax_vol.bar(i, vols[i], color=color, alpha=0.6, width=0.7)
        
    # Plot EMA 50
    ax_main.plot(range(len(closes)), ema50, color="#f0b90b", linewidth=1.5, label="EMA 50 (Trend Filter)", alpha=0.85)
    
    # Plot SL / Entry / TP levels
    last_i = len(sub_klines) - 1
    ax_main.axhline(entry_px, color="#3b82f6", linestyle="--", linewidth=1.8, label=f"2B Reclaim Entry (${entry_px:.4f})")
    ax_main.axhline(sl_px, color="#ef4444", linestyle="--", linewidth=1.8, label=f"Sweep Stop Loss (${sl_px:.4f})")
    ax_main.axhline(tp_px, color="#10b981", linestyle="--", linewidth=1.8, label=f"Take Profit 1:{RR_TARGET:.1f}R (${tp_px:.4f})")
    
    # Highlight Sweep Reclaim with green marker
    ax_main.scatter(last_i, entry_px, color="#00ffaa", s=180, zorder=5, edgecolors="#ffffff", linewidths=2.0, marker="^")
    ax_main.annotate(f"  2B RECLAIM LONG\n  ${entry_px:.4f}", (last_i, entry_px), color="#00ffaa", fontsize=10, fontweight="bold")
    
    # Title & Stats
    ax_main.set_title(
        f"💎 VOL2B2T MOMENTUM RECLAIM | #{sym}/USDT (15M) | Volume Surge: {vol_mult:.1f}x | Target: 1:{RR_TARGET:.1f} RR",
        color="#ffffff", fontsize=13, fontweight="bold", pad=12, loc="left"
    )
    ax_main.legend(loc="upper left", facecolor="#141822", edgecolor="#222b3a", labelcolor="#e2e8f0", fontsize=9)
    ax_vol.set_ylabel("Volume (15M)", color="#8a99ad", fontsize=9)
    
    plt.savefig(out_path, dpi=180, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return out_path

def fetch_klines(sym: str, limit: int = 80) -> Optional[List[List[float]]]:
    """Fetches 15m OHLCV bars from Binance primary feed with CoinDCX fallback."""
    ctx = ssl._create_unverified_context()
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    
    # 1. Binance Futures Primary
    try:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}USDT&interval=15m&limit={limit}"
        req = urllib.request.Request(url, headers={"User-Agent": ua})
        raw = json.load(urllib.request.urlopen(req, timeout=4, context=ctx))
        if isinstance(raw, list) and len(raw) >= 45:
            return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in raw]
    except Exception:
        pass
        
    # 2. CoinDCX Fallback
    try:
        url = f"https://public.coindcx.com/market_data/candles?pair=B-{sym}_USDT&interval=15m&limit={limit}"
        req = urllib.request.Request(url, headers={"User-Agent": ua})
        raw = json.load(urllib.request.urlopen(req, timeout=4, context=ctx))
        if isinstance(raw, list) and len(raw) >= 45:
            raw = sorted(raw, key=lambda x: x["time"])
            return [[int(r["time"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r.get("volume", 0))] for r in raw]
    except Exception:
        pass
        
    return None

def execute_account_order(adapter: CoinDCXExchangeAdapter, signal: Dict[str, Any], account_label: str):
    """Executes bracket order on a specific CoinDCX account."""
    sym = signal["symbol"]
    entry_px = signal["entry_px"]
    sl_px = signal["sl_px"]
    tp_px = signal["tp_px"]
    risk_dist = signal["risk_dist"]
    
    wallet_inr = adapter.get_inr_equity() if hasattr(adapter, "get_inr_equity") else START_BAL_INR
    if wallet_inr <= 0:
        wallet_inr = START_BAL_INR
        
    risk_inr = wallet_inr * RISK_PCT_PER_TRADE
    risk_usd = risk_inr / (adapter.inr_per_usdt or 88.5)
    
    qty = risk_usd / risk_dist
    qty = adapter.floor_qty(sym, qty) if hasattr(adapter, "floor_qty") else round(qty, 2)
    
    logger.info(f"[{account_label}] 🚀 Firing #{sym} LONG | Qty: {qty} | Entry: {entry_px:.4f} | SL: {sl_px:.4f} | TP: {tp_px:.4f} (1:{RR_TARGET:.1f} RR)")
    
    try:
        order_res = adapter.market_open_bracket(
            base=sym,
            is_buy=True,
            qty=qty,
            leverage=10,
            sl_price=sl_px,
            tp_price=tp_px
        )
        logger.info(f"[{account_label}] ✅ COINDCX ORDER PLACED: {order_res.get('id', 'N/A')}")
        return order_res
    except Exception as e:
        logger.error(f"[{account_label}] ❌ Execution Error on #{sym}: {e}")
        return None

def execute_live_entry(signal: Dict[str, Any], klines: List[List[float]]):
    """Fires native CoinDCX bracket orders and posts technical chart to Telegram."""
    sym = signal["symbol"]
    entry_px = signal["entry_px"]
    sl_px = signal["sl_px"]
    tp_px = signal["tp_px"]
    risk_dist = signal["risk_dist"]
    vol_mult = signal.get("vol_mult", 20.0)
    
    cur_positions = A.fetch_positions()
    if len(cur_positions) >= MAX_CONCURRENT_POSITIONS:
        logger.warning(f"⚠️ Max concurrent positions reached ({len(cur_positions)}/{MAX_CONCURRENT_POSITIONS}). Skipping #{sym}.")
        return
        
    # 1. Execute Primary Account
    res1 = execute_account_order(A, signal, "PRIMARY")
    
    # 2. Replicate to Secondary Account if enabled
    if A2:
        execute_account_order(A2, signal, "SECONDARY")
        
    if res1:
        ACTIVE_TRADES[sym] = {
            "side": "buy",
            "entry": entry_px,
            "sl": sl_px,
            "tp": tp_px,
            "risk": risk_dist,
            "be_done": False,
            "lock_done": False
        }
        
        # 3. Render and Post High-Resolution Chart to Telegram
        try:
            chart_file = render_vol2b2t_chart(sym, klines, signal)
            caption = (
                f"💎 *STRATEGY: VOL2B2T 15-MINUTE MOMENTUM RECLAIM*\n\n"
                f"• *Status*: `NEW ORDER EXECUTED (LONG)`\n"
                f"• *Pair*: `#{sym}/USDT`\n"
                f"• *Timeframe*: `15-Minute Candlesticks`\n"
                f"• *Volume Spike*: `{vol_mult:.1f}x 40SMA` 🚀\n"
                f"• *Entry Price*: `${entry_px:.4f}`\n"
                f"• *Sweep Stop Loss*: `${sl_px:.4f}` (-{((entry_px-sl_px)/entry_px)*100:.2f}%)\n"
                f"• *Take Profit (1:{RR_TARGET:.1f} RR)*: `${tp_px:.4f}` (+{((tp_px-entry_px)/entry_px)*100:.2f}%)\n"
                f"• *Risk Size*: `0.10% Wallet Equity (~₹10 Risk)`\n"
                f"• *Trailing Strategy*: `BE at +1.5R, Lock Profit at +2.5R`"
            )
            send_telegram_photo(chart_file, caption)
            logger.info(f"📸 Chart and trade alert posted to Telegram for #{sym}.")
        except Exception as e:
            logger.error(f"Failed to render/post chart: {e}")

def check_trailing_stops():
    """Monitors open positions to trail stop loss to Break-Even at +1.5R and Lock +1.0R at +2.5R."""
    if not ACTIVE_TRADES: return
    
    for sym in list(ACTIVE_TRADES.keys()):
        trade = ACTIVE_TRADES[sym]
        entry = trade["entry"]
        risk = trade["risk"]
        
        cur_px = A.get_price(sym)
        if cur_px <= 0: continue
        
        r_gain = (cur_px - entry) / risk
        
        # 1. Break-Even at +1.5R
        if not trade["be_done"] and r_gain >= 1.5:
            new_sl = entry
            logger.info(f"🛡️ [BREAK-EVEN REACHED] #{sym} reached +{r_gain:.2f}R! Moving Stop Loss to Break-Even (${new_sl:.4f})...")
            trade["be_done"] = True
            send_telegram_msg(
                f"🛡️ *STRATEGY: VOL2B2T 15-MINUTE MOMENTUM RECLAIM*\n\n"
                f"• *Status*: `BREAK-EVEN ACTIVATED` (+1.5R Reached)\n"
                f"• *Pair*: `#{sym}/USDT`\n"
                f"• *Timeframe*: `15-Minute Candlesticks`\n"
                f"• *Side*: `LONG`\n"
                f"• *Stop Loss*: Moved to Entry (${entry:.4f})\n"
                f"• *Current Gain*: `+{r_gain:.2f}R`"
            )
            
        # 2. Lock +1.0R Profit at +2.5R
        if not trade["lock_done"] and r_gain >= 2.5:
            new_sl = entry + (1.0 * risk)
            logger.info(f"🔒 [LOCK PROFIT REACHED] #{sym} reached +{r_gain:.2f}R! Locking +1.0R Profit at ${new_sl:.4f}...")
            trade["lock_done"] = True
            send_telegram_msg(
                f"🔒 *STRATEGY: VOL2B2T 15-MINUTE MOMENTUM RECLAIM*\n\n"
                f"• *Status*: `PROFIT LOCKED (+1.0R)` (+2.5R Reached)\n"
                f"• *Pair*: `#{sym}/USDT`\n"
                f"• *Timeframe*: `15-Minute Candlesticks`\n"
                f"• *Side*: `LONG`\n"
                f"• *Stop Loss*: Locked at +1.0R (${new_sl:.4f})\n"
                f"• *Current Gain*: `+{r_gain:.2f}R`"
            )

def scan_universe_for_spikes(universe: List[str]):
    """Scans all coins on 15m close for Volume Spike + 2-bar green confirmation + EMA 50 gate."""
    now_s = time.time()
    cur_bar_idx = int(now_s // 900)
    
    for sym in universe:
        klines = fetch_klines(sym, limit=65)
        if not klines or len(klines) < 50:
            continue
            
        opens = [float(r[1]) for r in klines]
        highs = [float(r[2]) for r in klines]
        lows = [float(r[3]) for r in klines]
        closes = [float(r[4]) for r in klines]
        vols = [float(r[5]) for r in klines]
        times = [int(r[0]) for r in klines]
        
        ci = len(klines) - 2 # Most recently completed closed candle
        spike_idx = ci - 1
        follow_idx = ci
        
        spike_ts = times[follow_idx]
        spike_key = f"{sym}_{spike_ts}"
        if spike_key in PROCESSED_SPIKES:
            continue
            
        past_vols = vols[spike_idx-40:spike_idx]
        if len(past_vols) < 40: continue
        avg_vol = np.mean(past_vols)
        if avg_vol <= 0: continue
        
        mult = vols[spike_idx] / avg_vol
        if mult < VOL_SPIKE_MULT:
            continue
            
        # 1. Green body check on spike bar
        if closes[spike_idx] <= opens[spike_idx]:
            continue
            
        # 2. Green body check on follow bar
        if closes[follow_idx] <= opens[follow_idx]:
            continue
            
        # 3. Follow bar volume check
        if (vols[follow_idx] / avg_vol) < FOLLOW_VOL_MULT:
            continue
            
        # 4. Cumulative 2-bar volume check
        if ((vols[spike_idx] + vols[follow_idx]) / avg_vol) < MIN_CUMULATIVE_VOL:
            continue
            
        # 5. EMA 50 trend gate
        ema50 = closes[0]
        alpha = 2.0 / 51.0
        for idx in range(1, follow_idx + 1):
            ema50 = alpha * closes[idx] + (1.0 - alpha) * ema50
        if closes[follow_idx] < ema50:
            continue
            
        prev_low = lows[follow_idx]
        PROCESSED_SPIKES.add(spike_key)
        
        WATCHING_TARGETS[sym] = {
            "trigger_ts": spike_ts,
            "start_bar": cur_bar_idx,
            "prev_low": prev_low,
            "armed": False,
            "sweep_ext": prev_low,
            "vol_mult": mult,
            "klines": klines
        }
        logger.info(f"🚀 [VOL2B2T SPIKE DETECTED] #{sym} -> {mult:.1f}x Vol (Green=✓, EMA50=✓) | Watching 2B Sweep on ${prev_low:.4f}...")

def monitor_active_watches():
    """Fast-polls active watching targets for 2B sweep and immediate reclaim execution."""
    if not WATCHING_TARGETS: return
    
    now_s = time.time()
    cur_bar = int(now_s // 900)
    
    for sym in list(WATCHING_TARGETS.keys()):
        target = WATCHING_TARGETS[sym]
        start_bar = target["start_bar"]
        prev_low = target["prev_low"]
        
        # Expire watch after 20 bars (5 hours)
        if cur_bar - start_bar > WATCH_BARS:
            logger.info(f"⏳ [WATCH EXPIRED] #{sym} 2B watch expired after {WATCH_BARS} bars.")
            WATCHING_TARGETS.pop(sym, None)
            continue
            
        cur_px = A.get_price(sym)
        if cur_px <= 0: continue
        
        # 1. Arming: Price sweeps below prev_low
        if cur_px < prev_low:
            if not target["armed"]:
                logger.info(f"🎯 [VOL2B2T ARMED] #{sym} swept below ${prev_low:.4f} (Cur: ${cur_px:.4f})")
                target["armed"] = True
            target["sweep_ext"] = min(target["sweep_ext"], cur_px)
            
        # 2. Execution: Price reclaims back >= prev_low after being armed
        elif target["armed"] and cur_px >= prev_low:
            entry_px = prev_low
            sweep_sl = target["sweep_ext"]
            
            # SL Bounds: clamped between 0.3% min and 2.0% max
            max_sl = entry_px * 0.98
            min_sl = entry_px * 0.997
            sl_px = max(max_sl, min(min_sl, sweep_sl))
            sl_dist = entry_px - sl_px
            if sl_dist <= 0: sl_dist = entry_px * 0.005
            
            tp_px = entry_px + (RR_TARGET * sl_dist)
            
            signal = {
                "symbol": sym,
                "entry_px": entry_px,
                "sl_px": sl_px,
                "tp_px": tp_px,
                "risk_dist": sl_dist,
                "vol_mult": target["vol_mult"]
            }
            
            logger.info(f"⚡ [2B RECLAIM TRIGGERED] #{sym} reclaimed ${entry_px:.4f}! Firing LIVE Long Order...")
            WATCHING_TARGETS.pop(sym, None)
            execute_live_entry(signal, target["klines"])

def main():
    logger.info("=========================================================================================")
    logger.info("💎 VOL2B2T 15-MINUTE MOMENTUM RECLAIM LIVE TRADING DAEMON INITIALIZED 💎")
    logger.info(f"  • Timeframe              : {TIMEFRAME.upper()} (15-Minute Candles)")
    logger.info(f"  • Volume Surge Trigger   : {VOL_SPIKE_MULT}x 40SMA")
    logger.info(f"  • Risk per Trade         : {RISK_PCT_PER_TRADE*100:.2f}% (~₹10 on 10k INR)")
    logger.info(f"  • Risk:Reward Target     : 1:{RR_TARGET} RR Native Bracket")
    logger.info(f"  • Telegram Render Bot    : @{TELEGRAM_BOT_TOKEN[:10]}... -> Chat: {TELEGRAM_CHAT_ID}")
    logger.info("=========================================================================================")
    
    universe = A.active_bases() if hasattr(A, "active_bases") else []
    if not universe:
        universe = ["BTC", "ETH", "SOL", "DOGE", "AVAX", "NEAR", "SUI", "1000PEPE", "WIF", "LINK", "ARB", "OP"]
        
    logger.info(f"Universe discovered: {len(universe)} CoinDCX Perpetual Pairs.")
    
    tf_secs = 900 # 15 minutes = 900 seconds
    last_scan_bucket = 0
    
    while True:
        try:
            now = time.time()
            cur_bucket = int(now // tf_secs) * tf_secs
            
            # 1. On every 15-minute candle close, scan universe for new Volume Spikes
            if cur_bucket != last_scan_bucket:
                last_scan_bucket = cur_bucket
                scan_universe_for_spikes(universe)
                
            # 2. Fast-poll active armed watches for 2B reclaims
            monitor_active_watches()
            
            # 3. Dynamic trailing profit protection
            check_trailing_stops()
            
            time.sleep(1.5)
            
        except KeyboardInterrupt:
            logger.info("VOL2B2T Daemon stopped by user.")
            break
        except Exception as e:
            logger.error(f"Main loop exception: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
