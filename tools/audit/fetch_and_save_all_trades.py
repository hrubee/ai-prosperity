import os
import sys
import json
from dotenv import load_dotenv

# Load env from workspace root
load_dotenv()

# Add workspace paths
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'platforms', 'binance'))

from adapter import BinanceExchangeAdapter

def fetch_and_save():
    adapter = BinanceExchangeAdapter()
    exchange = adapter._exchange
    
    symbols = [
        'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'ADA/USDT', 'XRP/USDT', 
        'DOGE/USDT', '1000PEPE/USDT', 'WIF/USDT', 'LINK/USDT', 'DOT/USDT', 'BNB/USDT'
    ]
    
    all_fills = []
    
    for sym in symbols:
        ccxt_sym = adapter._format_symbol(sym, "futures")
        print(f"Fetching trades for {ccxt_sym}...")
        try:
            trades = exchange.fetch_my_trades(ccxt_sym)
            print(f"Fetched {len(trades)} trades for {ccxt_sym}")
            for t in trades:
                fee_cost = 0.0
                fee_currency = 'USDT'
                if t.get('fee'):
                    fee_cost = t['fee'].get('cost', 0.0)
                    fee_currency = t['fee'].get('currency', 'USDT')
                
                # Fetch realizedPnl from exchange info
                realized_pnl = 0.0
                if t.get('info'):
                    realized_pnl = float(t['info'].get('realizedPnl', 0.0))
                
                all_fills.append({
                    'symbol': sym,
                    'id': t.get('id'),
                    'order': t.get('order'),
                    'timestamp': t.get('timestamp'),
                    'datetime': t.get('datetime'),
                    'side': t.get('side').upper() if t.get('side') else '',
                    'price': float(t.get('price', 0.0)),
                    'amount': float(t.get('amount', 0.0)),
                    'cost': float(t.get('cost', 0.0)),
                    'fee_cost': float(fee_cost),
                    'fee_currency': fee_currency,
                    'realized_pnl': realized_pnl
                })
        except Exception as e:
            print(f"Error fetching {ccxt_sym}: {e}")
            
    # Sort chronologically descending
    all_fills.sort(key=lambda x: x['timestamp'] if x['timestamp'] is not None else 0, reverse=True)
    
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'all_fetched_trades.json')
    with open(output_path, 'w') as f:
        json.dump(all_fills, f, indent=2)
        
    print(f"\nDone! Fetched {len(all_fills)} total trades with PnL.")
    print(f"Saved to: {output_path}")

if __name__ == '__main__':
    fetch_and_save()
