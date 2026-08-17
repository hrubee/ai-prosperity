import time
import requests
import json
import os
import datetime
import asyncio
import httpx

from .db import SessionLocal
from .models import DhanConnection
from .crypto import decrypt_secret

POLL_INTERVAL_SEC = 0.3
WEBHOOK_URL = "http://127.0.0.1:8000/api/webhook"
SEEN_ORDERS_FILE = "dhan_poller_seen.json"

def get_dhan_headers(force_db_reload: bool = False):
    token = "" if force_db_reload else os.environ.get("DHAN_ACCESS_TOKEN", "").strip()
    client_id = "" if force_db_reload else os.environ.get("DHAN_CLIENT_ID", "").strip()

    if not token or force_db_reload:
        try:
            with SessionLocal() as db:
                conn = db.query(DhanConnection).filter(
                    DhanConnection.status == "connected",
                    DhanConnection.access_token_encrypted.isnot(None),
                    DhanConnection.access_token_encrypted != ""
                ).order_by(DhanConnection.updated_at.desc()).first()
                if conn and conn.access_token_encrypted:
                    token = decrypt_secret(conn.access_token_encrypted)
                    client_id = conn.client_id
        except Exception as e:
            log_msg(f"DB token fetch error: {e}")

    if not token:
        return None

    headers = {
        "access-token": token,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    if client_id:
        headers["client-id"] = client_id
    return headers

def load_seen_orders():
    if os.path.exists(SEEN_ORDERS_FILE):
        try:
            with open(SEEN_ORDERS_FILE, "r") as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_seen_orders(seen_orders):
    try:
        with open(SEEN_ORDERS_FILE, "w") as f:
            json.dump(list(seen_orders), f)
    except Exception as e:
        pass

def log_msg(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[DhanPoller] {ts} - {msg}", flush=True)

async def run_poller_async():
    seen_orders = load_seen_orders()
    log_msg(f"Started Dhan Poller. Loaded {len(seen_orders)} previously seen executed orders.")
    
    last_heartbeat = 0
    force_db = False

    async with httpx.AsyncClient() as client:
        while True:
            try:
                headers = get_dhan_headers(force_db_reload=force_db)
                if not headers:
                    await asyncio.sleep(5.0)
                    force_db = True
                    continue

                resp = await client.get("https://api.dhan.co/v2/orders", headers=headers, timeout=5.0)
                
                if resp.status_code == 200:
                    force_db = False
                    orders = resp.json()
                    if not isinstance(orders, list):
                        await asyncio.sleep(POLL_INTERVAL_SEC)
                        continue

                    new_executions = []
                    for order in orders:
                        oid = order.get("orderId")
                        status = order.get("orderStatus", "")
                        
                        if not oid:
                            continue
                            
                        if status in ("PENDING", "TRANSFERRED", "OPEN", "MODIFIED", "UPDATED", "TRADED", "CANCELLED", "REJECTED", "FAILED", "EXPIRED", "TRANSIT"):
                            state_key = f"{oid}_{status}"
                            if state_key not in seen_orders:
                                new_executions.append((state_key, order))
                    
                    if time.time() - last_heartbeat > 60:
                        last_heartbeat = time.time()
                        try:
                            await client.post(WEBHOOK_URL, json={"type": "status", "message": f"Dhan Poller Active: Monitoring master orders... (Seen: {len(seen_orders)})"}, timeout=5.0)
                        except Exception as we:
                            log_msg(f"Heartbeat webhook failed: {we}")
                    
                    for state_key, order_data in new_executions:
                        log_msg(f"Detected new Dhan order state: {state_key} ({order_data.get('tradingSymbol')}). Forwarding to backend webhook...")
                        try:
                            wh_resp = await client.post(WEBHOOK_URL, json=order_data, timeout=5.0)
                            if wh_resp.status_code < 500:
                                seen_orders.add(state_key)
                                save_seen_orders(seen_orders)
                        except Exception as we:
                            log_msg(f"Failed to forward webhook: {we}")
                elif resp.status_code == 401:
                    log_msg(f"Dhan API 401 Unauthorized: Access token expired. Forcing DB token reload...")
                    force_db = True
                    await asyncio.sleep(3.0)
                else:
                    log_msg(f"Dhan API Error: {resp.status_code} - {resp.text}")
                    await asyncio.sleep(5.0)
                
            except Exception as e:
                log_msg(f"Exception in poller loop: {e}")
                
            await asyncio.sleep(POLL_INTERVAL_SEC)
