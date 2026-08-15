#!/usr/bin/env python3
"""stream_short_coindcx.py — Mean Reversion Shorting Strategy Engine for CoinDCX.

Strategy Rules:
1. Watch Trigger: 4h candle closes GREEN with Volume >= 10.0x SMA(40) baseline volume.
2. Peak Tracking: Track the highest price reached during the pump (pump_peak_px).
3. Short Entry: Enter Short (Sell) when a 4h candle closes RED and below the 9 EMA.
4. Levels:
   - Entry Price: Close of the trigger candle (Market Sell).
   - Stop Loss: pump_peak_px (the absolute peak).
   - Take Profit: 70% retracement (Peak - 0.70 * (Peak - Spike_Low)).
5. Risk Management: 1% wallet balance risk per trade (RISK_FRAC = 0.01).
"""
import os
import sys
import time
import json
import datetime
from concurrent.futures import ThreadPoolExecutor
import numpy as np

# Add adapter path to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from platforms.coindcx.adapter import CoinDCXAdapter, CoinDCXError
from shared_scripts.b2_telegram import notify_telegram, post_entry_chart, post_exit_chart

# ── Environment & Config ──────────────────────────────────────────────────────
TF = "4h"
TF_SEC = 4 * 60 * 60
TF_MS = TF_SEC * 1000

SPIKE_VOL_MULT = float(os.environ.get("MR_SPIKE_VOL", "10.0"))
EMA_PERIOD = 9
RISK_FRAC = float(os.environ.get("MR_RISK_FRAC", "0.01"))  # 1% risk per trade
LEVERAGE = int(os.environ.get("MR_LEVERAGE", "10"))
MAX_CONCURRENT = int(os.environ.get("MR_MAX_CONCURRENT", "5"))
POLL_INTERVAL = float(os.environ.get("MR_POLL", "1.0"))  # 1s positions loop

ARMED = os.environ.get("LIVE_ARMED", "0") == "1"
START_BAL_INR = float(os.environ.get("MR_START_BAL_INR", "16000"))
STATE_FILE = os.path.expanduser("~/mr_coindcx_state.json")
ACCOUNT_NAME = "CoinDCX Acc 1 (Shorting)"

# Initialize CoinDCX Adapters
key1 = os.environ.get("COINDCX_API_KEY")
sec1 = os.environ.get("COINDCX_SECRET")
A = CoinDCXAdapter(api_key=key1, secret=sec1) if key1 and sec1 else None

key2 = os.environ.get("COINDCX_API_KEY_2")
sec2 = os.environ.get("COINDCX_SECRET_2")
A2 = CoinDCXAdapter(api_key=key2, secret=sec2) if key2 and sec2 else None

if not A:
    print("ERROR: COINDCX_API_KEY and COINDCX_SECRET must be set in environment.")
    sys.exit(1)

# ── State Management ──────────────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"watching": {}, "positions": {}, "_bal_inr": START_BAL_INR}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"Error saving state: {e}")

def log(msg):
    tstr = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"[{tstr}] {msg}", flush=True)

# ── Balance & Risk Math ───────────────────────────────────────────────────────
def get_wallet_usdt(state):
    if ARMED:
        try:
            inr = A.get_free_inr_balance()
            rate = getattr(A, "inr_per_usdt", 88.5) or 88.5
            return inr / rate
        except Exception:
            pass
    inr = state.get("_bal_inr", START_BAL_INR)
    return inr / 88.5

def calculate_position_size(base, entry_px, sl_px, wallet_usdt, adapter=A):
    risk_usdt = wallet_usdt * RISK_FRAC
    risk_px = abs(sl_px - entry_px)
    if risk_px <= 0:
        return 0.0
    qty = risk_usdt / risk_px
    try:
        min_notional = float(adapter.min_notional_usdt(base) or 10.0)
        if qty * entry_px < min_notional:
            qty = min_notional / entry_px
        qty = adapter.floor_qty(base, qty)
    except Exception:
        pass
    return float(qty)

# ── Market Data ───────────────────────────────────────────────────────────────
def fetch_coin_klines(base):
    try:
        # Fetch 4h candles
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

