# AI Prosperity — Productization Build Plan

> SaaS layer that fans out the existing go-trader "brain" signals to many clients'
> own Delta Exchange (India) accounts. One brain → N client accounts. Sizing and
> stop-loss happen **per client** from their own equity. The brain only decides
> **direction**.

Status: **Phase 0 scaffold** (this folder). The live trading brain in the parent
repo is **untouched** — this is purely additive.

---

## Locked product decisions (2026-05-30)

| Decision | Value |
|---|---|
| Payments | **Dodo Payments** (Merchant of Record — legal seller, sidesteps Indian crypto-gateway bans, handles tax/chargebacks) |
| Risk per trade | **2% of client equity, global** (fixed for everyone) |
| On subscription lapse | **Force-close** the client's open positions (reduce-only), then disconnect |
| Packages | Frequency-gated signal access (see below) |
| Scale target | **~1,000 clients within ~2 months** — design for it now |
| Hosting | **Caddy** on the VPS → `app.diffraction.in` (auto-HTTPS) |
| Exchange | Delta Exchange **India** (`api.india.delta.exchange`) |
| Legal | Deferred by owner for now (build first). ToS/disclaimers are placeholders. |

### Packages (the entitlement gate)

The brain emits signals continuously; each client's **package** decides whether
that client is allowed to act on a given signal this week.

| Package | Price/mo | Entitlement |
|---|---|---|
| Starter | ₹5,000 | **1 trade per week** — only the first signal of the week executes |
| Growth | ₹10,000 | **Up to 2 trading days per week** — signals execute on at most 2 distinct calendar days |
| Pro | ₹20,000 | **Unlimited** — every signal executes, all days |

Week boundary: **Monday 00:00 IST** (configurable). Counters reset weekly per client.

---

## Architecture — decouple the brain from execution

```
┌──────────────────────┐
│   THE BRAIN (1×)      │  existing go-trader orchestrator — UNCHANGED
│  EMA cross + Vision    │  emits {symbol, side, sl_price, ts, signal_id}
└──────────┬───────────┘
           │ publish (signal_bus table / Redis stream)
           ▼
┌──────────────────────┐
│   SIGNAL BUS          │  Postgres `signals` table (+ optional Redis stream)
└──────────┬───────────┘
           │ fan-out
           ▼
┌───────────────────────────────────────────────────────────┐
│   EXECUTION SERVICE (multi-tenant worker, async)            │
│   for each ACTIVE + SUBSCRIBED + CONNECTED client:           │
│     0. entitlement gate (package weekly quota) — skip if used│
│     1. equity = client Delta balance                         │
│     2. size = equity × 2% / |entry − sl_price|               │
│     3. place market order (client_order_id = sig:client)     │   ← idempotent
│     4. attach bracket SL @ sl_price                          │
│     5. on CLOSE signal → reduce_only close                   │
│     6. reconcile that client's account                       │
└───────────────────────────────────────────────────────────┘
           │ STATIC egress IP (whitelisted on every client's Delta key)
           ▼
        Delta Exchange India — N independent client accounts
```

### Why this is low-risk to build
- **The brain already emits `{symbol, side, stop_loss_price}` JSON.** The product is
  everything downstream — we never modify signal generation.
- **The per-client executor reuses `../platforms/delta/adapter.py`** — the
  contract-size normalization, order placement, reduce-only close, and stop-order
  dedup hardened this session. We parameterize it to accept per-client keys instead
  of reading from env.
- **Sizing = the existing `perpsLiveOrderSize` logic**, run once per client with their
  equity. The brain stays size-agnostic, exactly as the owner wants.

### Delta API facts that shape the design (from docs research)
- **No broker/copy-trade/delegation API.** Custodial per-client keys are the only path.
- Key scopes: **Trading** and **Read Data** only — **no withdrawal permission exists.**
  → A leaked key cannot withdraw funds, only trade. Major blast-radius reduction.
- **Trading keys require IP whitelisting** → clients whitelist our static IP; a stolen
  key is useless off our infra. Forces **fixed egress IP** (NAT gateway / Elastic IP).
