#!/usr/bin/env python3
"""test_live_wallet_read.py — Verify exact CoinDCX Live Wallet API Balance Reading.
"""
import sys
import os
import json

sys.path.insert(0, ".")
from shared_scripts.stream_fibvol_coindcx import load_env, get_wallet_usdt, calculate_position_size, A
load_env()

def main():
    real_inr = A.get_inr_equity()
    conv_rate = 86.0  # Market USD/INR rate
    wallet_usdt = real_inr / conv_rate

    print("=== LIVE COINDCX WALLET API READ ===")
    print(f"Direct CoinDCX Balances API Endpoint: https://api.coindcx.com/exchange/v1/users/balances")
    print(f"Direct CoinDCX Real INR Wallet Equity: ₹{real_inr:.2f} INR")
    print(f"CoinDCX Settlement Conversion Rate: {conv_rate:.2f} INR / USDT")
    print(f"Calculated Wallet Balance in USDT: ${wallet_usdt:.2f} USDT")
    print(f"1% Risk Capital for Next Trade: ${wallet_usdt * 0.01:.2f} USDT (₹{wallet_usdt * 0.01 * conv_rate:.2f} INR)")

    # Sample position calculation
    qty = calculate_position_size("GPS", 0.00974, 0.009616, wallet_usdt)
    print(f"\nCalculated Position Qty for 1% Risk on GPS: {qty} GPS (Notional Value: ${qty * 0.00974:.2f})")

if __name__ == "__main__":
    main()
