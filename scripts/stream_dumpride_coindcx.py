#!/usr/bin/env python3
"""stream_dumpride_coindcx.py — 4H "DumpRide" Exhaustion Short Live Strategy Engine for CoinDCX.

Strategy Rules:
1. Setup: 4H candle closes GREEN (Close >= Open) with Volume >= 10.0x SMA(40) baseline volume AND pump >= 5.0%.
2. Peak Tracking: Continuously tracks the highest price reached during the pump move.
3. Invalidation: If price drops below the pump start level before triggering, watch loop stops.
4. Exhaustion Entry Trigger:
   - When a 4H candle closes RED (Close < Open) and closes BELOW the 9 EMA:
   - Enters SHORT via Market/Limit Order.
5. Invalidation / Stop Loss: Placed at the exact Pump Peak High (structural ceiling).
6. Target / Exit: 70% Retracement Target (Peak - 0.70 * (Peak - Pump_Start)).
7. Multi-Account Execution: Executes on Account 1 and optional Account 2.
8. Position Sizing: 1% wallet balance risk per trade (RISK_FRAC = 0.01).
"""
import os
import sys
import time
import math
import json
import datetime
from concurrent.futures import ThreadPoolExecutor
import numpy as np

# ── Environment & Config ──────────────────────────────────────────────────────
TF = os.environ.get("DUMPRIDE_TF", "4h")
TF_SEC = {"15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800, "12h": 43200}.get(TF, 14400)
TF_MS = TF_SEC * 1000

SPIKE_VOL_MULT = float(os.environ.get("DUMPRIDE_SPIKE_VOL", "10.0"))
PUMP_MIN_PCT = float(os.environ.get("DUMPRIDE_PUMP_MIN_PCT", "5.0"))
EMA_PERIOD = int(os.environ.get("DUMPRIDE_EMA_PERIOD", "9"))
RETRACE_TARGET_FIB = float(os.environ.get("DUMPRIDE_RETRACE_FIB", "0.70"))

RISK_FRAC = float(os.environ.get("DUMPRIDE_RISK_FRAC", "0.01"))  # 1% risk per trade
LEVERAGE = int(os.environ.get("DUMPRIDE_LEVERAGE", "10"))
MAX_CONCURRENT = int(os.environ.get("DUMPRIDE_MAX_CONCURRENT", "5"))
POLL_INTERVAL = float(os.environ.get("DUMPRIDE_POLL", "1.0"))

ARMED = os.environ.get("LIVE_ARMED", "0") == "1"
START_BAL_INR = float(os.environ.get("DUMPRIDE_START_BAL_INR", "16000"))
OUT_DIR = os.environ.get("DUMPRIDE_OUT", "/root/dumpride_coindcx")
STATE_FILE = os.path.join(OUT_DIR, "state.json")
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

# ── CoinDCX Adapter Setup ─────────────────────────────────────────────────────
sys.path.append("/root/trading-bot/crypto")
sys.path.append("/root/trading-bot/crypto/shared_scripts")

from dotenv import load_dotenv
load_dotenv("/root/go-trader/.env")
from platforms.coindcx.adapter import CoinDCXExchangeAdapter

PRIMARY_KEY = os.environ.get("COINDCX_LIVE_API_KEY")
PRIMARY_SECRET = os.environ.get("COINDCX_LIVE_API_SECRET")
A = CoinDCXExchangeAdapter(key=PRIMARY_KEY, secret=PRIMARY_SECRET)

SEC_KEY = os.environ.get("COINDCX_KEY_2")
SEC_SECRET = os.environ.get("COINDCX_SECRET_2")
A2 = CoinDCXExchangeAdapter(key=SEC_KEY, secret=SEC_SECRET) if (SEC_KEY and SEC_SECRET) else None
if A2:
    log(f"[MultiAccount] Secondary CoinDCX Account Enabled (Key ending in ...{SEC_KEY[-6:]})")

# ── Telegram Notifications ────────────────────────────────────────────────────
try:
    from b2_telegram import send_telegram_alert, send_telegram_chart
    TELEGRAM_ENABLED = True
except Exception:
    TELEGRAM_ENABLED = False
    def send_telegram_alert(msg): log(f"TELEGRAM: {msg}")
    def send_telegram_chart(buf, cap): log(f"TELEGRAM CHART: {cap}")

# ── State Persistence ─────────────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "watching": {},
        "positions": {},
        "closed_trades": [],
        "total_pnl_inr": 0.0,
        "wallet_bal_inr": START_BAL_INR
    }

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log(f"Error saving state: {e}")

# ── Market Data ───────────────────────────────────────────────────────────────
def fetch_coin_klines(base):
    try:
        rows = A.get_ohlcv(base, interval=TF, limit=100)
        if not isinstance(rows, list) or len(rows) < 42:
            return None
        opens = np.array([float(r[1]) for r in rows])
        highs = np.array([float(r[2]) for r in rows])
        lows = np.array([float(r[3]) for r in rows])
        closes = np.array([float(r[4]) for r in rows])
        vols = np.array([float(r[5]) for r in rows])
        times = [int(r[0]) for r in rows]
        return (opens, highs, lows, closes, vols, times)
    except Exception:
        return None

