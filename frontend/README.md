# AI Prosperity — Frontend

Next.js 15 (App Router) + TypeScript + Tailwind. User panel + admin panel.
Phase 0 scaffold: real pages, placeholder data, backend hooks marked `TODO(Phase 2)`.

## Run locally

```bash
cd aiprosperity/frontend
npm install
npm run dev        # http://localhost:3000
```

## Pages

| Route | Purpose |
|---|---|
| `/` | Landing + pricing (Starter / Growth / Pro) |
| `/login` | Email + OTP auth (scaffold) |
| `/connect` | 3-step Delta key wizard (create key → whitelist IP → paste secret) |
| `/dashboard` | User: connection health, positions, PnL, signal feed |
| `/admin` | Admin: client roster, subscription/connection state, kill switch |

Package definitions live in `lib/packages.ts` (single source of truth, mirrored
server-side by the entitlement gate).

## Production (VPS + Caddy)

```bash
npm ci && npm run build
npm run start            # serves on :3000 under a process manager (systemd/pm2)
```

Then place `../Caddyfile` on the VPS and `systemctl reload caddy`. Caddy
reverse-proxies `app.diffraction.in` → `127.0.0.1:3000` with auto-HTTPS.

## Not yet wired (next phases)

- Backend API (FastAPI) + Postgres + encrypted key storage — Phase 2
- Dodo Payments checkout + `subscription.*` webhooks — Phase 2
- Execution fan-out worker (entitlement gate, 2% sizing, idempotent placement,
  force-close on lapse) wrapping `../../platforms/delta/adapter.py` — Phase 3
- Static egress IP → replace `STATIC_EGRESS_IP` in `app/connect/page.tsx` — Phase 4

See `../BUILD_PLAN.md` for the full architecture and decisions.
