#!/usr/bin/env python3
import os
import sys
import sqlite3
import time
import numpy as np

DB_PATH = "datasets/coindcx_last_24h.db"

from backtest_rr_comparison_1m import resample_bars

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]

raw_m1 = {}
for sym in symbols:
    rows = cursor.execute(
        "SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC",
        (sym,)
    ).fetchall()
    if len(rows) >= 100:
        raw_m1[sym] = rows
conn.close()

print(f"CoinDCX 24h Dataset: {len(raw_m1)} symbols loaded.")
