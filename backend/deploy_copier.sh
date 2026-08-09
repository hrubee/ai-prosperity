#!/usr/bin/env bash
# Deploys the Tradejini Copier & Log Monitor to the VPS.
# Usage (from aiprosperity/backend/):
#   bash deploy_copier.sh
#   STRADDLE_VPS=root@1.2.3.4 bash deploy_copier.sh

set -euo pipefail

VPS="${STRADDLE_VPS:-root@187.127.132.39}"
REMOTE="/root/aiprosperity/backend"

echo "[deploy] Syncing copier files to ${VPS}:${REMOTE} ..."

# We sync the newly created files and preserve their directory structure
rsync -avR \
  app/copier_monitor.py \
  app/xts_interactive_ws.py \
  app/static/index.html \
  requirements.txt \
  tradejini-copier.service \
  "${VPS}:${REMOTE}/"

echo "[deploy] Installing requirements on remote..."
ssh "$VPS" "cd ${REMOTE} && .venv/bin/pip install -r requirements.txt"

echo "[deploy] Updating systemd service..."
ssh "$VPS" "cp ${REMOTE}/tradejini-copier.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable tradejini-copier && systemctl restart tradejini-copier"

echo "[deploy] Done! Copier is running on port 8000."
echo "[deploy] Logs monitor dashboard should be available at http://YOUR_VPS_IP:8000/"
