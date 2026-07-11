#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/github-release-proxy}"
APP_USER="${APP_USER:-githubproxy}"
PORT="${PORT:-8000}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
SECRET_KEY="${SECRET_KEY:-}"
ENV_FILE="${ENV_FILE:-/etc/github-release-proxy.env}"
DEPLOY_REPO_URL="${DEPLOY_REPO_URL:-https://github.com/iCyrene093/GitHubProxy.git}"
DEPLOY_REF="${DEPLOY_REF:-main}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SOURCE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
DEST_DIR="$APP_DIR/app"
TMP_SOURCE_DIR=""
EXISTING_DB_PATH=""
PRESERVED_APP_DB_DIR=""

read_env_value() {
  local key="$1"
  local file="$2"
  if [[ -f "$file" ]]; then
    python3 - "$key" "$file" <<'PY'
import sys
key, path = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name == key:
            print(value.strip().strip('"').strip("'"))
            break
PY
  fi
}

if [[ $EUID -ne 0 ]]; then
  echo "请使用 root 运行：sudo APP_DIR=$APP_DIR ADMIN_PASSWORD=... $0" >&2
  exit 1
fi

if [[ -z "$ADMIN_PASSWORD" || "$ADMIN_PASSWORD" == "admin" || "$ADMIN_PASSWORD" == "change-this-admin-password" ]]; then
  echo "请设置非默认管理员密码：sudo ADMIN_PASSWORD=... $0" >&2
  exit 1
fi

cleanup() {
  if [[ -n "$TMP_SOURCE_DIR" ]]; then
    rm -rf "$TMP_SOURCE_DIR"
  fi
  if [[ -n "$PRESERVED_APP_DB_DIR" ]]; then
    rm -rf "$PRESERVED_APP_DB_DIR"
  fi
}
trap cleanup EXIT

apt-get update
apt-get install -y python3 python3-venv python3-pip git

if [[ ! -f "$SOURCE_DIR/app.py" || ! -f "$SOURCE_DIR/requirements.txt" ]]; then
  if [[ -z "$DEPLOY_REPO_URL" ]]; then
    echo "未检测到本地源码，且 DEPLOY_REPO_URL 为空。请设置 DEPLOY_REPO_URL=https://github.com/owner/repo.git 以便自动从 GitHub 下载并安装。" >&2
    exit 1
  fi
  TMP_SOURCE_DIR="$(mktemp -d)"
  git clone --depth 1 --branch "$DEPLOY_REF" "$DEPLOY_REPO_URL" "$TMP_SOURCE_DIR"
  SOURCE_DIR="$TMP_SOURCE_DIR"
fi

if [[ -z "$SECRET_KEY" ]]; then
  SECRET_KEY="$(read_env_value SECRET_KEY "$ENV_FILE")"
fi
if [[ -z "$SECRET_KEY" ]]; then
  SECRET_KEY="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
fi

EXISTING_DB_PATH="$(read_env_value GITHUB_PROXY_DB "$ENV_FILE")"
if [[ -z "$EXISTING_DB_PATH" ]]; then
  EXISTING_DB_PATH="$APP_DIR/data/github_proxy.sqlite3"
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
    if [[ -d "$DEST_DIR/data" ]]; then
      PRESERVED_APP_DB_DIR="$(mktemp -d)"
      cp -a "$DEST_DIR/data/." "$PRESERVED_APP_DB_DIR/"
    fi
    rm -rf "$DEST_DIR"
    mkdir -p "$DEST_DIR"
    cp -a "$SOURCE_DIR/." "$DEST_DIR/"
    if [[ -n "$PRESERVED_APP_DB_DIR" ]]; then
      mkdir -p "$DEST_DIR/data"
      cp -a "$PRESERVED_APP_DB_DIR/." "$DEST_DIR/data/"
    fi
    ;;
esac
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/app/requirements.txt"
install -m 600 /dev/null "$ENV_FILE"
cat > "$ENV_FILE" <<ENV
ADMIN_PASSWORD=$ADMIN_PASSWORD
SECRET_KEY=$SECRET_KEY
GITHUB_PROXY_DB=$EXISTING_DB_PATH
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
