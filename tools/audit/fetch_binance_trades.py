import os
import sys
from dotenv import load_dotenv

# Load env from workspace root
load_dotenv()

# Add workspace paths
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'platforms', 'binance'))

from adapter import BinanceExchangeAdapter

def test_fetch():
    adapter = BinanceExchangeAdapter()
    exchange = adapter._exchange
    print(f"Is Live: {adapter.is_live}, Mode: {adapter.mode}")
    print(f"Base URLs: {exchange.urls}")
    
    symbols = [
        'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'ADA/USDT', 'XRP/USDT', 
        'DOGE/USDT', '1000PEPE/USDT', 'WIF/USDT', 'LINK/USDT', 'DOT/USDT', 'BNB/USDT'
    ]
    
    # Try fetching for futures/perps first
    for sym in symbols:
        ccxt_sym = adapter._format_symbol(sym, "futures")
        print(f"Querying CCXT symbol: {ccxt_sym}")
        try:
            trades = exchange.fetch_my_trades(ccxt_sym)
            print(f"Successfully fetched {len(trades)} trades for {ccxt_sym}")
            for t in trades[:3]:
                print(f"  - Order ID: {t.get('order')}, Fill ID: {t.get('id')}, Time: {t.get('datetime')}, Side: {t.get('side')}, Price: {t.get('price')}, Amount: {t.get('amount')}")
        except Exception as e:
            print(f"Error querying {ccxt_sym}: {e}")

if __name__ == '__main__':
    test_fetch()
