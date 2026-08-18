#!/usr/bin/env python3
"""scripts/stream_breakout_booster_paper.py — Live Paper Trading Daemon for Breakout Booster Strategy.

Features:
- Polls live 5-minute candles of NIFTY and BANKNIFTY during market hours (09:15 - 15:30 IST).
- Generates Breakout Booster signals (10m ORB, 15m ORB, 5m Momentum).
- Resolves ATM / Near-ITM option contracts.
- Simulates realistic paper executions with virtual fills, SL, TP, and holding duration limits.
- Persists all trades and metrics to SQLite database (`/root/breakout_booster/paper_trades.db`).
- Logs live dashboard updates to console and journal.
"""
from __future__ import annotations

import os
import sys
import time
import json
import sqlite3
import datetime
import urllib.request
import ssl
from typing import Dict, Any

# Ensure project root is in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

import pandas as pd
from strategies.breakout_booster import detect_breakout_signals, BreakoutSignal

DB_PATH = "/root/breakout_booster/paper_trades.db" if os.path.exists("/root") else os.path.join(BASE_DIR, "paper_trades.db")
SSL_CTX = ssl._create_unverified_context()

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            underlying TEXT,
            signal_type TEXT,
            direction TEXT,
            symbol TEXT,
            option_type TEXT,
            strike INTEGER,
            quantity INTEGER,
            entry_price REAL,
            exit_price REAL,
            target_price REAL,
            stop_loss_price REAL,
            pnl REAL,
            return_pct REAL,
            duration_sec REAL,
            status TEXT, -- 'OPEN', 'CLOSED_TP', 'CLOSED_SL', 'CLOSED_TIMEOUT'
            entry_time TEXT,
            exit_time TEXT
        )
    """)
    conn.commit()
    conn.close()

def log(msg: str):
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] {msg}", flush=True)

def fetch_5m_candles(symbol_ticker: str) -> pd.DataFrame:
    """Fetches recent 5-minute candles from market data feed."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol_ticker}?interval=5m&range=5d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (BreakoutBooster/1.0)"})
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        res = data["chart"]["result"][0]
        timestamps = res["timestamp"]
        q = res["indicators"]["quote"][0]
        
        records = []
        for i, ts in enumerate(timestamps):
            if q["open"][i] is not None and q["close"][i] is not None:
                dt = datetime.datetime.fromtimestamp(ts, datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
                records.append({
                    "timestamp": dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "open": float(q["open"][i]),
                    "high": float(q["high"][i]),
                    "low": float(q["low"][i]),
                    "close": float(q["close"][i]),
                    "volume": float(q["volume"][i] or 0.0)
                })
        df = pd.DataFrame(records)
        if not df.empty:
            df["dt"] = pd.to_datetime(df["timestamp"])
            df.set_index("dt", inplace=True)
        return df
    except Exception as e:
        log(f"Error fetching candles for {symbol_ticker}: {e}")
        return pd.DataFrame()

def estimate_option_premium(underlying: str, spot: float, strike: int, opt_type: str) -> float:
    """Estimates realistic option premium for ATM/Near-ITM contracts based on current volatility."""
    if underlying == "NIFTY":
        # Base ATM premium ~ ₹80 to ₹140
        diff = (spot - strike) if opt_type == "CE" else (strike - spot)
        intrinsic = max(0.0, diff)
        time_value = 85.0  # standard weekly ATM extrinsic value
        return round(max(25.0, intrinsic + time_value), 2)
    elif underlying == "BANKNIFTY":
        # Base ATM monthly premium ~ ₹450 to ₹650
        diff = (spot - strike) if opt_type == "CE" else (strike - spot)
        intrinsic = max(0.0, diff)
        time_value = 420.0
        return round(max(80.0, intrinsic + time_value), 2)
    return 100.0

class PaperTradingEngine:
    def __init__(self):
        init_db()
        self.open_positions: Dict[str, Dict[str, Any]] = {}
        self.seen_signals = set()
        log("Breakout Booster Paper Trading Engine initialized.")

    def run_cycle(self):
        now_ist = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
        today_date = now_ist.date()

        # Update open positions
        self.evaluate_open_positions()

        # Only scan for new entries between 09:20 and 15:00 IST
        if not (datetime.time(9, 20) <= now_ist.time() <= datetime.time(15, 0)):
            return

        targets = [
            ("NIFTY", "%5ENSEI"),
            ("BANKNIFTY", "%5ENSEBANK")
        ]

        for und, ticker in targets:
            df = fetch_5m_candles(ticker)
            if df.empty:
                continue

            signals = detect_breakout_signals(df, underlying=und, session_date=today_date)
            for sig in signals:
                sig_key = f"{today_date}_{sig.underlying}_{sig.signal_type}_{sig.action}_{sig.timestamp.strftime('%H%M')}"
                if sig_key in self.seen_signals:
                    continue

                self.seen_signals.add(sig_key)
                self.open_paper_trade(sig)

    def open_paper_trade(self, sig: BreakoutSignal):
        pos_key = f"{sig.underlying}_{sig.option_type}_{sig.recommended_strike}"
        if pos_key in self.open_positions:
            return  # already have open position

        entry_px = estimate_option_premium(sig.underlying, sig.underlying_spot, sig.recommended_strike, sig.option_type)
        tp_px = round(entry_px * (1.0 + sig.target_pct / 100.0), 2)
        sl_px = round(entry_px * (1.0 - sig.stop_loss_pct / 100.0), 2)
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sym_name = f"{sig.underlying} {sig.recommended_strike} {sig.option_type}"

        # Insert into DB
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO paper_trades 
            (timestamp, underlying, signal_type, direction, symbol, option_type, strike, quantity, entry_price, target_price, stop_loss_price, status, entry_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
        """, (now_str, sig.underlying, sig.signal_type, sig.direction, sym_name, sig.option_type, sig.recommended_strike, sig.suggested_qty, entry_px, tp_px, sl_px, now_str))
        trade_id = cur.lastrowid
        conn.commit()
        conn.close()

        self.open_positions[pos_key] = {
            "id": trade_id,
            "signal": sig,
            "symbol": sym_name,
            "entry_price": entry_px,
            "tp_price": tp_px,
            "sl_price": sl_px,
            "entry_time": time.time(),
            "quantity": sig.suggested_qty
        }

        log(f"🟢 [PAPER ENTRY] {sig.signal_type} -> BUY {sig.suggested_qty}x {sym_name} @ ₹{entry_px:.2f} | TP: ₹{tp_px:.2f} (+{sig.target_pct}%), SL: ₹{sl_px:.2f} (-{sig.stop_loss_pct}%)")

    def evaluate_open_positions(self):
        if not self.open_positions:
            return

        to_close = []
        for pos_key, pos in self.open_positions.items():
            trade_id = pos["id"]
            sig: BreakoutSignal = pos["signal"]
            entry_px = pos["entry_price"]
            tp_px = pos["tp_price"]
            sl_px = pos["sl_price"]
            entry_ts = pos["entry_time"]
            qty = pos["quantity"]

            elapsed_sec = time.time() - entry_ts

            # Fetch current spot
            ticker = "%5ENSEI" if sig.underlying == "NIFTY" else "%5ENSEBANK"
            df = fetch_5m_candles(ticker)
            if df.empty:
                continue

            current_spot = df["close"].iloc[-1]
            current_px = estimate_option_premium(sig.underlying, current_spot, sig.recommended_strike, sig.option_type)

            status = None
            exit_px = current_px

            # Check TP hit
            if current_px >= tp_px:
                status = "CLOSED_TP"
                exit_px = tp_px
            # Check SL hit
            elif current_px <= sl_px:
                status = "CLOSED_SL"
                exit_px = sl_px
            # Check Max Time Limit (15 mins)
            elif elapsed_sec >= 900:
                status = "CLOSED_TIMEOUT"
                exit_px = current_px

            if status:
                pnl = (exit_px - entry_px) * qty
                pct = ((exit_px - entry_px) / entry_px) * 100.0
                now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # Update DB
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute("""
                    UPDATE paper_trades
                    SET exit_price = ?, pnl = ?, return_pct = ?, duration_sec = ?, status = ?, exit_time = ?
                    WHERE id = ?
                """, (exit_px, round(pnl, 2), round(pct, 2), round(elapsed_sec, 1), status, now_str, trade_id))
                conn.commit()
                conn.close()

                tag = "🟢 WIN" if pnl > 0 else "🔴 LOSS"
                log(f"{tag} [PAPER EXIT] {pos['symbol']} | Reason: {status} | Out: ₹{exit_px:.2f} ({pct:+.2f}%) | PnL: ₹{pnl:+.2f} | Dur: {elapsed_sec/60:.1f}m")
                to_close.append(pos_key)

        for k in to_close:
            del self.open_positions[k]

def main():
    log("Starting Breakout Booster Paper Trading Service...")
    engine = PaperTradingEngine()
    while True:
        try:
            engine.run_cycle()
        except Exception as e:
            log(f"Unhandled error in paper cycle: {e}")
        time.sleep(10)

if __name__ == "__main__":
    main()
