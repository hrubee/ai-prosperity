import socketio
import asyncio
import json
from typing import Callable
from sqlalchemy import select

from . import tradejini_auth
from . import db
from .config import settings
from .models import TradejiniConnection

XTS_INTERACTIVE_URL = "https://api.tradejini.com"

async def start_xts_websockets(broadcast_cb: Callable[[str], None]):
    with db.session_scope() as session:
        conns = session.execute(select(TradejiniConnection)).scalars().all()
        clients = []
        for c in conns:
            if not tradejini_auth.has_auto_creds(c) or c.status == "disconnected" or c.paused:
                continue
            try:
                token = tradejini_auth.ensure_client_token(session, c)
                from .tradejini import TradejiniClient
                cl = TradejiniClient(token, api_key=c.api_key)
                clients.append(cl)
            except Exception:
                pass
        
    if not clients:
        await broadcast_cb(json.dumps({"type": "status", "message": "No active Tradejini clients found to monitor."}))
        return

    await broadcast_cb(json.dumps({"type": "status", "message": f"Starting Interactive WebSockets for {len(clients)} accounts."}))

    tasks = []
    for client in clients:
        tasks.append(connect_client_ws(client, broadcast_cb))
        
    await asyncio.gather(*tasks)

async def connect_client_ws(client, broadcast_cb):
    sio = socketio.AsyncClient(reconnection=True, reconnection_attempts=0)
    user_id = getattr(client, 'user_id', client.api_key)

    @sio.event
    async def connect():
        await broadcast_cb(json.dumps({"type": "ws_connected", "account": user_id}))

    @sio.event
    async def disconnect():
        await broadcast_cb(json.dumps({"type": "ws_disconnected", "account": user_id}))

    @sio.on('order')
    async def on_order_update(data):
        await broadcast_cb(json.dumps({"type": "order_update", "account": user_id, "data": data}))

    @sio.on('trade')
    async def on_trade_update(data):
        await broadcast_cb(json.dumps({"type": "trade_update", "account": user_id, "data": data}))

    @sio.on('position')
    async def on_position_update(data):
        await broadcast_cb(json.dumps({"type": "position_update", "account": user_id, "data": data}))
        
    @sio.on('error')
    async def on_error(data):
         await broadcast_cb(json.dumps({"type": "ws_error", "account": user_id, "data": data}))

    try:
        token = getattr(client, 'interactive_token', client.access_token) 
        url = getattr(settings, 'tradejini_base_url', XTS_INTERACTIVE_URL)
        await sio.connect(f"{url}/interactive?token={token}&userID={user_id}&apiType=INTERACTIVE", transports=['websocket'], socketio_path='/socket.io')
        await sio.wait()
    except Exception as e:
        await broadcast_cb(json.dumps({"type": "ws_error", "account": user_id, "message": f"Connection failed: {str(e)}"}))
