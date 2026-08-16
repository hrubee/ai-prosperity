import os
import sys
import json

# Add path for adapter
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'platforms', 'binance'))
from adapter import BinanceExchangeAdapter

def check_live_balance_v3():
    os.environ["BINANCE_SANDBOX"] = "0"
    try:
        adapter = BinanceExchangeAdapter()
        
        # Check Spot
        print("Checking Spot balance...")
        spot = adapter._exchange.fetch_balance(params={"type": "spot"})
        for asset, val in spot.get("total", {}).items():
            if val > 0:
                print(f"Spot - {asset}: {val}")
        
        # Check Futures
        print("\nChecking Futures balance...")
        try:
            futures = adapter._exchange.fetch_balance(params={"type": "future"})
            for asset, val in futures.get("total", {}).items():
                if val > 0:
                    print(f"Futures - {asset}: {val}")
        except Exception as e:
            print(f"Could not fetch Futures balance: {e}")
            
        # Check Funding
        print("\nChecking Funding balance...")
        try:
            funding = adapter._exchange.fetch_balance(params={"type": "funding"})
            for asset, val in funding.get("total", {}).items():
                if val > 0:
                    print(f"Funding - {asset}: {val}")
        except Exception as e:
            print(f"Could not fetch Funding balance: {e}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_live_balance_v3()
