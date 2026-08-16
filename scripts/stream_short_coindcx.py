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

# Add paths to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from dotenv import load_dotenv
    for env_p in ["/root/go-trader/.env", "/root/trading-bot/crypto/.env", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")]:
        if os.path.exists(env_p):
            load_dotenv(env_p)
            break
except ImportError:
    pass

from platforms.coindcx.adapter import CoinDCXExchangeAdapter as CoinDCXAdapter, CoinDCXError
try:
    from shared_scripts.b2_telegram import notify_telegram, post_entry_chart, post_exit_chart
except ImportError:
    def notify_telegram(msg): pass
    def post_entry_chart(*args, **kwargs): pass
    def post_exit_chart(*args, **kwargs): pass

# ── Environment & Config ──────────────────────────────────────────────────────
TF = os.environ.get("MR_TF", "4h")
TF_SEC = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}.get(TF, 14400)
TF_MS = TF_SEC * 1000

SPIKE_VOL_MULT = float(os.environ.get("MR_SPIKE_VOL", "10.0"))
EMA_PERIOD = int(os.environ.get("MR_EMA_PERIOD", "9"))
RISK_FRAC = float(os.environ.get("MR_RISK_FRAC", "0.01"))  # 1% risk per trade
LEVERAGE = int(os.environ.get("MR_LEVERAGE", "10"))
MAX_CONCURRENT = int(os.environ.get("MR_MAX_CONCURRENT", "5"))
POLL_INTERVAL = float(os.environ.get("MR_POLL", "1.0"))  # 1s positions loop

ARMED = os.environ.get("LIVE_ARMED", "0") == "1"
START_BAL_INR = float(os.environ.get("MR_START_BAL_INR", "16000"))
OUT_DIR = os.environ.get("MR_OUT", os.path.expanduser("~/short_coindcx"))
os.makedirs(OUT_DIR, exist_ok=True)
STATE_FILE = os.path.join(OUT_DIR, "state.json")
TRADES_FILE = os.path.join(OUT_DIR, "trades.jsonl")
ACCOUNT_NAME = "CoinDCX Acc 1 (Shorting)"

# Initialize CoinDCX Adapters
key1 = os.environ.get("COINDCX_LIVE_API_KEY") or os.environ.get("COINDCX_API_KEY")
sec1 = os.environ.get("COINDCX_LIVE_API_SECRET") or os.environ.get("COINDCX_SECRET")
A = CoinDCXAdapter(key=key1, secret=sec1) if key1 and sec1 else None

key2 = os.environ.get("COINDCX_KEY_2") or os.environ.get("COINDCX_API_KEY_2")
sec2 = os.environ.get("COINDCX_SECRET_2")
A2 = CoinDCXAdapter(key=key2, secret=sec2) if key2 and sec2 else None

if not A:
    print("ERROR: COINDCX_LIVE_API_KEY and COINDCX_LIVE_API_SECRET must be set in environment.")
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
    tstr = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
    print(f"[{tstr}] {msg}", flush=True)

