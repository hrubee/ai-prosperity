import os
import sys
import json
import ccxt

# Add path for adapter
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'platforms', 'binance'))
from adapter import BinanceExchangeAdapter

def check_live_balance_debug_v4():
    os.environ["BINANCE_SANDBOX"] = "0"
    try:
        adapter = BinanceExchangeAdapter()
        ex = adapter._exchange
        
        print(f"Exchange ID: {ex.id}")
        print(f"Base URL: {ex.urls['api']}")
        
        # Test Spot connectivity
        try:
            print("\nTesting Spot (account info)...")
            info = ex.privateGetAccount()
            print("Spot Account Info: Success")
        except Exception as e:
            print(f"Spot Account Info Error: {e}")
            
        # Test Futures connectivity (USD-M)
        try:
            print("\nTesting Futures (fapi account info)...")
            # fapi is for USD-M Futures
            info = ex.fapiPrivateGetAccount()
            print("Futures Account Info: Success")
            
            # Print balances
            for asset in info.get('assets', []):
                margin_balance = float(asset.get('marginBalance', 0))
                if margin_balance > 0:
                    print(f"Futures - {asset['asset']}: {margin_balance}")
        except Exception as e:
            print(f"Futures Account Info Error: {e}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_live_balance_debug_v4()
