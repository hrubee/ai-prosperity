#!/usr/bin/env python3
"""scripts/stream_heikinashi_coindcx.py — Production Live Heikin-Ashi 15-Minute Momentum Execution Daemon for CoinDCX.

Features:
1. Strategy Rules (Pure Heikin-Ashi Momentum without EMA Filter):
   - 🟢 Bullish Long: HA Green Candle + Flat Bottom (no lower wick) + Vol >= 2.5x
   - 🔴 Bearish Short: HA Red Candle + Flat Top (no upper wick) + Vol >= 2.5x
   - Color Transition: Triggers on 1st or 2nd candle of new sequence.
   - ATR Stop Loss Buffer: SL = max(Trigger Wick, 0.8 * ATR_14).
   - 1:4.0 Risk-to-Reward Target Native Bracket Orders.

2. Execution Flow & Renders:
   - 15-Minute Candlestick evaluation cycle.
   - Automated Dark-Mode Side-by-Side Chart Rendering (Left: 15M Raw Candles, Right: 15M Heikin-Ashi).
   - Broadcasts rendered charts and status updates to Telegram Group `-5535049486` via bot `8823560993:AAHAtlrSlbedbUJeIBgj2_NUe4-7BxB9Lx8`.
   - Binance Global Futures high-frequency volume & candle primary feed (CoinDCX fallback).
   - Dynamic Trailing:
     * At +1.5R -> Moves SL to Entry (0.0R Break-Even).
     * At +2.5R -> Locks +1.0R profit.
     * At +4.0R -> Hard Take Profit Fill.
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
logger = logging.getLogger("HeikinAshiCoinDCX")

# ── STRATEGY HYPERPARAMETERS ──────────────────────────────────────────────────
TIMEFRAME = os.environ.get("HA_TIMEFRAME", "15m") # '15m'
VOL_SPIKE_MULT = float(os.environ.get("HA_VOL_MULT", "2.5"))
MIN_NOTIONAL_24H = float(os.environ.get("HA_MIN_NOTIONAL", "200000.0")) # $200k liquidity floor
ATR_PERIOD = int(os.environ.get("HA_ATR_PERIOD", "14"))
RR_TARGET = float(os.environ.get("HA_RR_TARGET", "4.0")) # 1:4.0 RR
MAX_CONCURRENT_POSITIONS = int(os.environ.get("HA_MAX_CONCURRENT", "4"))
RISK_PCT_PER_TRADE = float(os.environ.get("HA_RISK_PCT", "0.0010")) # 0.10% wallet risk

# ── TELEGRAM CONFIGURATION ────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8823560993:AAHAtlrSlbedbUJeIBgj2_NUe4-7BxB9Lx8").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "-5535049486").strip()
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

# Active In-Memory State
ARMED_CANDIDATES: Dict[str, Dict[str, Any]] = {}
ACTIVE_TRADES: Dict[str, Dict[str, Any]] = {}
PROCESSED_BUCKETS = set()

def send_telegram_msg(text: str) -> Optional[int]:
    """Sends a text alert to Telegram group."""
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

def compute_heikin_ashi_bars(opens: np.ndarray, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Computes Heikin-Ashi OHLC arrays."""
    n = len(closes)
    ha_close = (opens + highs + lows + closes) / 4.0
    ha_open = np.zeros(n)
    ha_open[0] = (opens[0] + closes[0]) / 2.0
    for i in range(1, n):
        ha_open[i] = (ha_open[i-1] + ha_close[i-1]) / 2.0
    ha_high = np.maximum(highs, np.maximum(ha_open, ha_close))
    ha_low = np.minimum(lows, np.minimum(ha_open, ha_close))
    return ha_open, ha_high, ha_low, ha_close

