#!/usr/bin/env python3
"""stream_fibvol_coindcx.py — 30X Volume Spike Fibonacci (FIBVOL) Live Strategy Engine for CoinDCX.

Strategy Rules:
1. Spike Trigger: 15m candle closes GREEN (Close >= Open) with Volume >= 30.0x SMA(40) baseline volume.
2. Fibonacci Levels:
   - Entry: 0.6 Fib Retracement (High - 0.6 * (High - Low))
   - Stop Loss: 0.8 Fib Retracement (High - 0.8 * (High - Low)), configurable via FIBVOL_SL_FIB
   - Take Profit: 1:4 Risk-Reward (Entry + 4.0 * (Entry - SL))
3. Limit Order Loop:
   - Next candle retraces to Entry -> Entry Filled, enter Long.
   - Next candle closes GREEN without filling -> Re-plot Fibonacci levels on new green candle, update limit order.
   - Next candle closes RED without filling -> Stop watch loop for this coin.
4. Position Sizing: 1% wallet balance risk per trade (RISK_FRAC = 0.01).
5. Chart Renders: Entry and Exit candlestick charts posted directly to Telegram via b2_telegram module.
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
TF = os.environ.get("FIBVOL_TF", "15m")
TF_SEC = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600}.get(TF, 900)
TF_MS = TF_SEC * 1000

SPIKE_VOL_MULT = float(os.environ.get("FIBVOL_SPIKE_VOL", "30.0"))
ENTRY_FIB_LEVEL = float(os.environ.get("FIBVOL_ENTRY_FIB", "0.700"))
SL_FIB_LEVEL = float(os.environ.get("FIBVOL_SL_FIB", "0.800"))
RR_RATIO = float(os.environ.get("FIBVOL_RR_RATIO", "5.0"))

TRAIL_ENABLE = os.environ.get("FIBVOL_TRAIL_ENABLE", "1") == "1"
TRAIL_ACT_R = float(os.environ.get("FIBVOL_TRAIL_ACT_R", "2.0"))  # Activate trailing SL at +2.0R profit
TRAIL_DIST_R = float(os.environ.get("FIBVOL_TRAIL_DIST_R", "1.0")) # Trail 1.0R behind peak high

RISK_FRAC = float(os.environ.get("FIBVOL_RISK_FRAC", "0.01"))  # 1% risk per trade
LEVERAGE = int(os.environ.get("FIBVOL_LEVERAGE", "10"))
MAX_CONCURRENT = int(os.environ.get("FIBVOL_MAX_CONCURRENT", "5"))
POLL_INTERVAL = float(os.environ.get("FIBVOL_POLL", "0.1"))  # 100ms ultra-fast 10Hz position & trailing SL loop

ARMED = os.environ.get("LIVE_ARMED", "0") == "1"
START_BAL_INR = float(os.environ.get("FIBVOL_START_BAL_INR", "16000"))
OUT_DIR = os.environ.get("FIBVOL_OUT", "/root/fibvol_coindcx")
STATE_FILE = os.path.join(OUT_DIR, "state.json")
LOG_FILE = os.path.join(OUT_DIR, "run.log")
TRADES_FILE = os.path.join(OUT_DIR, "trades.csv")
ENV_FILE = os.environ.get("FIBVOL_ENV_FILE", "/root/go-trader/.env")
ACCOUNT_NAME = "CoinDCX FIBVOL Strategy"

# Telegram integration
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8683548180:AAFwxp682aMZHh-_BHZksBUfEhEoEfvTeyk")
TELEGRAM_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "-5370765433")

try:
    sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
    import b2_telegram as TG
except Exception:
    TG = None

# Load env variables from file if present
def load_env():
    try:
        if os.path.exists(ENV_FILE):
            for ln in open(ENV_FILE):
                ln = ln.strip()
                if ln and "=" in ln and not ln.startswith("#"):
                    k, v = ln.split("=", 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass

load_env()

# Import CoinDCX Exchange Adapter
try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(script_dir, "../platforms/coindcx"))
    sys.path.insert(0, "/root/ai-prosperity/platforms/coindcx")
    sys.path.insert(0, "/root/go-trader/platforms/coindcx")
    from adapter import CoinDCXExchangeAdapter, CoinDCXError
except ImportError:
    class CoinDCXError(Exception): pass
    class CoinDCXExchangeAdapter:
        inr_per_usdt = 84.0
        def get_ohlcv(self, *args, **kwargs): return []
        def get_price(self, *args, **kwargs): return 0
        def fetch_positions(self): return []
        def select_universe(self, *args, **kwargs): return []
        def active_bases(self): return []
        def market_open_bracket(self, *args, **kwargs): return {"id": "paper", "avg_price": 1}
        def floor_qty(self, *args, **kwargs): return 1.0
        def min_notional_usdt(self, *args, **kwargs): return 10.0

A = CoinDCXExchangeAdapter()
KEY_2 = os.environ.get("COINDCX_KEY_2", "").strip()
SECRET_2 = os.environ.get("COINDCX_SECRET_2", "").strip()
A2 = CoinDCXExchangeAdapter(key=KEY_2, secret=SECRET_2) if (KEY_2 and SECRET_2) else None
if A2:
    print(f"[MultiAccount] Secondary CoinDCX Account Enabled (Key ending in ...{KEY_2[-6:]})", flush=True)

# ── Logging & Persistence ─────────────────────────────────────────────────────
def log(msg):
    timestamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp} IST] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def record_trade(coin, action, side, price, qty, extra=""):
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        timestamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%dT%H:%M:%S IST")
        with open(TRADES_FILE, "a") as f:
            f.write(f"{timestamp},{coin},{action},{side},{price:.8g},{qty:.8g},{extra}\n")
    except Exception:
        pass

def load_state():
    try:
        if os.path.exists(STATE_FILE):
            return json.load(open(STATE_FILE))
    except Exception as e:
        log(f"Error loading state: {e}")
    return {}

def save_state(state):
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log(f"Error saving state: {e}")

def notify_telegram(msg):
    log(f"TELEGRAM: {msg}")
    if TG and hasattr(TG, "send_telegram_msg"):
        try:
            TG.send_telegram_msg(msg, token=TELEGRAM_TOKEN, chat_id=TELEGRAM_CHAT)
        except Exception:
            pass

def post_entry_chart(base, entry_px, sl_px, tp_px):
    if TG and hasattr(TG, "post_entry"):
        try:
            return TG.post_entry(
                token=TELEGRAM_TOKEN,
                chat=TELEGRAM_CHAT,
                base=base,
                bias="long",
                entry=entry_px,
                sl=sl_px,
                tp=tp_px,
                risk_pct=RISK_FRAC,
                account=ACCOUNT_NAME,
                tf=TF
            )
        except Exception as e:
            log(f"Telegram post_entry error: {e}")
    return None

def post_exit_chart(base, exit_px, entry_px, initial_sl_px, pnl_usdt, reason, reply_to_msg_id=None):
    if TG and hasattr(TG, "post_exit"):
        try:
            risk = max(abs(entry_px - initial_sl_px), 1e-9)
            r_multiple = (exit_px - entry_px) / risk if risk > 0 else 0.0
            return TG.post_exit(
                token=TELEGRAM_TOKEN,
                chat=TELEGRAM_CHAT,
                reply_to_msg_id=reply_to_msg_id,
                base=base,
                bias="long",
                reason=reason,
                exit_px=exit_px,
                r=r_multiple,
                pnl=pnl_usdt,
                account=ACCOUNT_NAME,
                entry=entry_px,
                sl=initial_sl_px
            )
        except Exception as e:
            log(f"Telegram post_exit error: {e}")
    return False

# ── Balance & Risk Management ─────────────────────────────────────────────────
def get_wallet_usdt(state):
    usdt_market_rate = 86.0  # Real market exchange rate (INR per USDT)
    override_bal = os.environ.get("FIBVOL_OVERRIDE_BAL_INR")
    if override_bal:
        try:
            val = float(override_bal)
            if val > 0:
                state["_bal_inr"] = val
                return val / usdt_market_rate
        except Exception:
            pass
            
    override_inr = os.environ.get("FIBVOL_OVERRIDE_BAL_INR")
    if override_inr:
        try:
            override_val = float(override_inr)
            if override_val > 0:
                state["_bal_inr"] = override_val
                return override_val / usdt_market_rate
        except Exception:
            pass

    if ARMED:
        try:
            real_inr = A.get_inr_equity()
            if real_inr > 0:
                state["_bal_inr"] = real_inr
                return real_inr / usdt_market_rate
        except Exception as e:
            log(f"Error fetching live equity: {e}")
            
    bal_inr = float(state.get("_bal_inr", START_BAL_INR))
    return bal_inr / usdt_market_rate

def apply_pnl(state, pnl_usdt):
    usdt_rate = getattr(A, "inr_per_usdt", 84.0) or 84.0
    current_inr = float(state.get("_bal_inr", START_BAL_INR))
    state["_bal_inr"] = current_inr + (pnl_usdt * usdt_rate)

def calculate_position_size(base, entry_px, sl_px, wallet_usdt, adapter=None):
    if adapter is None:
        adapter = A
    if entry_px <= 0 or sl_px >= entry_px:
        return 0.0
    risk_per_unit = entry_px - sl_px
    risk_usdt = max(wallet_usdt * RISK_FRAC, 1.0)
    qty = risk_usdt / (risk_per_unit + (entry_px * 0.0012))
    
    # Cap position leverage relative to wallet (Max 2.0x wallet equity notional size)
    max_notional = wallet_usdt * 2.0
    if qty * entry_px > max_notional:
        qty = max_notional / entry_px

    # Cap position size relative to available free INR balance
    if ARMED and adapter:
        try:
            free_inr = adapter.get_free_inr_balance()
            if free_inr > 0:
                usdt_rate = getattr(adapter, "inr_per_usdt", 84.0) or 84.0
                free_usdt = free_inr / usdt_rate
                max_margin_qty = (free_usdt * LEVERAGE * 0.8) / entry_px
                if qty > max_margin_qty:
                    qty = max_margin_qty
        except Exception:
            pass
        
    try:
        min_notional = float(adapter.min_notional_usdt(base) or 10.0)
        if qty * entry_px < min_notional:
            qty = min_notional / entry_px
        qty = adapter.floor_qty(base, qty)
    except Exception:
        pass
    return float(qty)

def get_account2_qty(base, entry_px, sl_px):
    if not A2:
        return 0.0
    try:
        free_inr_2 = A2.get_free_inr_balance()
        if free_inr_2 < 612.0:
            return 0.0
        usdt_rate = getattr(A2, "inr_per_usdt", 86.0) or 86.0
        w_usdt_2 = free_inr_2 / usdt_rate
        return calculate_position_size(base, entry_px, sl_px, w_usdt_2, adapter=A2)
    except Exception:
        return 0.0

# ── Market Data Retrieval via CoinDCX API ──────────────────────────────────────
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
    data = {}
    with ThreadPoolExecutor(max_workers=25) as ex:
        futures = {ex.submit(fetch_coin_klines, coin): coin for coin in coins}
        for fut in futures:
            coin = futures[fut]
            res = fut.result()
            if res:
                data[coin] = res
    return data

# ── Signal & Watch Loop Logic ──────────────────────────────────────────────────
def evaluate_fibvol_signal(base, klines_tuple, now_ms, state):
    """Evaluates 15m candles for 30x volume spike and updates watch levels."""
    opens, highs, lows, closes, vols, times = klines_tuple
    
    # Identify closed 15m bar index
    ci = -1
    for idx in range(len(times) - 1, -1, -1):
        if times[idx] + TF_MS <= now_ms + 1000:
            ci = idx
            break
            
    if ci < 40:
        return None, "INSUFFICIENT_DATA"
        
    cur_t = times[ci]
    cur_o, cur_h, cur_l, cur_c, cur_v = opens[ci], highs[ci], lows[ci], closes[ci], vols[ci]
    is_green = cur_c >= cur_o
    
    # Calculate 40-candle baseline volume SMA
    baseline_window = vols[ci - 40 : ci]
    avg_vol = float(np.mean(baseline_window))
    if avg_vol <= 0:
        return None, "ZERO_VOLUME"
        
    vol_mult = cur_v / avg_vol
    
    # Check watching state for this coin
    watching = state.get("watching", {}).get(base)
    
    if watching:
        last_eval_t = watching.get("last_eval_t", 0)
        if cur_t <= last_eval_t:
            # Same candle, no change
            return watching, "WATCHING_CURRENT"
            
        # New candle closed while watching!
        if is_green:
            # Re-plot Fibonacci levels on new green candle!
            rng = cur_h - cur_l
            if rng > 0:
                entry_px = A._round_px(base, cur_h - (ENTRY_FIB_LEVEL * rng))
                sl_px = A._round_px(base, cur_h - (SL_FIB_LEVEL * rng))
                risk = entry_px - sl_px
                tp_px = A._round_px(base, entry_px + (RR_RATIO * risk))
                
                watching["entry_px"] = entry_px
                watching["sl_px"] = sl_px
                watching["tp_px"] = tp_px
                watching["last_eval_t"] = cur_t
                watching["spike_mult"] = vol_mult
                log(f"[{base}] 🔄 UPDATED GREEN CANDLE FIB LEVELS ({ENTRY_FIB_LEVEL} Entry / {SL_FIB_LEVEL} SL): Entry={entry_px}, SL={sl_px}, TP={tp_px}")
                return watching, "WATCH_UPDATED"
        else:
            # Red candle closed -> stop watching this coin!
            log(f"[{base}] 🛑 RED CANDLE CLOSED ({cur_c:.6f} < {cur_o:.6f}). Cancelling watch loop.")
            return None, "WATCH_CANCELLED"
            
    # Check for NEW 30x volume spike trigger
    if is_green and vol_mult >= SPIKE_VOL_MULT:
        last_spike_t = state.get("last_spikes", {}).get(base, 0)
        if cur_t > last_spike_t:
            rng = cur_h - cur_l
            if rng > 0:
                entry_px = A._round_px(base, cur_h - (ENTRY_FIB_LEVEL * rng))
                sl_px = A._round_px(base, cur_h - (SL_FIB_LEVEL * rng))
                risk = entry_px - sl_px
                tp_px = A._round_px(base, entry_px + (RR_RATIO * risk))
                
                signal_info = {
                    "symbol": base,
                    "spike_t": cur_t,
                    "last_eval_t": cur_t,
                    "spike_mult": vol_mult,
                    "high": cur_h,
                    "low": cur_l,
                    "entry_px": entry_px,
                    "sl_px": sl_px,
                    "tp_px": tp_px
                }
                log(f"[{base}] 🚀 {SPIKE_VOL_MULT:.0f}X VOLUME SPIKE DETECTED ({vol_mult:.1f}x)! Fib {ENTRY_FIB_LEVEL} Entry={entry_px}, SL={sl_px}, TP={tp_px}")
                return signal_info, "NEW_SPIKE"
                
    return None, "NO_SIGNAL"

# ── Main Execution Loop ───────────────────────────────────────────────────────
def main():
    log("===================================================================")
    log("⚡ COINDCX 30X VOLUME SPIKE FIBONACCI (FIBVOL) STRATEGY STARTED ⚡")
    log(f"   Timeframe: {TF} | Volume Spike: >={SPIKE_VOL_MULT}x | Entry Fib: {ENTRY_FIB_LEVEL}")
    log(f"   SL Fib: {SL_FIB_LEVEL} | Risk Reward: 1:{RR_RATIO:.1f} | Risk per Trade: {RISK_FRAC*100}%")
    log(f"   Mode: {'🔴 LIVE ARMED TRADING' if ARMED else '🟡 PAPER TRADING SIMULATION'}")
    log("===================================================================")
    
    state = load_state()
    state.setdefault("_bal_inr", START_BAL_INR)
    state.setdefault("watching", {})
    state.setdefault("positions", {})
    state.setdefault("last_spikes", {})

    while True:
        try:
            now_ms = int(time.time() * 1000)
            
            # 1. Fetch Universe (Restrict to top 50 most liquid coins with min $5,000,000 daily volume to avoid slippage/fee traps)
            universe = sorted(list(A.select_universe(50, 5000000.0)))
            active_local_bases = list(state.get("positions", {}).keys())
            for ab in active_local_bases:
                if ab not in universe:
                    universe.append(ab)
            universe = sorted(universe)
            if not universe:
                time.sleep(POLL_INTERVAL)
                continue
                
            # 2. Fetch 15m Klines
            klines_map = fetch_all_klines(universe)
            
            # 3. Process Active Positions & SL/TP Triggers (with Exchange Reconciliation)
            active_positions = state.get("positions", {})
            live_pos_map = {}
            if ARMED:
                try:
                    raw_live = A.fetch_positions() or []
                    for p in raw_live:
                        if p.get("base"):
                            live_pos_map[p["base"]] = p
                except Exception as e:
                    log(f"Error fetching live positions: {e}")

            live_pos_bases = set(live_pos_map.keys())

            for base in list(active_positions.keys()):
                pos = active_positions[base]
                cur_price = A.get_price(base)
                if cur_price <= 0:
                    continue
                
                # Auto-sync real CoinDCX Position UUID onto state
                if base in live_pos_map and live_pos_map[base].get("id"):
                    pos["pos_id"] = live_pos_map[base]["id"]
                    
                entry_px = pos["entry_px"]
                sl_px = pos["sl_px"]
                initial_sl_px = pos.get("initial_sl_px", sl_px)
                tp_px = pos["tp_px"]
                qty = pos["qty"]
                pos_id = pos.get("pos_id")
                msg_id = pos.get("msg_id")
                
                # If ARMED and position no longer exists on CoinDCX -> natively closed on exchange!
                if ARMED and pos_id and base not in live_pos_bases:
                    exec_px, exec_qty, exec_fees = A.fetch_executed_trade_vwap(base, side="sell")
                    exit_px = exec_px if exec_px > 0 else cur_price
                    pnl_usdt = (exit_px - entry_px) * qty - exec_fees
                    apply_pnl(state, pnl_usdt)
                    log(f"[{base}] 🏦 POSITION CLOSED ON EXCHANGE @ REAL VWAP {exit_px:.6f}! PnL: ${pnl_usdt:+.2f}")
                    record_trade(base, "CLOSE_EXCHANGE", "LONG", exit_px, qty, f"PnL=${pnl_usdt:+.2f}")
                    post_exit_chart(base, exit_px, entry_px, initial_sl_px, pnl_usdt, "Exchange Exit", reply_to_msg_id=msg_id)
                    del state["positions"][base]
                    save_state(state)
                    continue

                # Dynamic Trailing Stop Loss step-up logic
                peak_px = float(pos.get("peak_px", entry_px))
                if cur_price > peak_px:
                    peak_px = cur_price
                    pos["peak_px"] = peak_px
                    
                    if TRAIL_ENABLE:
                        initial_sl = float(pos.get("initial_sl_px", sl_px))
                        risk = entry_px - initial_sl
                        if risk > 0:
                            peak_r = (peak_px - entry_px) / risk
                            if peak_r >= TRAIL_ACT_R:
                                desired_sl = A._round_px(base, peak_px - (TRAIL_DIST_R * risk))
                                if desired_sl > sl_px and (desired_sl - sl_px) / entry_px >= 0.001:
                                    pos["sl_px"] = desired_sl
                                    log(f"[{base}] 📈 DYNAMIC TRAILING SL STEP: Peak {peak_px:.6f} (+{peak_r:.2f}R) -> New Trailing SL {desired_sl:.6f}")
                                    if ARMED and pos_id:
                                        try:
                                            A.update_tpsl(pos_id, base, sl_price=desired_sl)
                                            log(f"[{base}] 🔒 Updated exchange Stop Loss to {desired_sl} on CoinDCX Acc 1")
                                        except Exception as e:
                                            err_str = str(e)
                                            log(f"[{base}] Update exchange SL error (Acc 1): {e}")
                                            if "Trigger price should be less than" in err_str or "less than the current price" in err_str:
                                                log(f"[{base}] ⚠️ Price retraced below trailing SL {desired_sl}. Triggering immediate exit!")
                                                cur_price = min(cur_price, desired_sl)
                                    if ARMED and A2:
                                        try:
                                            p2 = next((p for p in (A2.fetch_positions() or []) if p.get("base") == base), None)
                                            if p2 and p2.get("id"):
                                                A2.update_tpsl(p2["id"], base, sl_price=desired_sl)
                                                log(f"[{base}] 🔒 Updated exchange Stop Loss to {desired_sl} on CoinDCX Acc 2")
                                        except Exception as e2:
                                            pass
                                    save_state(state)

                # Check Stop Loss Trigger
                if cur_price <= sl_px:
                    if ARMED and pos_id:
                        try:
                            A.position_exit(pos_id)
                        except Exception as e:
                            log(f"[{base}] Exit API error (Acc 1): {e}")
                        if A2:
                            try:
                                p2 = next((p for p in (A2.fetch_positions() or []) if p.get("base") == base), None)
                                if p2 and p2.get("id"):
                                    A2.position_exit(p2["id"])
                            except Exception:
                                pass
                        time.sleep(0.5)
                        fresh_live = A.fetch_positions() or []
                        if any(p.get("base") == base for p in fresh_live):
                            log(f"[{base}] ⚠️ Exit call failed or pending. Position remains active on CoinDCX. Retrying next cycle.")
                            continue

                        exec_px, exec_qty, exec_fees = A.fetch_executed_trade_vwap(base, side="sell")
                        exit_px = exec_px if exec_px > 0 else cur_price
                    else:
                        exit_px = cur_price
                        exec_fees = 0.0

                    pnl_usdt = (exit_px - entry_px) * qty - exec_fees
                    apply_pnl(state, pnl_usdt)
                    log(f"[{base}] 🔴 STOP LOSS HIT @ REAL VWAP {exit_px:.6f} (SL Target: {sl_px:.6f})! PnL: ${pnl_usdt:+.2f}")
                    record_trade(base, "CLOSE_SL", "LONG", exit_px, qty, f"PnL=${pnl_usdt:+.2f}")
                    post_exit_chart(base, exit_px, entry_px, initial_sl_px, pnl_usdt, "Trailing SL" if pos.get("sl_px") > initial_sl_px else "SL", reply_to_msg_id=msg_id)
                    del state["positions"][base]
                    save_state(state)
                    continue
                    
                # Check Take Profit Trigger
                if cur_price >= tp_px:
                    if ARMED and pos_id:
                        try:
                            A.position_exit(pos_id)
                        except Exception as e:
                            log(f"[{base}] Exit API error (Acc 1): {e}")
                        if A2:
                            try:
                                p2 = next((p for p in (A2.fetch_positions() or []) if p.get("base") == base), None)
                                if p2 and p2.get("id"):
                                    A2.position_exit(p2["id"])
                            except Exception:
                                pass
                        time.sleep(0.5)
                        fresh_live = A.fetch_positions() or []
                        if any(p.get("base") == base for p in fresh_live):
                            log(f"[{base}] ⚠️ Exit call failed or pending. Position remains active on CoinDCX. Retrying next cycle.")
                            continue

                        exec_px, exec_qty, exec_fees = A.fetch_executed_trade_vwap(base, side="sell")
                        exit_px = exec_px if exec_px > 0 else cur_price
                    else:
                        exit_px = cur_price
                        exec_fees = 0.0

                    pnl_usdt = (exit_px - entry_px) * qty - exec_fees
                    apply_pnl(state, pnl_usdt)
                    log(f"[{base}] 🎯 TAKE PROFIT HIT @ REAL VWAP {exit_px:.6f} (TP Target: {tp_px:.6f})! PnL: ${pnl_usdt:+.2f}")
                    record_trade(base, "CLOSE_TP", "LONG", exit_px, qty, f"PnL=${pnl_usdt:+.2f}")
                    post_exit_chart(base, exit_px, entry_px, initial_sl_px, pnl_usdt, "1:5 Take Profit", reply_to_msg_id=msg_id)
                    del state["positions"][base]
                    save_state(state)
                    continue
            
            # 4. Process Signals & Watch List
            for base in universe:
                if base in state.get("positions", {}):
                    continue
                    
                if base not in klines_map:
                    continue
                    
                signal_info, code = evaluate_fibvol_signal(base, klines_map[base], now_ms, state)
                
                if code == "NEW_SPIKE":
                    state["watching"][base] = signal_info
                    state["last_spikes"][base] = signal_info["spike_t"]
                    save_state(state)
                    notify_telegram(f"⚡ [{ACCOUNT_NAME}] {base} 30X Volume Spike ({signal_info['spike_mult']:.1f}x)! Watching Fib 0.6 Entry={signal_info['entry_px']}")
                    
                elif code == "WATCH_CANCELLED":
                    if base in state["watching"]:
                        del state["watching"][base]
                        save_state(state)
                        
                elif code == "WATCH_UPDATED":
                    state["watching"][base] = signal_info
                    save_state(state)

            # 5. Check Limit Order Entries for Watching Coins (enforcing MAX_CONCURRENT cap)
            for base, watch in list(state.get("watching", {}).items()):
                if base in state.get("positions", {}):
                    del state["watching"][base]
                    continue
                    
                if len(state.get("positions", {})) >= MAX_CONCURRENT:
                    log(f"[{base}] ⚠️ MAX_CONCURRENT positions ({MAX_CONCURRENT}) reached. Skipping entry.")
                    break
                    
                cur_price = A.get_price(base)
                if cur_price <= 0:
                    continue
                    
                entry_px = watch["entry_px"]
                sl_px = watch["sl_px"]
                tp_px = watch["tp_px"]
                
                # Limit order trigger: current price retraces down to or below 0.5 Fib entry price!
                if cur_price <= entry_px:
                    if cur_price <= sl_px:
                        log(f"[{base}] ⚠️ Price {cur_price:.6f} dropped below SL {sl_px:.6f} before fill. Skipping trade.")
                        del state["watching"][base]
                        save_state(state)
                        continue

                    # Ensure SL is strictly lower than cur_price for long order placement
                    if sl_px >= cur_price:
                        sl_px = cur_price * 0.995

                    w_usdt = get_wallet_usdt(state)
                    qty = calculate_position_size(base, entry_px, sl_px, w_usdt)
                    if qty <= 0:
                        continue
                        
                    # Fetch per-coin max leverage directly from CoinDCX instrument spec for exact coin-by-coin clamping
                    inst = A.instrument(base) if hasattr(A, "instrument") else {}
                    coin_max_lev = int(float(inst.get("max_leverage_long") or LEVERAGE))
                    
                    sl_dist_pct = (entry_px - sl_px) / entry_px if entry_px > 0 else 0.10
                    target_lev = int(1.0 / max(sl_dist_pct, 0.01)) if sl_dist_pct > 0 else LEVERAGE
                    dyn_lev = max(1, min(target_lev, coin_max_lev))

                    pos_id = None
                    if ARMED:
                        try:
                            res = A.limit_open_bracket(base, is_buy=True, qty=qty, price=entry_px, leverage=dyn_lev, sl_price=sl_px, tp_price=tp_px)
                            pos_id = res.get("id") or res.get("position_id") or res.get("order_id")
                            log(f"[{base}] 🔴 LIVE LIMIT BRACKET ORDER PLACED ON COINDCX (Acc 1): Limit Px={entry_px:.6f}, DynLev={dyn_lev}x, ID={pos_id}")
                        except Exception as e:
                            log(f"[{base}] Live limit order execution error (Acc 1): {e}")
                            if base in state["watching"]:
                                del state["watching"][base]
                                save_state(state)
                            continue

                        if A2:
                            try:
                                qty2 = get_account2_qty(base, entry_px, sl_px)
                                if qty2 > 0:
                                    res2 = A2.limit_open_bracket(base, is_buy=True, qty=qty2, price=entry_px, leverage=dyn_lev, sl_price=sl_px, tp_price=tp_px)
                                    log(f"[{base}] 🔴 LIVE LIMIT BRACKET ORDER PLACED ON COINDCX (Acc 2): Qty={qty2}, DynLev={dyn_lev}x, ID={res2.get('id')}")
                            except Exception as e2:
                                log(f"[{base}] Account 2 limit order placement error: {e2}")
                            
                    fill_entry_px = entry_px
                    if ARMED:
                        exec_px, _, _ = A.fetch_executed_trade_vwap(base, side="buy")
                        if exec_px > 0:
                            fill_entry_px = exec_px
                            
                    log(f"[{base}] 🚀 LIMIT ORDER FILLED @ {fill_entry_px:.6f} (Target Fib {ENTRY_FIB_LEVEL}: {entry_px:.6f})! Qty: {qty}")
                    record_trade(base, "ENTRY_LONG", "LONG", fill_entry_px, qty, f"SL={sl_px:.6f}, TP={tp_px:.6f}")
                    
                    msg_id = post_entry_chart(base, fill_entry_px, sl_px, tp_px)
                    
                    state["positions"][base] = {
                        "symbol": base,
                        "entry_px": fill_entry_px,
                        "sl_px": sl_px,
                        "initial_sl_px": sl_px,
                        "peak_px": fill_entry_px,
                        "tp_px": tp_px,
                        "qty": qty,
                        "entry_t": now_ms,
                        "pos_id": pos_id,
                        "msg_id": msg_id
                    }
                    del state["watching"][base]
                    save_state(state)

            save_state(state)
            time.sleep(POLL_INTERVAL)
            
        except Exception as e:
            log(f"Error in main execution loop: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
