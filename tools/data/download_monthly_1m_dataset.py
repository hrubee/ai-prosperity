#!/usr/bin/env python3
"""tools/data/download_monthly_1m_dataset.py — High-Speed Parallel Downloader for 1-Minute Historical Binance Archives.

Downloads complete 1-minute monthly klines for all active perpetual coins for:
- May 2026 (2026-05) -> datasets/may_2026_1m.db
- July 2026 (2026-07) -> datasets/july_2026_1m.db
- April 2026 (2026-04) -> datasets/april_2026_1m.db
"""
import os
import sys
import io
import ssl
import csv
import time
import zipfile
import sqlite3
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

DB_SYMBOLS_PATH = "datasets/candles_4h_april_july.db"
OUTPUT_DIR = "datasets"

ctx = ssl._create_unverified_context()

def get_symbols():
    conn = sqlite3.connect(DB_SYMBOLS_PATH)
    cursor = conn.cursor()
    symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM candles_4h").fetchall()]
    conn.close()
    
    # Ensure USDT suffix
    clean_syms = []
    for s in symbols:
        if not s.endswith("USDT"):
            clean_syms.append(f"{s}USDT")
        else:
            clean_syms.append(s)
    return sorted(list(set(clean_syms)))

def download_and_extract_symbol(sym, month_str):
    # e.g. sym = "BTCUSDT", month_str = "2026-05"
    url = f"https://data.binance.vision/data/futures/um/monthly/klines/{sym}/1m/{sym}-1m-{month_str}.zip"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            if resp.status == 200:
                data = resp.read()
                z = zipfile.ZipFile(io.BytesIO(data))
                fname = z.namelist()[0]
                content = z.read(fname).decode("utf-8")
                
                rows = []
                reader = csv.reader(content.splitlines())
                for r in reader:
                    if not r or r[0].startswith("open_time") or r[0].startswith("timestamp"):
                        continue
                    try:
                        ts = int(r[0])
                        o = float(r[1])
                        h = float(r[2])
                        l = float(r[3])
                        c = float(r[4])
                        v = float(r[5])
                        rows.append((sym, ts, o, h, l, c, v))
                    except Exception:
                        continue
                return sym, rows
    except Exception:
        return sym, None
    return sym, None

def build_month_database(month_str, db_name, symbols):
    db_path = os.path.join(OUTPUT_DIR, db_name)
    if os.path.exists(db_path):
        os.remove(db_path)
        
    print("="*80)
    print(f"📥 DOWNLOADING 1-MINUTE CANDLES FOR {month_str} -> {db_path}")
    print("="*80)
    
    t0 = time.time()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE klines_1m (
            symbol TEXT,
            timestamp INTEGER,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL
        )
    """)
    conn.commit()
    
    total_bars = 0
    successful_syms = 0
    
    with ThreadPoolExecutor(max_workers=35) as executor:
        futures = {executor.submit(download_and_extract_symbol, s, month_str): s for s in symbols}
        for fut in as_completed(futures):
            sym, rows = fut.result()
            if rows:
                cursor.executemany("INSERT INTO klines_1m VALUES (?,?,?,?,?,?,?)", rows)
                total_bars += len(rows)
                successful_syms += 1
                if successful_syms % 25 == 0 or successful_syms == len(symbols):
                    conn.commit()
                    print(f"   ✓ [{month_str}] {successful_syms}/{len(symbols)} coins downloaded ({total_bars:,} bars)...")
                    
    conn.commit()
    
    print(f"🔨 Building index on {db_name}(symbol, timestamp)...")
    cursor.execute("CREATE INDEX idx_sym_ts ON klines_1m(symbol, timestamp)")
    conn.commit()
    conn.close()
    
    print(f"✅ Finished {month_str}: {successful_syms} coins, {total_bars:,} 1m bars in {time.time()-t0:.1f}s.")
    print(f"   Database File Size: {os.path.getsize(db_path) / (1024*1024):.1f} MB\n")

def main():
    symbols = get_symbols()
    print(f"Targeting {len(symbols)} perpetual symbols from candles_4h_april_july.db\n")
    
    # Download May 2026 and July 2026 (the other 2 months requested)
    build_month_database("2026-05", "may_2026_1m.db", symbols)
    build_month_database("2026-07", "july_2026_1m.db", symbols)
    build_month_database("2026-04", "april_2026_1m.db", symbols)

if __name__ == "__main__":
    main()