def log_trade(event: str, base: str, **kwargs):
    """Append a trade event to trades.jsonl for permanent audit trail."""
    record = {
        "ts": datetime.datetime.now().isoformat(),
        "event": event,   # ENTRY | EXIT_SL | EXIT_TP | EXIT_EXCHANGE
        "base": base,
        "mode": "LIVE" if ARMED else "PAPER",
        **kwargs
    }
    try:
        with open(TRADES_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        log(f"[{base}] ⚠️ Failed to write trade log: {e}")

# ── Balance & Risk Math ───────────────────────────────────────────────────────
def get_wallet_usdt(state):
    if ARMED:
        try:
            inr = A.get_free_inr_balance()
            rate = getattr(A, "inr_per_usdt", 102.0) or 102.0
            return inr / rate
        except Exception:
            pass
    inr = state.get("_bal_inr", START_BAL_INR)
    rate = getattr(A, "inr_per_usdt", 102.0) or 102.0
    return inr / rate

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
    
    # Stale data protection: ensure last closed 4h bar is within 2 periods (8h)
    # Scan latency can be 40-70s, so we give 2x buffer (not 1.5x which was too tight)
    if now_ms - (cur_t + TF_MS) >= TF_MS * 2:
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
                calc_tp = sl_px - 0.70 * (sl_px - watching["pump_start_px"])
                # For Short, TP must be strictly below Entry
                if calc_tp >= entry_px * 0.998:
                    calc_tp = entry_px - 2.0 * (sl_px - entry_px) # Fallback to 1:2 RR below entry
                watching["tp_px"] = calc_tp
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

SCAN_INTERVAL = float(os.environ.get("MR_SCAN_INTERVAL", "15.0")) # 15s candle scan loop

# ── Main Loop ─────────────────────────────────────────────────────────────────
def main():
    log("===================================================================")
    log("⚡ COINDCX MEAN REVERSION SHORTING STRATEGY STARTED ⚡")
    log(f"   Timeframe: {TF} | Spike Vol: >={SPIKE_VOL_MULT}x | Exit: 70% Retrace")
    log(f"   Mode: {'🔴 LIVE ARMED TRADING' if ARMED else '🟡 PAPER TRADING SIMULATION'}")
    log(f"   Scan Interval: {SCAN_INTERVAL}s | Positions Poll: {POLL_INTERVAL}s")
    log("===================================================================")
    
    state = load_state()
    state.setdefault("watching", {})
    state.setdefault("positions", {})
    state.setdefault("last_spikes", {})
    
    last_scan_t = 0
    last_universe_t = 0
    universe = []
    klines_map = {}
    
    while True:
        try:
            now_ms = int(time.time() * 1000)
            now_sec = time.time()

            # Refresh universe every 5 minutes (not every 1s tick)
            if now_sec - last_universe_t >= 300 or not universe:
                fresh = sorted(list(A.active_bases() or []))
                if fresh:
                    universe = fresh
                    last_universe_t = now_sec
                elif not universe:
                    time.sleep(5)
                    continue
                
            # Scan universe on scan interval
            if now_sec - last_scan_t >= SCAN_INTERVAL:
                t0 = time.time()
                klines_map = fetch_all_klines(universe)
                last_scan_t = now_sec
                w_count = len(state.get("watching", {}))
                p_count = len(state.get("positions", {}))
                log(f"🔍 Scanned {len(klines_map)}/{len(universe)} coins in {time.time()-t0:.1f}s | Watching: {w_count} | Positions: {p_count}")
            
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
                    state["_bal_inr"] += pnl_usdt * getattr(A, "inr_per_usdt", 102.0)
                    log(f"[{base}] 🏦 SHORT POSITION CLOSED ON EXCHANGE @ REAL VWAP {exit_px:.6f}! PnL: ${pnl_usdt:+.2f}")
                    log_trade("EXIT_EXCHANGE", base,
                        entry_px=pos["entry_px"], exit_px=exit_px,
                        qty=pos["qty"], pnl_usdt=round(pnl_usdt, 4),
                        sl_px=pos["sl_px"], tp_px=pos["tp_px"],
                        entry_t=pos.get("entry_t"), fees_usdt=round(exec_fees, 4),
                        pos_id=pos_id)
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
                    state["_bal_inr"] += pnl_usdt * getattr(A, "inr_per_usdt", 102.0)
                    log(f"[{base}] 🔴 SHORT STOP LOSS HIT @ {exit_px:.6f}! PnL: ${pnl_usdt:+.2f}")
                    log_trade("EXIT_SL", base,
                        entry_px=pos["entry_px"], exit_px=exit_px,
                        qty=pos["qty"], pnl_usdt=round(pnl_usdt, 4),
                        sl_px=pos["sl_px"], tp_px=pos["tp_px"],
                        entry_t=pos.get("entry_t"), pos_id=pos_id)
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
                    state["_bal_inr"] += pnl_usdt * getattr(A, "inr_per_usdt", 102.0)
                    log(f"[{base}] 🎯 SHORT TAKE PROFIT HIT @ {exit_px:.6f}! PnL: ${pnl_usdt:+.2f}")
                    log_trade("EXIT_TP", base,
                        entry_px=pos["entry_px"], exit_px=exit_px,
                        qty=pos["qty"], pnl_usdt=round(pnl_usdt, 4),
                        sl_px=pos["sl_px"], tp_px=pos["tp_px"],
                        entry_t=pos.get("entry_t"), pos_id=pos_id)
                    del state["positions"][base]
                    save_state(state)
                    continue
            
            # Process Signals
            for base in universe:
                if base in state.get("positions", {}):
                    continue
                if base not in klines_map:
                    # If we're watching a coin but can't fetch its klines, clean up after 2 scan cycles
                    if base in state.get("watching", {}):
                        w = state["watching"][base]
                        if now_ms - w.get("last_eval_t", now_ms) > TF_MS * 2:
                            log(f"[{base}] 🧹 Stale watch (no klines). Removing.")
                            del state["watching"][base]
                            save_state(state)
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
                            # Clamp leverage to coin's max allowed
                            instr = A.instrument(base) or {}
                            max_lev = int(instr.get("max_leverage_short") or instr.get("max_leverage_long") or LEVERAGE)
                            order_lev = min(LEVERAGE, max_lev)
                            # Short bracket order: is_buy = False
                            res = A.limit_open_bracket(base, is_buy=False, qty=qty, price=entry_px, leverage=order_lev, sl_price=sl_px, tp_price=tp_px)
                            pos_id = res.get("id") or res.get("position_id")
                            log(f"[{base}] 🔴 LIVE SHORT BRACKET ORDER PLACED ON EXCHANGE! ID: {pos_id} (Lev={order_lev}x)")
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
                    log_trade("ENTRY", base,
                        entry_px=entry_px, sl_px=sl_px, tp_px=tp_px,
                        qty=qty, entry_t=now_ms, pos_id=pos_id,
                        spike_mult=signal_info.get("spike_mult"),
                        pump_start_px=signal_info.get("pump_start_px"),
                        pump_peak_px=signal_info.get("pump_peak_px"))
            
            save_state(state)
            time.sleep(POLL_INTERVAL)
            
        except Exception as e:
            log(f"Error in main loop: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
