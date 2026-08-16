import requests, pandas as pd
url = "https://fapi.binance.com/fapi/v1/klines?symbol=BMTUSDT&interval=15m&limit=1500"
r = requests.get(url)
data = r.json()
df = pd.DataFrame(data, columns=["t","o","h","l","c","v","ct","qav","num","tbbav","tbqav","ignore"])
df['t'] = pd.to_datetime(df['t'], unit='ms')
for col in ["o","h","l","c","v"]:
    df[col] = df[col].astype(float)
mask = (df['t'] >= '2026-08-09 11:30:00') & (df['t'] <= '2026-08-09 18:30:00')
print(df[mask][['t', 'o', 'h', 'l', 'c', 'v']].to_string(index=False))
