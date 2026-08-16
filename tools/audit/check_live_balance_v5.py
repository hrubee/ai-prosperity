import os
import sys
import ccxt

# Add path for adapter
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'platforms', 'binance'))
from adapter import BinanceExchangeAdapter

def check_live_balance_debug_v5():
    os.environ["BINANCE_SANDBOX"] = "0"
    try:
        adapter = BinanceExchangeAdapter()
        ex = adapter._exchange
        
        print(f"CCXT Version: {ccxt.__version__}")
        
        # Method 1: fetch_balance with type=future
        try:
            print("\nMethod 1: fetch_balance(type=future)...")
            bal = ex.fetch_balance({'type': 'future'})
            for asset, val in bal.get('total', {}).items():
                if val > 0:
                    print(f"Future - {asset}: {val}")
        except Exception as e:
            print(f"Method 1 Error: {e}")

        # Method 2: fapiPrivateGetAccount (Direct)
        try:
            print("\nMethod 2: fapiPrivateGetAccount()...")
            info = ex.fapiPrivateGetAccount()
            print("Method 2 Success")
        except Exception as e:
            print(f"Method 2 Error: {e}")

        # Method 3: dapi (Coin-M) just in case
        try:
            print("\nMethod 3: dapiPrivateGetAccount()...")
            info = ex.dapiPrivateGetAccount()
            print("Method 3 Success (Coin-M)")
        except Exception as e:
            print(f"Method 3 Error: {e}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_live_balance_debug_v5()
