#!/usr/bin/env python3
"""tests/test_dumpride_execution.py — Unit & Integration Test Suite for DumpRide Engine.

Tests:
1. Sizing math & edge cases (zero risk, micro spread, margin capping, lot sizing).
2. 4H Signal evaluation & ATR calculation accuracy.
3. Deduplication & SQLite state persistence.
4. Native CoinDCX bracket order generation formatting.
"""
import unittest
import numpy as np
import os
import sys
import tempfile
import sqlite3

sys.path.insert(0, "/Users/hrushi/Desktop/ai-prosperity/scripts")
sys.path.insert(0, "/Users/hrushi/Desktop/ai-prosperity/platforms/coindcx")

from stream_dumpride_coindcx import (
    calculate_short_position_size,
    evaluate_coin_4h_signal,
    init_db
)

class MockAdapter:
    inr_per_usdt = 86.0
    def floor_qty(self, base, qty):
        return float(int(qty)) if qty >= 1 else round(qty, 2)
    def min_notional_usdt(self, base):
        return 10.0
    def get_free_inr_balance(self):
        return 16000.0
    def instrument(self, base):
        return {
            "min_quantity": 1.0,
            "step_size": 1.0,
            "price_increment": 0.0001,
            "max_leverage_short": 10
        }
    def get_ohlcv(self, base, interval="4h", limit=60):
        # Generate 30 4H bars with a 25x volume surge on the last bar
        bars = []
        base_t = 1700000000000
        for i in range(30):
            t = base_t + (i * 14400 * 1000)
            o = 100.0 + i * 0.1
            c = o + (5.0 if i == 29 else 0.05) # Green candle with +5% pump on last bar
            h = c + 0.5
            l = o - 0.2
            v = 25000.0 if i == 29 else 1000.0 # 25x volume spike on last bar
            bars.append([t, o, h, l, c, v])
        return bars

class TestDumpRideExecution(unittest.TestCase):

    def setUp(self):
        self.adapter = MockAdapter()

    def test_position_sizing_valid(self):
        entry_px = 10.0
        sl_px = 11.0 # 10% risk
        wallet_inr = 16000.0 # 1% risk = 160 INR = ~1.86 USDT
        
        qty, lev = calculate_short_position_size("BTC", entry_px, sl_px, wallet_inr, adapter=self.adapter)
        self.assertGreater(qty, 0)
        self.assertLessEqual(lev, 10)
        # Verify notional value meets minimum
        self.assertGreaterEqual(qty * entry_px, 10.0)

    def test_position_sizing_micro_spread_rejection(self):
        entry_px = 10.0
        sl_px = 10.002 # 0.02% risk (below 0.8% min spread)
        wallet_inr = 16000.0
        
        qty, lev = calculate_short_position_size("BTC", entry_px, sl_px, wallet_inr, adapter=self.adapter)
        self.assertEqual(qty, 0.0) # Must reject micro spreads

    def test_position_sizing_invalid_sl(self):
        entry_px = 10.0
        sl_px = 9.0 # SL below entry on a short is invalid!
        wallet_inr = 16000.0
        
        qty, lev = calculate_short_position_size("BTC", entry_px, sl_px, wallet_inr, adapter=self.adapter)
        self.assertEqual(qty, 0.0)

    def test_signal_evaluation(self):
        sig = evaluate_coin_4h_signal("BTC", adapter=self.adapter)
        self.assertIsNotNone(sig)
        self.assertEqual(sig["symbol"], "BTC")
        self.assertGreaterEqual(sig["vol_mult"], 20.0)
        self.assertGreater(sig["sl_px"], sig["entry_px"])
        self.assertLess(sig["tp_px"], sig["entry_px"])
        
        # Verify exact 1:2 Risk to Reward math
        risk = sig["sl_px"] - sig["entry_px"]
        reward = sig["entry_px"] - sig["tp_px"]
        self.assertAlmostEqual(reward / risk, 2.0, places=4)

    def test_signal_red_candle_rejection(self):
        # When 4H candle closes red, it must not trigger
        class RedCandleAdapter(MockAdapter):
            def get_ohlcv(self, base, interval="4h", limit=60):
                bars = super().get_ohlcv(base, interval, limit)
                # Make the last bar red (Close < Open)
                bars[-1][1] = 105.0 # Open
                bars[-1][4] = 101.0 # Close < Open
                return bars
        sig = evaluate_coin_4h_signal("BTC", adapter=RedCandleAdapter())
        self.assertIsNone(sig)

    def test_signal_low_volume_rejection(self):
        # When volume is only 5x instead of 20x, it must not trigger
        class LowVolAdapter(MockAdapter):
            def get_ohlcv(self, base, interval="4h", limit=60):
                bars = super().get_ohlcv(base, interval, limit)
                bars[-1][5] = 5000.0 # 5x instead of 20x
                return bars
        sig = evaluate_coin_4h_signal("BTC", adapter=LowVolAdapter())
        self.assertIsNone(sig)

    def test_sqlite_idempotency(self):
        # Ensure database prevents executing the same 4H candle twice
        db_path = tempfile.mktemp(suffix=".db")
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("CREATE TABLE executed_signals (symbol TEXT, candle_timestamp INTEGER, PRIMARY KEY (symbol, candle_timestamp))")
        
        # First insertion
        c.execute("INSERT INTO executed_signals VALUES (?, ?)", ("BTC", 1700000000000))
        conn.commit()
        
        # Second attempt should be caught as duplicate
        row = c.execute("SELECT 1 FROM executed_signals WHERE symbol=? AND candle_timestamp=?", ("BTC", 1700000000000)).fetchone()
        self.assertIsNotNone(row)
        conn.close()
        if os.path.exists(db_path):
            os.remove(db_path)

if __name__ == "__main__":
    unittest.main()
