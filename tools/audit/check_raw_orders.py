import os
import sys
import json

# Add path for adapter
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'platforms', 'binance'))
from adapter import BinanceExchangeAdapter

def check_raw_orders():
    os.environ["BINANCE_SANDBOX"] = "0"
    try:
        adapter = BinanceExchangeAdapter()
        symbol = "BTCUSDT"
        print(f"Fetching RAW Open Orders for {symbol} Futures...")
        orders = adapter._exchange.fapiPrivateGetOpenOrders({"symbol": symbol})
        print(json.dumps(orders, indent=2))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_raw_orders()
