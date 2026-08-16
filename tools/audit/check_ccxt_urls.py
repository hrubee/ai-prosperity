import ccxt
exchange = ccxt.binance()
print(f"Public: {exchange.urls['api']['public']}")
print(f"Private: {exchange.urls['api']['private']}")
