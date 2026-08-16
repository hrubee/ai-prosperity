import os
import sys
import json

# Add path for adapter
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'platforms', 'binance'))
from adapter import BinanceExchangeAdapter

def check_orders():
    os.environ["BINANCE_SANDBOX"] = "0"
    try:
        adapter = BinanceExchangeAdapter()
        print("Fetching Open Orders for BTC/USDT Futures...")
        orders = adapter._exchange.fetch_open_orders("BTC/USDT", params={"type": "future"})
        for o in orders:
            print(f"Order: {o['id']} | Type: {o['type']} | Side: {o['side']} | Qty: {o['amount']} | StopPx: {o.get('stopPrice', 'N/A')}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_orders()
