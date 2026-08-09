#!/bin/bash
set -e

echo "Deploying aiprosperity to VPS..."

# 1. Rsync backend and frontend to VPS
rsync -avz --exclude "node_modules" --exclude ".next" --exclude "__pycache__" --exclude ".git" --exclude ".venv" \
    /Users/hrushi/Downloads/Desktop\ offline/vibe\ coding/go\ trader/go-trader/aiprosperity/ \
    root@187.127.132.39:/root/aiprosperity/

# 2. Inject UPI_QR_BASE64 into backend/.env on VPS
# Extract from local go-trader/.env
QR_VAR=$(grep "UPI_QR_BASE64=" "/Users/hrushi/Downloads/Desktop offline/vibe coding/go trader/go-trader/.env" || true)
if [ -n "$QR_VAR" ]; then
    ssh -o BatchMode=yes root@187.127.132.39 "sed -i '/^UPI_QR_BASE64=/d' /root/aiprosperity/backend/.env && echo '$QR_VAR' >> /root/aiprosperity/backend/.env"
    echo "Injected QR code to VPS backend/.env"
fi

# 3. Build frontend and restart services on VPS
ssh -o BatchMode=yes root@187.127.132.39 'bash -s' << 'REMOTE_EOF'
    set -e
    echo "Building frontend on VPS..."
    cd /root/aiprosperity/frontend
    npm run build
    
    echo "Restarting services..."
    systemctl restart aiprosperity-backend.service
    systemctl restart aiprosperity-frontend.service
    echo "Deployment Complete!"
REMOTE_EOF

