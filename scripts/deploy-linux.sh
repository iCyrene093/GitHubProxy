#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/github-release-proxy}"
APP_USER="${APP_USER:-githubproxy}"
PORT="${PORT:-8000}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-change-this-admin-password}"
SECRET_KEY="${SECRET_KEY:-$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)}"

if [[ $EUID -ne 0 ]]; then
  echo "请使用 root 运行：sudo APP_DIR=$APP_DIR ADMIN_PASSWORD=... $0" >&2
  exit 1
fi

apt-get update
apt-get install -y python3 python3-venv python3-pip git

id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR" "$APP_DIR/data"
cp -R . "$APP_DIR/app"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/app/requirements.txt"
cat > /etc/systemd/system/github-release-proxy.service <<SERVICE
[Unit]
Description=GitHub Release Proxy
After=network.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR/app
Environment=ADMIN_PASSWORD=$ADMIN_PASSWORD
Environment=SECRET_KEY=$SECRET_KEY
Environment=GITHUB_PROXY_DB=$APP_DIR/data/github_proxy.sqlite3
ExecStart=$APP_DIR/venv/bin/gunicorn -w 2 -b 0.0.0.0:$PORT app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
systemctl daemon-reload
systemctl enable --now github-release-proxy.service
systemctl status github-release-proxy.service --no-pager
