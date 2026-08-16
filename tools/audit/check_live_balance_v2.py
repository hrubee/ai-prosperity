import os
import sys
import json

# Add path for adapter
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'platforms', 'binance'))
from adapter import BinanceExchangeAdapter

def check_live_balance_debug():
    os.environ["BINANCE_SANDBOX"] = "0"
    try:
        adapter = BinanceExchangeAdapter()
        # Fetch full balance
        print("Fetching Spot balance...")
        bal = adapter._exchange.fetch_balance()
        
        print("\n--- Assets with any balance ---")
        found = False
        for asset, data in bal.items():
            if asset in ('info', 'free', 'used', 'total', 'timestamp', 'datetime'):
                continue
            total = data.get('total', 0)
            if total > 0:
                print(f"{asset}: {total}")
                found = True
        
        if not found:
            print("No assets found in Spot wallet.")
            
        # Try to check Funding wallet if permissions allow
        try:
            print("\nAttempting to fetch Funding balance...")
            funding = adapter._exchange.fetch_balance(params={"type": "funding"})
            for asset, val in funding.get("total", {}).items():
                if val > 0:
                    print(f"Funding - {asset}: {val}")
        except Exception as e:
            print(f"Could not fetch Funding wallet: {e}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_live_balance_debug()