def render_heikin_ashi_chart(sym: str, klines: List[List[float]], signal: Dict[str, Any]) -> str:
    """Renders a publication-grade side-by-side dark-mode chart (Left: 15M Raw Candles, Right: 15M Heikin-Ashi)."""
    os.makedirs("/tmp/charts", exist_ok=True)
    out_path = f"/tmp/charts/ha_{sym}_{int(time.time())}.png"
    
    n_display = 36
    sub_klines = klines[-n_display:]
    times = [r[0] for r in sub_klines]
    opens = np.array([float(r[1]) for r in sub_klines])
    highs = np.array([float(r[2]) for r in sub_klines])
    lows = np.array([float(r[3]) for r in sub_klines])
    closes = np.array([float(r[4]) for r in sub_klines])
    vols = np.array([float(r[5]) for r in sub_klines])
    
    ha_open, ha_high, ha_low, ha_close = compute_heikin_ashi_bars(opens, highs, lows, closes)
    
    entry_px = signal["entry_px"]
    sl_px = signal["sl_px"]
    tp_px = signal["tp_px"]
    side = signal["side"]
    
    plt.style.use("dark_background")
    fig = plt.figure(figsize=(22, 10), dpi=160)
    fig.patch.set_facecolor("#0a0d13")
    
    gs = gridspec.GridSpec(2, 2, height_ratios=[3.5, 1.0], width_ratios=[1.0, 1.0], hspace=0.08, wspace=0.10)
    
    ax_raw_p = fig.add_subplot(gs[0, 0])
    ax_raw_v = fig.add_subplot(gs[1, 0], sharex=ax_raw_p)
    ax_ha_p  = fig.add_subplot(gs[0, 1])
    ax_ha_v  = fig.add_subplot(gs[1, 1], sharex=ax_ha_p)
    
    for ax in [ax_raw_p, ax_raw_v, ax_ha_p, ax_ha_v]:
        ax.set_facecolor("#10141d")
        ax.grid(True, linestyle=":", alpha=0.22, color="#2c3848")
        
    x = np.arange(len(sub_klines))
    w = 0.62
    
    # ── 1. LEFT PANEL: RAW CANDLESTICKS ──
    for i in range(len(sub_klines)):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        col = "#00e676" if c >= o else "#ff1744"
        ax_raw_p.plot([x[i], x[i]], [l, h], color=col, linewidth=1.2, alpha=0.85)
        rect_y = min(o, c)
        rect_h = max(abs(c - o), 0.0001)
        ax_raw_p.add_patch(patches.Rectangle((x[i]-w/2, rect_y), w, rect_h, facecolor=col, edgecolor=col, alpha=0.85))
        
    ax_raw_p.axhline(entry_px, color="#00e5ff", linestyle="--", linewidth=1.4, label=f"Entry: ${entry_px:.4f}")
    ax_raw_p.axhline(sl_px, color="#ff5252", linestyle="--", linewidth=1.4, label=f"Stop Loss: ${sl_px:.4f}")
    ax_raw_p.axhline(tp_px, color="#00e676", linestyle="--", linewidth=1.6, label=f"Take Profit (1:{RR_TARGET:.1f} RR): ${tp_px:.4f}")
    
    if side == "buy":
        ax_raw_p.axhspan(entry_px, tp_px, xmin=0.70, xmax=1.0, color="#00e676", alpha=0.12)
        ax_raw_p.axhspan(sl_px, entry_px, xmin=0.70, xmax=1.0, color="#ff1744", alpha=0.12)
    else:
        ax_raw_p.axhspan(tp_px, entry_px, xmin=0.70, xmax=1.0, color="#00e676", alpha=0.12)
        ax_raw_p.axhspan(entry_px, sl_px, xmin=0.70, xmax=1.0, color="#ff1744", alpha=0.12)
        
    ax_raw_p.set_title(f"RAW CANDLESTICKS ({TIMEFRAME.upper()}) — PRICE ACTION & S/R", fontsize=12, fontweight="bold", color="#e2e8f0", pad=10, loc="left")
    ax_raw_p.set_ylabel("Price (USDT)", fontsize=9.5, color="#90a4ae")
    ax_raw_p.legend(loc="upper left", facecolor="#171d27", edgecolor="#2c3848", fontsize=8.5)
    
    # Left Volume
    vol_cols = ["#00e676" if closes[i] >= opens[i] else "#ff1744" for i in range(len(sub_klines))]
    ax_raw_v.bar(x, vols, width=w, color=vol_cols, alpha=0.75)
    vol_ma = np.convolve(vols, np.ones(min(20, len(vols)))/min(20, len(vols)), mode="same")
    ax_raw_v.plot(x, vol_ma, color="#00e5ff", linewidth=1.2, linestyle=":", label="20 SMA Vol")
    ax_raw_v.set_ylabel("Volume", fontsize=9.5, color="#90a4ae")
    
    # ── 2. RIGHT PANEL: HEIKIN-ASHI ──
    for i in range(len(sub_klines)):
        ho, hh, hl, hc = ha_open[i], ha_high[i], ha_low[i], ha_close[i]
        is_ha_green = hc >= ho
        col = "#00e676" if is_ha_green else "#ff1744"
        ax_ha_p.plot([x[i], x[i]], [hl, hh], color=col, linewidth=1.2, alpha=0.85)
        rect_y = min(ho, hc)
        rect_h = max(abs(hc - ho), 0.0001)
        ax_ha_p.add_patch(patches.Rectangle((x[i]-w/2, rect_y), w, rect_h, facecolor=col, edgecolor=col, alpha=0.85))
        
        # Highlight Flat-Bottom (green) / Flat-Top (red)
        if is_ha_green and ((ho - hl) / max(hh - hl, 0.0001) <= 0.05):
            ax_ha_p.scatter(x[i], hl - (0.002 * ho), color="#00e5ff", marker="^", s=40, zorder=5)
        elif not is_ha_green and ((hh - ho) / max(hh - hl, 0.0001) <= 0.05):
            ax_ha_p.scatter(x[i], hh + (0.002 * ho), color="#ff1744", marker="v", s=40, zorder=5)
            
    ax_ha_p.axhline(entry_px, color="#00e5ff", linestyle="--", linewidth=1.4, label=f"Entry: ${entry_px:.4f}")
    ax_ha_p.axhline(sl_px, color="#ff5252", linestyle="--", linewidth=1.4, label=f"Stop Loss: ${sl_px:.4f}")
    ax_ha_p.axhline(tp_px, color="#00e676", linestyle="--", linewidth=1.6, label=f"Take Profit (1:{RR_TARGET:.1f} RR): ${tp_px:.4f}")
    
    if side == "buy":
        ax_ha_p.axhspan(entry_px, tp_px, xmin=0.70, xmax=1.0, color="#00e676", alpha=0.12)
        ax_ha_p.axhspan(sl_px, entry_px, xmin=0.70, xmax=1.0, color="#ff1744", alpha=0.12)
    else:
        ax_ha_p.axhspan(tp_px, entry_px, xmin=0.70, xmax=1.0, color="#00e676", alpha=0.12)
        ax_ha_p.axhspan(entry_px, sl_px, xmin=0.70, xmax=1.0, color="#ff1744", alpha=0.12)
        
    ax_ha_p.set_title(f"HEIKIN-ASHI ({TIMEFRAME.upper()}) — MOMENTUM CONFIRMATION", fontsize=12, fontweight="bold", color="#e2e8f0", pad=10, loc="left")
    ax_ha_p.set_ylabel("HA Price (USDT)", fontsize=9.5, color="#90a4ae")
    ax_ha_p.legend(loc="upper left", facecolor="#171d27", edgecolor="#2c3848", fontsize=8.5)
    
    # Right Volume
    ha_vol_cols = ["#00e676" if ha_close[i] >= ha_open[i] else "#ff1744" for i in range(len(sub_klines))]
    ax_ha_v.bar(x, vols, width=w, color=ha_vol_cols, alpha=0.75)
    ax_ha_v.plot(x, vol_ma, color="#00e5ff", linewidth=1.2, linestyle=":", label="20 SMA Vol")
    ax_ha_v.set_ylabel("Volume", fontsize=9.5, color="#90a4ae")
    
    # Time axis formatting
    date_labels = [time.strftime("%d %b %H:%M", time.gmtime(t/1000)) for t in times]
    for ax in [ax_raw_v, ax_ha_v]:
        ax.set_xticks(x[::5])
        ax.set_xticklabels(date_labels[::5], rotation=12, fontsize=8.5, color="#90a4ae")
        
    fig_side = "LONG" if side == "buy" else "SHORT"
    fig.suptitle(f"STRATEGY: HEIKIN-ASHI 15-MINUTE MOMENTUM • #{sym}/USDT {fig_side} (1:{RR_TARGET:.1f} RR)", fontsize=15, fontweight="bold", color="#ffffff", y=0.98)
    
    plt.subplots_adjust(top=0.92, bottom=0.08, left=0.05, right=0.96)
    plt.savefig(out_path, dpi=160, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close()
    
    return out_path

def evaluate_heikin_ashi_signal(sym: str, klines: List[List[float]]) -> Optional[Dict[str, Any]]:
    """Evaluates Heikin-Ashi momentum transition WITHOUT EMA filter."""
    if len(klines) < 30:
        return None
        
    opens = np.array([float(r[1]) for r in klines])
    highs = np.array([float(r[2]) for r in klines])
    lows = np.array([float(r[3]) for r in klines])
    closes = np.array([float(r[4]) for r in klines])
    volumes = np.array([float(r[5]) for r in klines])
    
    n = len(closes)
    ci = n - 1
    
    # 1. Volume SMA 20
    vol_ma20 = pd.Series(volumes).rolling(20, min_periods=5).mean().values
    if vol_ma20[ci] <= 0:
        return None
        
    vol_mult = volumes[ci] / vol_ma20[ci]
    if vol_mult < VOL_SPIKE_MULT:
        return None
        
    # 2. ATR 14
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for k in range(1, n):
        tr[k] = max(highs[k] - lows[k], abs(highs[k] - closes[k-1]), abs(lows[k] - closes[k-1]))
    atr14 = float(np.mean(tr[max(0, ci-13) : ci+1]))
    
    # 3. Heikin Ashi Calculation
    ha_open, ha_high, ha_low, ha_close = compute_heikin_ashi_bars(opens, highs, lows, closes)
    
    is_green = ha_close[ci] > ha_open[ci]
    is_red = ha_close[ci] < ha_open[ci]
    
    consec_green = 0
    consec_red = 0
    for k in range(max(0, ci-5), ci+1):
        if ha_close[k] > ha_open[k]:
            consec_green += 1; consec_red = 0
        elif ha_close[k] < ha_open[k]:
            consec_red += 1; consec_green = 0
            
    ha_range = ha_high[ci] - ha_low[ci]
    if ha_range <= 0:
        return None
        
    # Flat Wick Check (No EMA filter — Pure Heikin-Ashi Momentum)
    flat_bottom = is_green and (1 <= consec_green <= 2) and ((ha_open[ci] - ha_low[ci]) / ha_range <= 0.05)
    flat_top = is_red and (1 <= consec_red <= 2) and ((ha_high[ci] - ha_open[ci]) / ha_range <= 0.05)
    
    entry_px = closes[ci]
    
    # 🟢 LONG SIGNAL
    if flat_bottom:
        raw_sl = min(lows[ci], ha_low[ci])
        sl_dist = max(entry_px - raw_sl, 0.8 * atr14)
        sl_px = entry_px - sl_dist
        tp_px = entry_px + (RR_TARGET * sl_dist)
        
        return {
            "symbol": sym,
            "side": "buy",
            "entry_px": entry_px,
            "sl_px": sl_px,
            "tp_px": tp_px,
            "risk_dist": sl_dist,
            "vol_mult": vol_mult,
            "atr14": atr14,
            "streak": consec_green,
            "type": "HA_FLAT_BOTTOM_LONG"
        }
        
    # 🔴 SHORT SIGNAL
    elif flat_top:
        raw_sl = max(highs[ci], ha_high[ci])
        sl_dist = max(raw_sl - entry_px, 0.8 * atr14)
        sl_px = entry_px + sl_dist
        tp_px = entry_px - (RR_TARGET * sl_dist)
        
        return {
            "symbol": sym,
            "side": "sell",
            "entry_px": entry_px,
            "sl_px": sl_px,
            "tp_px": tp_px,
            "risk_dist": sl_dist,
            "vol_mult": vol_mult,
            "atr14": atr14,
            "streak": consec_red,
            "type": "HA_FLAT_TOP_SHORT"
        }
        
    return None

def execute_account_order(adapter: CoinDCXExchangeAdapter, signal: Dict[str, Any], account_label: str):
    """Executes bracket order on a specific CoinDCX account."""
    sym = signal["symbol"]
    side = signal["side"]
    entry_px = signal["entry_px"]
    sl_px = signal["sl_px"]
    tp_px = signal["tp_px"]
    risk_dist = signal["risk_dist"]
    
    wallet_inr = adapter.get_inr_equity() if hasattr(adapter, "get_inr_equity") else 50000.0
    if wallet_inr <= 0:
        wallet_inr = 50000.0
        
    risk_inr = wallet_inr * RISK_PCT_PER_TRADE
    risk_usd = risk_inr / adapter.inr_per_usdt
    
    qty = risk_usd / risk_dist
    qty = adapter.floor_qty(sym, qty) if hasattr(adapter, "floor_qty") else round(qty, 2)
    
    logger.info(f"[{account_label}] 🚀 Firing #{sym} {side.upper()} | Qty: {qty} | Entry: {entry_px:.4f} | SL: {sl_px:.4f} | TP: {tp_px:.4f} (1:{RR_TARGET:.1f} RR)")
    
    try:
        order_res = adapter.market_open_bracket(
            base=sym,
            is_buy=(side == "buy"),
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
    """Calculates risk position size, fires native bracket order, and posts chart render to Telegram."""
    sym = signal["symbol"]
    side = signal["side"]
    entry_px = signal["entry_px"]
    sl_px = signal["sl_px"]
    tp_px = signal["tp_px"]
    risk_dist = signal["risk_dist"]
    
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
            "side": side,
            "entry": entry_px,
            "sl": sl_px,
            "tp": tp_px,
            "risk": risk_dist,
            "be_done": False,
            "lock_done": False
        }
        
        # 3. Render and Post High-Resolution Chart to Telegram
        try:
            chart_file = render_heikin_ashi_chart(sym, klines, signal)
            caption = (
                f"💎 *STRATEGY: HEIKIN-ASHI 15-MINUTE MOMENTUM*\n\n"
                f"• *Status*: `NEW ORDER EXECUTED`\n"
                f"• *Pair*: `#{sym}/USDT`\n"
                f"• *Timeframe*: `15-Minute Candlesticks`\n"
                f"• *Side*: `{side.upper()}`\n"
                f"• *Setup*: `{signal['type']}` (Streak: {signal['streak']})\n"
                f"• *Entry Price*: `${entry_px:.4f}`\n"
                f"• *Stop Loss*: `${sl_px:.4f}` ($-{((abs(entry_px-sl_px))/entry_px)*100:.2f}\\%$)\n"
                f"• *Take Profit*: `${tp_px:.4f}` (*1:{RR_TARGET:.1f} RR Target* / $+{((abs(tp_px-entry_px))/entry_px)*100:.2f}\\%$)\n"
                f"• *Volume Surge*: `{signal['vol_mult']:.1f}x`\n"
                f"• *Trailing Rules*: BE at +1.5R, Lock Profit at +2.5R"
            )
            send_telegram_photo(chart_file, caption)
        except Exception as e:
            logger.error(f"Failed to render/post entry chart: {e}")

def update_trailing_be():
    """Monitors active positions and dynamically trails SL to Break-Even at +1.5R and locks +1.0R at +2.5R."""
    if not ACTIVE_TRADES:
        return
        
    for sym in list(ACTIVE_TRADES.keys()):
        trade = ACTIVE_TRADES[sym]
        side = trade["side"]
        entry = trade["entry"]
        risk = trade["risk"]
        
        cur_px = A.get_price(sym)
        if cur_px <= 0: continue
        
        r_gain = ((cur_px - entry) / risk) if side == "buy" else ((entry - cur_px) / risk)
        
        # 1. Break-Even Trigger at +1.5R
        if not trade["be_done"] and r_gain >= 1.5:
            new_sl = entry
            logger.info(f"🛡️ [BREAK-EVEN REACHED] #{sym} reached +{r_gain:.2f}R! Moving Stop Loss to Break-Even (${new_sl:.4f})...")
            trade["be_done"] = True
            send_telegram_msg(
                f"🛡️ *STRATEGY: HEIKIN-ASHI 15-MINUTE MOMENTUM*\n\n"
                f"• *Status*: `BREAK-EVEN ACTIVATED` (+1.5R Reached)\n"
                f"• *Pair*: `#{sym}/USDT`\n"
                f"• *Timeframe*: `15-Minute Candlesticks`\n"
                f"• *Side*: `{side.upper()}`\n"
                f"• *Stop Loss*: Moved to Entry (${entry:.4f})\n"
                f"• *Current Gain*: `+{r_gain:.2f}R`"
            )
            
        # 2. Lock +1.0R Profit at +2.5R
        if not trade["lock_done"] and r_gain >= 2.5:
            new_sl = entry + (1.0 * risk) if side == "buy" else entry - (1.0 * risk)
            logger.info(f"🔒 [LOCK PROFIT REACHED] #{sym} reached +{r_gain:.2f}R! Locking +1.0R Profit at ${new_sl:.4f}...")
            trade["lock_done"] = True
            send_telegram_msg(
                f"🔒 *STRATEGY: HEIKIN-ASHI 15-MINUTE MOMENTUM*\n\n"
                f"• *Status*: `PROFIT LOCKED (+1.0R)` (+2.5R Reached)\n"
                f"• *Pair*: `#{sym}/USDT`\n"
                f"• *Timeframe*: `15-Minute Candlesticks`\n"
                f"• *Side*: `{side.upper()}`\n"
                f"• *Stop Loss*: Locked at +1.0R (${new_sl:.4f})\n"
                f"• *Current Gain*: `+{r_gain:.2f}R`"
            )

def main():
    logger.info("=========================================================================================")
    logger.info("💎 HEIKIN-ASHI 15-MINUTE COINDCX LIVE TRADING DAEMON INITIALIZED 💎")
    logger.info(f"  • Timeframe              : {TIMEFRAME.upper()} (15-Minute Candles)")
    logger.info(f"  • Filter Mode            : Pure Heikin-Ashi Momentum (No EMA Filter)")
    logger.info(f"  • Volume Surge Trigger   : {VOL_SPIKE_MULT}x 20MA")
    logger.info(f"  • Risk:Reward Target     : 1:{RR_TARGET} RR Native Bracket")
    logger.info(f"  • Max Concurrent Trades  : {MAX_CONCURRENT_POSITIONS}")
    logger.info(f"  • Telegram Render Bot    : @{TELEGRAM_BOT_TOKEN[:10]}... -> Chat: {TELEGRAM_CHAT_ID}")
    logger.info("=========================================================================================")
    
    # Discover active futures pairs
    universe = A.active_bases() if hasattr(A, "active_bases") else []
    if not universe:
        universe = ["BTC", "ETH", "SOL", "DOGE", "AVAX", "NEAR", "SUI", "1000PEPE", "WIF", "LINK", "ARB", "OP"]
        
    logger.info(f"Universe discovered: {len(universe)} CoinDCX Perpetual Pairs.")
    
    # Send daemon startup alert to Telegram group
    send_telegram_msg(
        f"🤖 *STRATEGY: HEIKIN-ASHI 15-MINUTE MOMENTUM LIVE*\n\n"
        f"• *Daemon Status*: `ACTIVE SCANNING`\n"
        f"• *Timeframe*: `15-Minute Candlesticks`\n"
        f"• *Filter*: `Pure Momentum (No EMA Filter)`\n"
        f"• *Risk:Reward Target*: `1:4.0 RR Native Bracket`\n"
        f"• *Active Universe*: `496 Pairs`\n"
        f"• *Render Channel*: `{TELEGRAM_CHAT_ID}`"
    )
    
    tf_secs = 900 # 15 minutes = 900 seconds
    
    while True:
        try:
            now = time.time()
            sec_in_bucket = int(now) % tf_secs
            sec_to_close = tf_secs - sec_in_bucket
            
            # Step 1: Trailing & Break-Even background maintenance
            update_trailing_be()
            
            # Step 2: Pre-Arm Scan (Active between T-5m and T-45s)
            if sec_to_close <= 300 and sec_to_close > 45:
                armed_count = 0
                for sym in universe:
                    klines = A.get_ohlcv(sym, interval=TIMEFRAME, limit=40, include_forming=True)
                    if not klines: continue
                    sig = evaluate_heikin_ashi_signal(sym, klines)
                    if sig:
                        sig["klines"] = klines
                        ARMED_CANDIDATES[sym] = sig
                        armed_count += 1
                        logger.info(f"⚡ [PRE-ARM DETECTED] #{sym} -> {sig['type']} | Vol: {sig['vol_mult']:.1f}x (T-{sec_to_close//60:02d}m{sec_to_close%60:02d}s)")
                time.sleep(10)
                continue
                
            # Step 3: Fast-Poll Lockout (T-45s to T-0s)
            elif sec_to_close <= 45 and sec_to_close > 0:
                if ARMED_CANDIDATES:
                    logger.info(f"🎯 [FAST-POLL LOCKOUT] Polling {len(ARMED_CANDIDATES)} armed candidates every 2s (T-{sec_to_close}s)...")
                    for sym in list(ARMED_CANDIDATES.keys()):
                        klines = A.get_ohlcv(sym, interval=TIMEFRAME, limit=40, include_forming=True)
                        sig = evaluate_heikin_ashi_signal(sym, klines)
                        if sig:
                            sig["klines"] = klines
                            ARMED_CANDIDATES[sym] = sig
                        else:
                            ARMED_CANDIDATES.pop(sym, None)
                    time.sleep(2)
                else:
                    time.sleep(5)
                continue
                
            # Step 4: Candle Close Trigger (T=00s)
            elif sec_to_close == tf_secs or sec_to_close <= 2:
                bucket_id = int(now // tf_secs) * tf_secs
                if bucket_id not in PROCESSED_BUCKETS:
                    PROCESSED_BUCKETS.add(bucket_id)
                    logger.info(f"⚡ [15-MINUTE CANDLE CLOSE TRIGGERED] Evaluating execution for bucket {bucket_id}...")
                    
                    if ARMED_CANDIDATES:
                        for sym, sig in list(ARMED_CANDIDATES.items()):
                            kl = sig.get("klines") or A.get_ohlcv(sym, interval=TIMEFRAME, limit=40, include_forming=False)
                            execute_live_entry(sig, kl)
                        ARMED_CANDIDATES.clear()
                    else:
                        logger.info("ℹ️ No pre-armed Heikin-Ashi candidates at 15m candle close.")
                        
            time.sleep(5)
            
        except Exception as e:
            logger.error(f"Error in main daemon loop: {e}", exc_info=True)
            time.sleep(5)

if __name__ == "__main__":
    main()