# ── Strategy Evaluation ───────────────────────────────────────────────────────
def evaluate_short_signal(base, klines_tuple, now_ms, state):
    opens, highs, lows, closes, vols, times = klines_tuple
    
    # Identify closed 4h bar index
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
    
    # Stale data protection: ensure 4h close is within 1.5 periods (6 hours)
    if now_ms - (cur_t + TF_MS) >= TF_MS * 1.5:
        return None, "STALE_DATA"
        
    # Calculate volume MA (excluding spike candle)
    baseline_window = vols[ci - 40 : ci]
    avg_vol = float(np.mean(baseline_window))
    if avg_vol <= 0:
        return None, "ZERO_VOLUME"
        
    vol_mult = cur_v / avg_vol
    
    # Calculate 9 EMA of closes
    k = 2.0 / (EMA_PERIOD + 1)
    ema = [closes[0]]
    for cl in closes[1:]:
        ema.append(cl * k + ema[-1] * (1.0 - k))
    cur_ema = ema[ci]
    
    # Check watching state for this coin
    watching = state.get("watching", {}).get(base)
    
    if watching:
        # Update peak high during pump
        cur_px = A.get_price(base)
        if cur_px > watching.get("pump_peak_px", 0):
            watching["pump_peak_px"] = cur_px
            watching["last_eval_t"] = now_ms
            return watching, "WATCH_UPDATED"
            
        last_eval_t = watching.get("last_eval_t", 0)
        if cur_t <= last_eval_t:
            return watching, "WATCHING_CURRENT"
            
        # Trigger entry: red close below 9 EMA
        if is_red and cur_c < cur_ema:
            entry_px = cur_c
            sl_px = watching["pump_peak_px"]
            
            # Risk distance validation
            inc = float(A.instrument(base).get("price_increment") or 0.0) if hasattr(A, "instrument") else 0.0
            min_risk = max(3 * inc, entry_px * 0.001, 1e-8)
            if (sl_px - entry_px) >= min_risk:
                watching["entry_px"] = entry_px
                watching["sl_px"] = sl_px
                # TP at 70% retracement: Peak - 0.70 * (Peak - Start)
                watching["tp_px"] = sl_px - 0.70 * (sl_px - watching["pump_start_px"])
                return watching, "TRIGGER_SHORT"
            else:
                log(f"[{base}] ⚠️ Red close below EMA, but risk too narrow. Cancelling watch.")
                return None, "WATCH_CANCELLED"
                
        # If price retraces back below the pump start, cancel watch
        if cur_c < watching["pump_start_px"]:
            log(f"[{base}] 🛑 Price retraced below pump start. Cancelling watch.")
            return None, "WATCH_CANCELLED"
            
        watching["last_eval_t"] = cur_t
        return watching, "WATCHING_CONTINUE"
        
    # Check for new spike (10x volume on green 4h close)
    if is_green and vol_mult >= SPIKE_VOL_MULT:
        last_spike_t = state.get("last_spikes", {}).get(base, 0)
        if cur_t > last_spike_t:
            watching_info = {
                "symbol": base,
                "spike_t": cur_t,
                "last_eval_t": cur_t,
                "spike_mult": vol_mult,
                "pump_start_px": cur_l,
                "pump_peak_px": cur_h
            }
            log(f"[{base}] 🚀 PUMP DETECTED ({vol_mult:.1f}x)! Watching for MR Short confirmation.")
            return watching_info, "NEW_PUMP"
            
    return None, "NO_SIGNAL"

