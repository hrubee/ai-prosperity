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

def get_dhan_headers():
    with SessionLocal() as db:
        conn = db.query(DhanConnection).filter(
            DhanConnection.status == "connected",
            DhanConnection.access_token_encrypted.isnot(None),
            DhanConnection.access_token_encrypted != ""
        ).order_by(DhanConnection.id.desc()).first()
        if not conn or not conn.access_token_encrypted:
            return None
        
        try:
            token = decrypt_secret(conn.access_token_encrypted)
            client_id = conn.client_id
            return {
                "access-token": token,
                "client-id": client_id,
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
        except:
            return None

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
    
    first_boot = len(seen_orders) == 0
    last_heartbeat = 0

    async with httpx.AsyncClient() as client:
        while True:
            try:
                headers = get_dhan_headers()
                if not headers:
                    await asyncio.sleep(5.0)
                    continue

                resp = await client.get("https://api.dhan.co/v2/orders", headers=headers, timeout=5.0)
                
                if resp.status_code == 200:
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
                                if first_boot:
                                    order_copy = dict(order)
                                    order_copy["is_historic"] = True
                                    new_executions.append((state_key, order_copy))
                                else:
                                    new_executions.append((state_key, order))
                    
                    if first_boot:
                        save_seen_orders(seen_orders)
                        log_msg(f"First boot: Pre-filled {len(seen_orders)} terminal orders.")
                        first_boot = False
                    
                    if time.time() - last_heartbeat > 60:
                        try:
                            await client.post(WEBHOOK_URL, json={"type": "status", "message": f"Dhan Poller Active: Monitoring master orders... (Seen: {len(seen_orders)})"}, timeout=5.0)
                            last_heartbeat = time.time()
                        except Exception as we:
                            log_msg(f"Heartbeat webhook failed: {we}")
                    
                    for state_key, order_data in new_executions:
                        log_msg(f"Detected new state: {state_key}. Forwarding to unified backend webhook...")
                        try:
                            wh_resp = await client.post(WEBHOOK_URL, json=order_data, timeout=5.0)
                            if wh_resp.status_code < 500:
                                seen_orders.add(state_key)
                                save_seen_orders(seen_orders)
                        except Exception as we:
                            log_msg(f"Failed to forward webhook: {we}")
                else:
                    log_msg(f"Dhan API Error: {resp.status_code} - {resp.text}")
                    await asyncio.sleep(5.0)
                
            except Exception as e:
                log_msg(f"Exception in poller loop: {e}")
                
            await asyncio.sleep(POLL_INTERVAL_SEC)
