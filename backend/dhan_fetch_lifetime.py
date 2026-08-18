import os, requests, json
from dotenv import load_dotenv
load_dotenv('/root/aiprosperity/backend/.env')
cid = os.getenv('DHAN_CLIENT_ID', '')
token = os.getenv('DHAN_ACCESS_TOKEN', '')

headers = {
    'access-token': token,
    'client-id': cid,
    'Content-Type': 'application/json',
    'Accept': 'application/json'
}

all_trades = []

# Fetch historical trades across 2024, 2025, 2026
for year in [2024, 2025, 2026]:
    for half in [(f'{year}-01-01', f'{year}-06-30'), (f'{year}-07-01', f'{year}-12-31')]:
        start_d, end_d = half
        page = 0
        while True:
            url = f'https://api.dhan.co/v2/trades/{start_d}/{end_d}/{page}'
            try:
                r = requests.get(url, headers=headers, timeout=10)
                if r.status_code != 200:
                    break
                data = r.json()
                if not isinstance(data, list) or len(data) == 0:
                    break
                all_trades.extend(data)
                page += 1
            except Exception:
                break

# Also fetch today's trades
try:
    r_today = requests.get('https://api.dhan.co/v2/trades', headers=headers, timeout=10)
    if r_today.status_code == 200:
        t_data = r_today.json()
        if isinstance(t_data, list):
            for td in t_data:
                all_trades.append(td)
except Exception:
    pass

with open('/root/aiprosperity/backend/dhan_all_lifetime_trades_raw.json', 'w') as f:
    json.dump(all_trades, f)
print(f'DONE:{len(all_trades)}')
