#!/usr/bin/env bash
# One-shot provisioning for a fresh Ubuntu/Debian VPS.
# Run as root (or with sudo) ON THE SERVER after copying the repo to
# /opt/binance-tv-bridge and creating /opt/binance-tv-bridge/.env
#
#     sudo bash deploy/setup_server.sh
#
set -euo pipefail

APP_DIR=/opt/binance-tv-bridge
SERVICE=binance-tv-bridge

echo "==> Installing system packages"
apt-get update -y
apt-get install -y python3-venv python3-pip

echo "==> Creating service user 'bridge'"
id -u bridge >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin bridge

echo "==> Building virtualenv"
cd "$APP_DIR"
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo "==> Fixing permissions"
mkdir -p "$APP_DIR/logs"
chown -R bridge:bridge "$APP_DIR"

if [[ ! -f "$APP_DIR/.env" ]]; then
  echo "!! No .env found — copy .env.example to .env and fill it in before starting."
fi

echo "==> Installing systemd service"
cp deploy/${SERVICE}.service /etc/systemd/system/${SERVICE}.service
systemctl daemon-reload
systemctl enable ${SERVICE}
systemctl restart ${SERVICE}

echo "==> Done. Status:"
sleep 1
systemctl --no-pager status ${SERVICE} || true
echo
echo "Logs:   journalctl -u ${SERVICE} -f"
echo "Health: curl http://127.0.0.1:8080/health"