- **Idempotency:** `client_order_id` (≤32 chars) → key it on `(signal_id, client_id)`.
- **Bracket orders** (`POST /v2/orders/bracket`) → native TP+SL per client.
- **Rate limits are per-user-ID** (10k weight / 5 min). Fan-out to 1,000 clients does
  not share a quota. Matching engine caps **500 ops/sec per product** → a 1,000-client
  burst on one symbol must spread over ~2–3s (throttle/batch in the worker).

---

## Components & stack

| Component | Tech | Folder |
|---|---|---|
| Frontend (user + admin panels) | Next.js 15 (App Router) + TypeScript + Tailwind | `frontend/` |
| Backend API | FastAPI (Python — reuses Delta adapter) | `backend/` (Phase 2) |
| Execution fan-out worker | Python async, wraps `platforms/delta/adapter.py` | `executor/` (Phase 2) |
| Database | Postgres | (managed/VPS) |
| Secrets | Envelope encryption (KMS / age / libsodium sealed box) | — |
| Auth | Email + OTP (Better Auth has a Dodo plugin) | — |
| Payments | Dodo Payments subscriptions + webhooks | — |
| Hosting | Caddy reverse-proxy → Next.js (`:3000`) | `Caddyfile` |

---

## Data model (Postgres, draft)

- `users` (id, email, created_at, role[user|admin])
- `subscriptions` (id, user_id, dodo_subscription_id, package[starter|growth|pro],
  status[active|on_hold|cancelled|failed], current_period_end)
- `delta_connections` (id, user_id, api_key, **api_secret_encrypted**, status[connected|invalid|revoked],
  last_validated_at) — secret NEVER stored plaintext, NEVER logged
- `signals` (id, symbol, side, sl_price, created_at, source) — the bus
- `client_orders` (id, user_id, signal_id, client_order_id, delta_order_id, status,
  size, fill_px, fee, created_at) — idempotency + audit
- `entitlement_counters` (user_id, week_start, trades_count, trading_days json) — package gate
- `audit_log` (actor, action, target, meta, ts)

---

## Subscription lifecycle → execution state (Dodo webhooks)

| Dodo webhook | Action |
|---|---|
| `subscription.active` / `subscription.renewed` | Enable client execution; reset/extend period |
| `subscription.on_hold` / `subscription.failed` | Disable execution; **force-close** open positions |
| cancellation / period end | Disable; **force-close**; mark disconnected |

---

## User flow (`app.diffraction.in`)

1. Sign up / log in (email + OTP).
2. **Pricing** — Starter / Growth / Pro → Dodo checkout session → pay.
3. **Connect Delta** wizard:
   - Create a Delta API key with **Trading** permission (no withdrawal exists).
   - **Whitelist this IP:** `<STATIC_EGRESS_IP>`.
   - Paste key + secret → backend validates with a read call → **Connected**.
4. **Dashboard** — connection health, live positions, PnL, signal feed, subscription, pause/disconnect.

## Admin panel

- Client roster, subscription state, connection health, equity, open positions.
- **Global kill switch** + per-client kill switch.
- Signal monitor (emitted vs. filled per client), revenue, churn, alerts
  (key revoked / IP-not-whitelisted / insufficient margin / reconcile drift).

---

## Phased roadmap

- **Phase 0 (this commit):** product folder, spec, frontend scaffold, Caddy config.
- **Phase 1:** finish frontend (auth, pricing→Dodo checkout, connect wizard, dashboard, admin).
- **Phase 2:** backend API + Postgres schema + secret encryption + Dodo webhooks.
- **Phase 3:** execution fan-out worker (entitlement gate, per-client sizing, idempotent
  placement, force-close, per-client reconcile) wrapping the Delta adapter.
- **Phase 4:** signal bus wiring from the brain; static egress IP; load test to 1,000 clients.
- **Phase 5:** hardening — secret-store audit, kill switches, monitoring, pen-test.

---

## Open risks (tracked, not blocking the build per owner)
- **Legality** (FIU-IND/PMLA "VDA service provider" question; SEBI advisory gray zone) — deferred.
- **Static egress IP** must be locked before client onboarding (key whitelists depend on it).
- **Force-close on lapse** touches client money after they stop paying — needs airtight ToS.
- **Secret custody** is a honeypot — encryption + no-withdrawal scope + IP-whitelist are the backstops.
