#!/usr/bin/env bash
# Guarded straddle deploy (enforces Fix 5). Refuses to push code during a live,
# market-hours session with an open position — the 2026-06-03 incident trigger was
# exactly a mid-session deploy that restarted the runner and orphaned a position.
#
# It runs `straddle_runner deploy-check` ON THE VPS (where the DB + .env live) and
# only rsyncs if that exits 0 (SAFE). Stage changes; they apply after the 15:20
# square-off confirms flat. Deploy is rsync-only (no GitHub), per project policy.
#
# Usage (from aiprosperity/backend/):
#   bash deploy_straddle.sh app/straddle_runner.py app/straddle_watchdog.py
#   STRADDLE_VPS=root@1.2.3.4 bash deploy_straddle.sh <files...>
set -euo pipefail

VPS="${STRADDLE_VPS:-root@187.127.167.177}"
REMOTE="/root/aiprosperity/backend"

if [ "$#" -eq 0 ]; then
  echo "usage: bash deploy_straddle.sh <file> [file...]   (paths relative to backend/)" >&2
  exit 2
fi

echo "[deploy] deploy-check on ${VPS} ..."
if ! ssh "$VPS" "cd ${REMOTE} && set -a; . ./.env; set +a; PYTHONPATH=${REMOTE} ./.venv/bin/python -m app.straddle_runner deploy-check"; then
  echo "[deploy] REFUSED — a live position is open during market hours." >&2
  echo "[deploy] Stage the change; re-run after 15:20 square-off confirms flat." >&2
  exit 1
fi

echo "[deploy] SAFE — rsyncing $# file(s) to ${VPS}:${REMOTE} ..."
for f in "$@"; do
  if [ ! -f "$f" ]; then echo "[deploy] missing local file: $f" >&2; exit 3; fi
  rsync -av "$f" "${VPS}:${REMOTE}/$(dirname "$f")/"
done
echo "[deploy] done. STRADDLE_LIVE is unchanged — going live is a separate, conscious flip."
