#!/usr/bin/env python3
import os, sys, json, urllib.request, hmac, hashlib

def load_env_file(path):
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")

load_env_file('/root/trading-bot/crypto/.env')

key1 = os.environ.get('COINDCX_LIVE_API_KEY') or os.environ.get('COINDCX_API_KEY')
secret1 = os.environ.get('COINDCX_LIVE_API_SECRET') or os.environ.get('COINDCX_API_SECRET')

key2 = os.environ.get('COINDCX_KEY_2') or os.environ.get('COINDCX_ACCOUNT2_API_KEY')
secret2 = os.environ.get('COINDCX_SECRET_2') or os.environ.get('COINDCX_ACCOUNT2_API_SECRET')

def fetch_fills(key, secret):
    if not key or not secret: return []
    url = 'https://api.coindcx.com/exchange/v1/derivatives/futures/trades'
    all_trades = []
    for page in range(1, 25):
        body = {'page': str(page), 'size': '100'}
        json_body = json.dumps(body, separators=(',', ':'))
        sig = hmac.new(secret.encode('utf-8'), json_body.encode('utf-8'), hashlib.sha256).hexdigest()
        headers = {'Content-Type': 'application/json', 'X-AUTH-APIKEY': key, 'X-AUTH-SIGNATURE': sig, 'User-Agent': 'Mozilla/5.0'}
        req = urllib.request.Request(url, data=json_body.encode('utf-8'), headers=headers)
        try:
            res = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
            if isinstance(res, list) and len(res) > 0:
                all_trades.extend(res)
            else:
                break
        except Exception:
            break
    return all_trades

def audit_pair(name, fills):
    hei_fills = [f for f in fills if 'HEI' in (f.get('pair') or '').upper()]
    print(f'=== {name} HEI TRADES ===')
    if not hei_fills:
        print('No HEI trades found.\n')
        return 0.0
    buys = [f for f in hei_fills if f.get('side') == 'buy']
    sells = [f for f in hei_fills if f.get('side') == 'sell']
    b_qty = sum(float(f.get('quantity', 0)) for f in buys)
    b_vol = sum(float(f.get('quantity', 0)) * float(f.get('price', 0)) for f in buys)
    b_vwap = b_vol / b_qty if b_qty > 0 else 0.0
    s_qty = sum(float(f.get('quantity', 0)) for f in sells)
    s_vol = sum(float(f.get('quantity', 0)) * float(f.get('price', 0)) for f in sells)
    s_vwap = s_vol / s_qty if s_qty > 0 else 0.0
    fee_usd = sum(float(f.get('fee_amount', 0) or f.get('fee', 0)) for f in hei_fills)
    matched = min(b_qty, s_qty)
    gross_pnl = (s_vwap - b_vwap) * matched if matched > 0 else 0.0
    net_pnl = gross_pnl - fee_usd
    net_inr = net_pnl * 86.0
    print(f'Total HEI Fills: {len(hei_fills)}')
    print(f'Bought: {b_qty:.1f} @ VWAP {b_vwap:.5f}')
    print(f'Sold:   {s_qty:.1f} @ VWAP {s_vwap:.5f}')
    print(f'Fees:   ${fee_usd:.4f} USD')
    print(f'Net Realized PnL: ${net_pnl:+.2f} USD | Rs.{net_inr:+.2f} INR\n')
    return net_pnl

p1 = audit_pair('ACCOUNT 1 (Primary)', fetch_fills(key1, secret1))
p2 = audit_pair('ACCOUNT 2 (Secondary)', fetch_fills(key2, secret2))
tot = p1 + p2
print('==================================================')
print(f'💥 COMBINED HEI NET REALIZED PnL: ${tot:+.2f} USD | Rs.{tot*86.0:+.2f} INR')
print('==================================================')
