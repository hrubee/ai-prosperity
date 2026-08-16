#!/usr/bin/env python3
"""tools/data/fetch_coindcx_last_24h.py — Fetch last 24h of 1m/15m OHLCV data for all CoinDCX coins.

Fetches 1,440 1m candles + 96 15m candles with volume for all active perpetual futures coins.
Stores the raw dataset in SQLite at datasets/coindcx_last_24h.db.
"""
import os
import sys
import time
import sqlite3
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

DB_PATH = "datasets/coindcx_last_24h.db"
os.makedirs("datasets", exist_ok=True)

# 1. Fetch active futures instruments from CoinDCX
print("Fetching active CoinDCX instruments list...")
try:
    resp = requests.get("https://api.coindcx.com/exchange/v1/derivatives/futures/data/active_instruments", timeout=10)
    pairs = resp.json() if resp.status_code == 200 else []
except Exception as e:
    print(f"Error fetching active instruments: {e}")
    pairs = []

if not pairs:
    # Fallback to current prices endpoint
    try:
        r2 = requests.get("https://public.coindcx.com/market_data/v3/current_prices/futures/rt", timeout=10)
        d = r2.json().get("prices", {})
        pairs = [p for p in d.keys() if p.startswith("B-") and p.endswith("_USDT")]
    except Exception as e:
        print(f"Fallback error: {e}")

bases = sorted(list(set([p.replace("B-", "").replace("_USDT", "") for p in pairs if "USDT" in p])))
print(f"Found {len(bases)} active perpetual futures coins on CoinDCX.")

# Initialize SQLite database
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute("PRAGMA journal_mode = WAL;")
cursor.execute("PRAGMA synchronous = NORMAL;")
cursor.execute("""
CREATE TABLE IF NOT EXISTS klines_1m (
    symbol TEXT,
    timestamp INTEGER,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    PRIMARY KEY (symbol, timestamp)
);
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS klines_15m (
    symbol TEXT,
    timestamp INTEGER,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    PRIMARY KEY (symbol, timestamp)
);
""")
conn.commit()

def fetch_coin_candles(base):
    # Fetch 1m candles (limit 1440 for 24 hours)
    m1_data = []
    m15_data = []
    
    # Try Binance Futures API first (direct fast source matching CoinDCX perp liquidity)
    try:
        url_binance = f"https://fapi.binance.com/fapi/v1/klines?symbol={base}USDT&interval=1m&limit=1440"
        res = requests.get(url_binance, timeout=6)
        if res.status_code == 200:
            rows = res.json()
            if isinstance(rows, list) and len(rows) > 0:
                m1_data = [(base, int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]
    except Exception:
        pass
        
    # If Binance fails or coin is exclusive, try CoinDCX candles endpoint
    if not m1_data:
        try:
            url_coindcx = f"https://public.coindcx.com/market_data/candles?pair=B-{base}_USDT&interval=1m&limit=1440"
            res = requests.get(url_coindcx, timeout=6)
            if res.status_code == 200:
                rows = res.json()
                if isinstance(rows, list) and len(rows) > 0:
                    m1_data = [(base, int(r['time']), float(r['open']), float(r['high']), float(r['low']), float(r['close']), float(r['volume'])) for r in rows]
        except Exception:
            pass
            
    # Also fetch 15m candles
    try:
        url_binance_15m = f"https://fapi.binance.com/fapi/v1/klines?symbol={base}USDT&interval=15m&limit=96"
        res = requests.get(url_binance_15m, timeout=6)
        if res.status_code == 200:
            rows = res.json()
            if isinstance(rows, list) and len(rows) > 0:
                m15_data = [(base, int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]
    except Exception:
        pass
        
    if not m15_data and m1_data:
        # Aggregate from 1m
        tf_ms = 15 * 60 * 1000
        cur_bar = None
        for r in m1_data:
            _, ts, o, h, l, c, v = r
            b_ts = (ts // tf_ms) * tf_ms
            if cur_bar is None or cur_bar[1] != b_ts:
                if cur_bar: m15_data.append(cur_bar)
                cur_bar = [base, b_ts, o, h, l, c, v]
            else:
                if h > cur_bar[3]: cur_bar[3] = h
                if l < cur_bar[4]: cur_bar[4] = l
                cur_bar[5] = c
                cur_bar[6] += v
        if cur_bar: m15_data.append(cur_bar)
        
    return base, m1_data, m15_data

print(f"Downloading last 24 hours of 1m & 15m candles across {len(bases)} coins...")
t0 = time.time()
successful = 0
total_1m = 0
total_15m = 0

with ThreadPoolExecutor(max_workers=30) as executor:
    futures = {executor.submit(fetch_coin_candles, base): base for base in bases}
    for fut in as_completed(futures):
        base = futures[fut]
        try:
            coin, m1_rows, m15_rows = fut.result()
            if m1_rows:
                cursor.executemany("INSERT OR REPLACE INTO klines_1m VALUES (?, ?, ?, ?, ?, ?, ?)", m1_rows)
                total_1m += len(m1_rows)
            if m15_rows:
                cursor.executemany("INSERT OR REPLACE INTO klines_15m VALUES (?, ?, ?, ?, ?, ?, ?)", m15_rows)
                total_15m += len(m15_rows)
            if m1_rows or m15_rows:
                successful += 1
        except Exception as e:
            pass

conn.commit()
conn.close()

print(f"\n✅ Download Complete in {time.time()-t0:.2f}s!")
print(f"  • Successfully Fetched Coins: {successful}/{len(bases)}")
print(f"  • Total 1-Minute Candles   : {total_1m:,}")
print(f"  • Total 15-Minute Candles  : {total_15m:,}")
print(f"  • Saved Locally to Database: {DB_PATH}")