def fetch_all_klines(coins):
    from concurrent.futures import as_completed
    data = {}
    with ThreadPoolExecutor(max_workers=50) as ex:
        futures = {ex.submit(fetch_coin_klines, coin): coin for coin in coins}
        for fut in as_completed(futures):
            coin = futures[fut]
            try:
                res = fut.result()
                if res:
                    data[coin] = res
            except Exception:
                pass
    return data

# ── Strategy Evaluation ───────────────────────────────────────────────────────
def evaluate_dumpride_signal(base, klines_tuple, now_ms, state):
    opens, highs, lows, closes, vols, times = klines_tuple
    
    # Identify closed bar index
    ci = -1
    for idx in range(len(times) - 1, -1, -1):
        if times[idx] + TF_MS <= now_ms + 1000:
            ci = idx
            break
            
    if ci < 40:
        return None, "INSUFFICIENT_DATA"
        
    cur_t = times[ci]
    cur_o, cur_h, cur_l, cur_c, cur_v = opens[ci], highs[ci], lows[ci], closes[ci], vols[ci]
    is_red = cur_c < cur_o
    is_green = cur_c >= cur_o
    
    # 9 EMA calculation
    k_ema = 2.0 / (EMA_PERIOD + 1)
    ema_vals = np.zeros(ci + 1)
    ema_vals[0] = closes[0]
    for idx in range(1, ci + 1):
        ema_vals[idx] = closes[idx] * k_ema + ema_vals[idx - 1] * (1.0 - k_ema)
    cur_ema = ema_vals[ci]
    
    # 14 ATR calculation
    tr = np.zeros(ci + 1)
    tr[0] = highs[0] - lows[0]
    for idx in range(1, ci + 1):
        tr[idx] = max(highs[idx] - lows[idx], abs(highs[idx] - closes[idx - 1]), abs(lows[idx] - closes[idx - 1]))
    cur_atr = float(np.mean(tr[max(0, ci - 13) : ci + 1]))
    
    # Check if coin is already being watched for exhaustion
    watch = state.get("watching", {}).get(base)
    
    if watch:
        # Update peak high reached during pump
        if cur_h > watch.get("pump_peak_px", 0):
            watch["pump_peak_px"] = cur_h
            
        # Invalidation check: price dumped below pump start before trigger
        if cur_c < watch.get("pump_start_px", 0):
            log(f"[{base}] 🛑 Price broke below pump start ({cur_c:.6f} < {watch['pump_start_px']:.6f}). Setup invalidated.")
            return None, "INVALIDATED_BELOW_ORIGIN"
            
        # Exhaustion Entry Trigger: Red candle close below 9 EMA
        if is_red and cur_c < cur_ema:
            entry_px = cur_c
            sl_px = watch["pump_peak_px"]
            risk = sl_px - entry_px
            pump_start = watch["pump_start_px"]
            
            # Risk spread check (>= 0.5% to avoid micro-spread traps)
            if (risk / entry_px) < 0.005:
                log(f"[{base}] ⚠️ Risk spread too tight ({(risk/entry_px)*100:.2f}% < 0.5%). Skipping trade.")
                return None, "RISK_TOO_TIGHT"
                
            # 70% Retracement Target
            tp_px = sl_px - RETRACE_TARGET_FIB * (sl_px - pump_start)
            if tp_px >= entry_px * 0.998:
                tp_px = entry_px - 2.0 * risk
                
            return {
                "symbol": base,
                "action": "ENTER_SHORT",
                "entry_px": entry_px,
                "sl_px": sl_px,
                "tp_px": tp_px,
                "risk": risk,
                "atr": cur_atr,
                "pump_peak_px": watch["pump_peak_px"],
                "pump_start_px": pump_start,
                "spike_mult": watch["spike_mult"]
            }, "EXHAUSTION_ENTRY_CONFIRMED"
            
        return None, "WAITING_FOR_EXHAUSTION"
        
    # Baseline volume over 40 periods
    base_vol = np.mean(vols[ci - 40 : ci])
    if base_vol <= 0:
        return None, "NO_BASELINE_VOLUME"
    vol_mult = cur_v / base_vol
    
    # Check for new institutional volume surge
    if is_green and vol_mult >= SPIKE_VOL_MULT:
        pump_pct = (cur_c - cur_o) / cur_o * 100 if cur_o > 0 else 0
        if pump_pct >= PUMP_MIN_PCT:
            return {
                "symbol": base,
                "action": "START_WATCH",
                "spike_t": cur_t,
                "spike_mult": vol_mult,
                "pump_start_px": cur_l,
                "pump_peak_px": cur_h,
                "pump_pct": pump_pct,
                "atr": cur_atr
            }, "PUMP_SPIKE_DETECTED"
            
    return None, "NO_SIGNAL"

def main():
    log("===================================================================")
    log("⚡ COINDCX 4H \"DUMPRIDE\" EXHAUSTION SHORT STRATEGY STARTED ⚡")
    log(f"   Timeframe: {TF} | Volume Spike: >={SPIKE_VOL_MULT}x | Pump Min: {PUMP_MIN_PCT}%")
    log(f"   Target Model: {RETRACE_TARGET_FIB*100:.0f}% Retracement | Risk per Trade: {RISK_FRAC*100:.1f}%")
    log(f"   Mode: {'🔴 LIVE ARMED TRADING' if ARMED else '🟡 PAPER MODE / MONITORING'}")
    log("===================================================================")

if __name__ == "__main__":
    main()
