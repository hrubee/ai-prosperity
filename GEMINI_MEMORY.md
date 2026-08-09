# AI Prosperity - System Memory & Architecture

## Overview
This repository contains the completely unified and consolidated **AI Prosperity (Dhan Copier)** project. It runs entirely within the `backend/`, `frontend/`, and `worker/` structure.

**Important Note for Future Agents:** The AI Prosperity project previously ran as fragmented microservices (`tradejini-copier`, `tradejini-poller`, `client-trade-watch`, etc). This is NO LONGER the case. Do not attempt to revive or edit those separate scripts. Everything is now natively integrated.

## Core Architecture

### 1. Backend (`backend/`)
The primary FastAPI application running on port 8000.
- **`app/main.py`**: The entrypoint. It mounts all routers and starts the background tasks.
- **`app/copier.py`**: Handles incoming webhooks from Dhan, verifies `Subscription.is_active`, restricts trades for expired clients, and translates valid trades into Tradejini API calls (`place_order`, `modify_order`, `cancel_order`).
- **`app/dhan_poller.py`**: A native `asyncio` background task started via `app.on_event("startup")` in `main.py`. It constantly polls the Dhan API for executed orders and feeds them to the execution pipeline, bridging missing/delayed webhooks.
- **`app/xts_interactive_ws.py`**: Connects to the XTS Interactive WebSocket for all active users upon startup, broadcasting real-time Tradejini order updates to the frontend dashboard.
- **`app/executor.py` / `app/worker.py`**: Standard execution pipeline for other platform features, although the Copier largely bypasses this by utilizing direct Tradejini API calls inside `copier.py` for lower latency.

### 2. Frontend (`frontend/`)
The Next.js React application.
- **`app/admin/ManageDrawer.tsx`**: Admin control panel for managing clients. Admins can delete accounts, archive users, and manage subscriptions (extending or expiring validity).
- **Client Dashboard**: Clients can connect their Tradejini credentials and monitor mirrored Dhan trades (now properly written to the `ClientOrder` database table).

## Key Constraints & Rules
- **Subscription Checks**: The system is designed to STRICTLY respect the `current_period_end` of a `Subscription`. If a client's subscription expires, `copier.py` will block all new entry trades but will allow exit trades to prevent them from being stuck in a position.
- **Database Modularity**: All services use `db.session_scope()`. Ensure database locks are minimized during high-frequency polling.
- **Scope**: The AI Prosperity project ONLY operates the Dhan Copier mechanism. Do not build unrelated strategies (like vol2b2t) into this specific workflow, unless explicitly instructed.

## Deployment
The backend, frontend, and worker are run as systemd services on the VPS (`187.127.132.39`).
- `systemctl restart aiprosperity-backend`
- `systemctl restart aiprosperity-frontend`
- `systemctl restart aiprosperity-worker`

## Where to Pick Up From
If you are reading this file at the start of a new session, you are working in the unified repository. Review `app/copier.py` and `app/dhan_poller.py` to understand the primary execution flows for the copier before making any architectural changes.