# ── Main Loop ─────────────────────────────────────────────────────────────────
def main():
    log("===================================================================")
    log("⚡ COINDCX MEAN REVERSION SHORTING STRATEGY STARTED ⚡")
    log(f"   Timeframe: {TF} | Spike Vol: >={SPIKE_VOL_MULT}x | Exit: 70% Retrace")
    log(f"   Mode: {'🔴 LIVE ARMED TRADING' if ARMED else '🟡 PAPER TRADING SIMULATION'}")
    log("===================================================================")
    
    state = load_state()
    state.setdefault("watching", {})
    state.setdefault("positions", {})
    state.setdefault("last_spikes", {})
    
    while True:
        try:
            now_ms = int(time.time() * 1000)
            universe = sorted(list(A.active_bases() or []))
            if not universe:
                time.sleep(10)
                continue
                
            klines_map = fetch_all_klines(universe)
            
            # Reconcile Positions
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
                    
                pos_id = pos.get("pos_id")
                
                # Check Exchange Exit
                if ARMED and pos_id and base not in live_pos_bases:
                    exec_px, exec_qty, exec_fees = A.fetch_executed_trade_vwap(base, side="buy")
                    exit_px = exec_px if exec_px > 0 else cur_price
                    pnl_usdt = (pos["entry_px"] - exit_px) * pos["qty"] - exec_fees
                    state["_bal_inr"] += pnl_usdt * 88.5
                    log(f"[{base}] 🏦 SHORT POSITION CLOSED ON EXCHANGE @ REAL VWAP {exit_px:.6f}! PnL: ${pnl_usdt:+.2f}")
                    del state["positions"][base]
                    save_state(state)
                    continue
                    
                # Check Stop Loss (Short)
                if cur_price >= pos["sl_px"]:
                    if ARMED and pos_id:
                        try:
                            A.position_exit(pos_id)
                        except Exception:
                            pass
                    exit_px = cur_price
                    pnl_usdt = (pos["entry_px"] - exit_px) * pos["qty"]
                    state["_bal_inr"] += pnl_usdt * 88.5
                    log(f"[{base}] 🔴 SHORT STOP LOSS HIT @ {exit_px:.6f}! PnL: ${pnl_usdt:+.2f}")
                    del state["positions"][base]
                    save_state(state)
                    continue
                    
                # Check Take Profit (Short)
                if cur_price <= pos["tp_px"]:
                    if ARMED and pos_id:
                        try:
                            A.position_exit(pos_id)
                        except Exception:
                            pass
                    exit_px = cur_price
                    pnl_usdt = (pos["entry_px"] - exit_px) * pos["qty"]
                    state["_bal_inr"] += pnl_usdt * 88.5
                    log(f"[{base}] 🎯 SHORT TAKE PROFIT HIT @ {exit_px:.6f}! PnL: ${pnl_usdt:+.2f}")
                    del state["positions"][base]
                    save_state(state)
                    continue
            
            # Process Signals
            for base in universe:
                if base in state.get("positions", {}):
                    continue
                if base not in klines_map:
                    continue
                    
                signal_info, code = evaluate_short_signal(base, klines_map[base], now_ms, state)
                
                if code == "NEW_PUMP":
                    state["watching"][base] = signal_info
                    state["last_spikes"][base] = signal_info["spike_t"]
                    save_state(state)
                    notify_telegram(f"🚨 [Shorting Bot] {base} Pump Spike ({signal_info['spike_mult']:.1f}x)! Watching for exhaustion short.")
                    
                elif code == "WATCH_CANCELLED":
                    if base in state["watching"]:
                        del state["watching"][base]
                        save_state(state)
                        
                elif code == "WATCH_UPDATED":
                    state["watching"][base] = signal_info
                    save_state(state)
                    
                elif code == "TRIGGER_SHORT":
                    if len(state["positions"]) >= MAX_CONCURRENT:
                        log(f"[{base}] ⚠️ MAX_CONCURRENT positions reached. Skipping entry.")
                        del state["watching"][base]
                        save_state(state)
                        continue
                        
                    entry_px = signal_info["entry_px"]
                    sl_px = signal_info["sl_px"]
                    tp_px = signal_info["tp_px"]
                    
                    cur_price = A.get_price(base)
                    # Slippage check
                    if cur_price >= sl_px * 0.998:
                        log(f"[{base}] ⚠️ Price too close to Stop Loss. Skipping trade.")
                        del state["watching"][base]
                        save_state(state)
                        continue
                        
                    w_usdt = get_wallet_usdt(state)
                    qty = calculate_position_size(base, entry_px, sl_px, w_usdt)
                    if qty <= 0:
                        del state["watching"][base]
                        save_state(state)
                        continue
                        
                    pos_id = None
                    if ARMED:
                        try:
                            # Short bracket order: is_buy = False
                            res = A.limit_open_bracket(base, is_buy=False, qty=qty, price=entry_px, leverage=LEVERAGE, sl_price=sl_px, tp_price=tp_px)
                            pos_id = res.get("id") or res.get("position_id")
                            log(f"[{base}] 🔴 LIVE SHORT BRACKET ORDER PLACED ON EXCHANGE! ID: {pos_id}")
                        except Exception as e:
                            log(f"[{base}] Live Short execution error: {e}")
                            del state["watching"][base]
                            save_state(state)
                            continue
                            
                    state["positions"][base] = {
                        "symbol": base,
                        "entry_px": entry_px,
                        "sl_px": sl_px,
                        "tp_px": tp_px,
                        "qty": qty,
                        "entry_t": now_ms,
                        "pos_id": pos_id
                    }
                    del state["watching"][base]
                    save_state(state)
                    log(f"[{base}] 📉 SHORT POSITION TRIGGERED: Entry={entry_px}, SL={sl_px}, TP={tp_px}, Qty={qty}")
            
            save_state(state)
            time.sleep(POLL_INTERVAL)
            
        except Exception as e:
            log(f"Error in main loop: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
