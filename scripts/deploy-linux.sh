#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/github-release-proxy}"
APP_USER="${APP_USER:-githubproxy}"
PORT="${PORT:-8000}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
SECRET_KEY="${SECRET_KEY:-}"
ENV_FILE="${ENV_FILE:-/etc/github-release-proxy.env}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SOURCE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
DEST_DIR="$APP_DIR/app"

if [[ $EUID -ne 0 ]]; then
  echo "请使用 root 运行：sudo APP_DIR=$APP_DIR ADMIN_PASSWORD=... $0" >&2
  exit 1
fi

if [[ -z "$ADMIN_PASSWORD" || "$ADMIN_PASSWORD" == "admin" || "$ADMIN_PASSWORD" == "change-this-admin-password" ]]; then
  echo "请设置非默认管理员密码：sudo ADMIN_PASSWORD=... $0" >&2
  exit 1
fi

apt-get update
apt-get install -y python3 python3-venv python3-pip git

if [[ -z "$SECRET_KEY" ]]; then
  SECRET_KEY="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
fi

id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR" "$APP_DIR/data"
RESOLVED_DEST_DIR="$(readlink -m "$DEST_DIR")"
case "$RESOLVED_DEST_DIR" in
  "$SOURCE_DIR")
    echo "检测到部署脚本已从目标应用目录运行，跳过源码复制。"
    ;;
  "$SOURCE_DIR"/*)
    echo "部署目标不能位于源码目录内：$RESOLVED_DEST_DIR 在 $SOURCE_DIR 下" >&2
    exit 1
    ;;
  *)
    case "$SOURCE_DIR" in
      "$RESOLVED_DEST_DIR"/*)
        echo "源码目录不能位于部署目标内：$SOURCE_DIR 在 $RESOLVED_DEST_DIR 下" >&2
        exit 1
        ;;
    esac
    rm -rf "$DEST_DIR"
    mkdir -p "$DEST_DIR"
    cp -a "$SOURCE_DIR/." "$DEST_DIR/"
    ;;
esac
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/app/requirements.txt"
install -m 600 /dev/null "$ENV_FILE"
cat > "$ENV_FILE" <<ENV
ADMIN_PASSWORD=$ADMIN_PASSWORD
SECRET_KEY=$SECRET_KEY
GITHUB_PROXY_DB=$APP_DIR/data/github_proxy.sqlite3
ENV
chmod 600 "$ENV_FILE"

cat > /etc/systemd/system/github-release-proxy.service <<SERVICE
[Unit]
Description=GitHub Release Proxy
After=network.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR/app
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/venv/bin/gunicorn -w 2 -b 0.0.0.0:$PORT app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
systemctl daemon-reload
systemctl enable github-release-proxy.service
systemctl restart github-release-proxy.service
systemctl status github-release-proxy.service --no-pager
