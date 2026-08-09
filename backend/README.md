# AI Prosperity — Backend (API + Execution Worker)

FastAPI API (Phase 2) + multi-tenant execution worker (Phase 3) in one package
(`app/`), sharing core (db, models, crypto, packages). Monolith-first; split into
separate deployables later if needed.

## Setup

```bash
cd aiprosperity/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # fill DATABASE_URL, JWT_SECRET, SECRET_ENCRYPTION_KEY, Dodo, etc.
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # -> SECRET_ENCRYPTION_KEY
python init_db.py               # create tables (needs Postgres reachable)
```

## Run

```bash
# API  (Caddy proxies app.diffraction.in/api/* -> :8000)
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Execution worker (separate process)
python -m app.worker
```

## What each module does

| Module | Role |
|---|---|
| `config.py` | env-driven settings |
| `db.py` / `models.py` | Postgres (SQLAlchemy 2.0) + schema |
| `crypto.py` | Fernet encryption of client API secrets at rest (KMS-ready) |
| `packages.py` | server-side package rules (mirror of frontend) |
| `auth.py` | email-OTP + JWT |
| `delta.py` | **per-client** Delta client — key-parameterized, contract-size safe; self-contained (does not touch the brain's adapter) |
| `sizing.py` | 2% risk → coin size |
| `entitlement.py` | weekly package quota gate |
| `executor.py` | per-client pipeline: dedupe → gate → size → order → SL → record |
| `worker.py` | fan-out loop + lapse force-close sweep |
| `signal_bus.py` | brain publishes signals here |
| `main.py` | FastAPI: auth, /me, /connect, /checkout, Dodo webhook, /admin, /internal/signals |

## API surface (for the frontend)

| Method | Path | Auth |
|---|---|---|
| POST | `/auth/request-otp` · `/auth/verify-otp` | public |
| GET | `/me` | bearer |
| POST | `/connect` · `/connection/pause` · `/disconnect` | bearer |
| GET | `/packages` | public |
| POST | `/checkout` | bearer → Dodo checkout url |
| POST | `/webhooks/dodo` | signature-verified |
| GET | `/admin/clients` | admin |
| POST | `/internal/signals` | `x-internal-token` (brain) |

## Brain integration (Phase 4)

The go-trader brain already emits `{symbol, side, stop_loss_price}`. Wire it to:

```
POST /internal/signals
  headers: x-internal-token: $INTERNAL_SIGNAL_TOKEN
  body: {"symbol":"BTC","side":"buy","sl_price":72180,"ref_price":73420}
```

The worker fans each signal out to all eligible clients. **Confirm before go-live:**
the static egress IP (clients whitelist it), the Dodo product ids + webhook secret,
and the `SECRET_ENCRYPTION_KEY` living in a real secret store.
