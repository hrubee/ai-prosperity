import os
import sys
import json

# Add path for adapter
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'platforms', 'binance'))
from adapter import BinanceExchangeAdapter

def check_live_balance():
    os.environ["BINANCE_SANDBOX"] = "0"
    try:
        adapter = BinanceExchangeAdapter()
        # Check Spot
        spot_balance = adapter._exchange.fetch_balance()
        print("--- Spot Balance ---")
        for asset, val in spot_balance.get("total", {}).items():
            if val > 0:
                print(f"{asset}: {val}")
        
        # Check Futures
        try:
            futures_balance = adapter._exchange.fetch_balance(params={"type": "future"})
            print("\n--- Futures Balance ---")
            for asset, val in futures_balance.get("total", {}).items():
                if val > 0:
                    print(f"{asset}: {val}")
        except Exception as e:
            print(f"\nCould not fetch Futures balance: {e}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_live_balance()
