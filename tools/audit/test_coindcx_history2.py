import requests
url = "https://public.coindcx.com/market_data/candles?pair=B-BTC_USDT&interval=1d&limit=5&startTime=1704067200000"
r = requests.get(url)
print(r.json())

url2 = "https://api.coindcx.com/exchange/v1/derivatives/futures/data/candles?pair=B-BTC_USDT&interval=1d&limit=5"
r2 = requests.get(url2)
print("api.coindcx.com result:", r2.status_code)
