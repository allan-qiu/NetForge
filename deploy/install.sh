#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 root 运行：sudo bash deploy/install.sh" >&2
  exit 1
fi

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_ROOT="/opt/netforge"
APP_DIR="${APP_ROOT}/app"
DATA_DIR="/var/lib/netforge"
DB_PATH="${DATA_DIR}/netforge.db"
BACKUP_DIR="${DATA_DIR}/deploy-backups"
ENV_FILE="/etc/netforge.env"
SERVICE_FILE="/etc/systemd/system/netforge.service"
NGINX_AVAILABLE="/etc/nginx/sites-available/netforge"
NGINX_ENABLED="/etc/nginx/sites-enabled/netforge"
STAMP="$(date +%Y%m%d-%H%M%S)"
NEW_DIR="${APP_ROOT}/app.new.${STAMP}"
OLD_DIR="${APP_ROOT}/app.backup.${STAMP}"
SWITCHED=0

rollback_app() {
  local exit_code=$?
  if [[ "${SWITCHED}" -eq 1 && -d "${OLD_DIR}" ]]; then
    echo "升级失败，正在切回旧应用目录" >&2
    systemctl stop netforge 2>/dev/null || true
    mv "${APP_DIR}" "${APP_ROOT}/app.failed.${STAMP}" 2>/dev/null || true
    mv "${OLD_DIR}" "${APP_DIR}"
    systemctl start netforge 2>/dev/null || true
  fi
  exit "${exit_code}"
}
trap rollback_app ERR

echo "[1/9] 安装系统依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y nginx python3 curl openssl

echo "[2/9] 创建服务账户和受限目录"
if ! id -u netforge >/dev/null 2>&1; then
  useradd --system --home-dir "${APP_ROOT}" --shell /usr/sbin/nologin netforge
fi
install -d -m 0755 -o root -g root "${APP_ROOT}"
install -d -m 0750 -o netforge -g netforge "${DATA_DIR}" "${BACKUP_DIR}"

echo "[3/9] 准备候选版本"
install -d -m 0755 "${NEW_DIR}/dist" "${NEW_DIR}/migrations" "${NEW_DIR}/scripts"
install -m 0644 "${SOURCE_DIR}/VERSION" "${SOURCE_DIR}/server.py" "${SOURCE_DIR}/netforge_v2.py" "${SOURCE_DIR}/v2_http.py" "${SOURCE_DIR}/learning_features.py" "${SOURCE_DIR}/question_bank.js" "${NEW_DIR}/"
install -m 0644 "${SOURCE_DIR}"/migrations/*.sql "${NEW_DIR}/migrations/"
install -m 0755 "${SOURCE_DIR}/scripts/netforge_v2_admin.py" "${NEW_DIR}/scripts/"
cp -a "${SOURCE_DIR}/dist/." "${NEW_DIR}/dist/"
chown -R root:root "${NEW_DIR}"

echo "[4/9] 保留或创建安全配置"
if [[ ! -f "${ENV_FILE}" ]]; then
  umask 077
  {
    printf 'NETFORGE_DATA_DIR=%s\n' "${DATA_DIR}"
    printf 'NETFORGE_HOST=127.0.0.1\n'
    printf 'NETFORGE_PORT=8787\n'
    printf 'NETFORGE_SESSION_DAYS=30\n'
    printf 'NETFORGE_COOKIE_SECURE=0\n'
    printf 'NETFORGE_REGISTRATION_CODE=NF-%s\n' "$(openssl rand -hex 5 | tr '[:lower:]' '[:upper:]')"
    printf 'NETFORGE_ADMIN_USERNAME=admin\n'
    printf 'NETFORGE_ADMIN_PASSWORD=NFADM-%s\n' "$(openssl rand -hex 12 | tr '[:lower:]' '[:upper:]')"
  } > "${ENV_FILE}"
fi
chmod 0600 "${ENV_FILE}"

echo "[5/9] 创建一致性备份并做隔离迁移预检"
if [[ -f "${DB_PATH}" ]]; then
  runuser -u netforge -- python3 "${NEW_DIR}/scripts/netforge_v2_admin.py" --db "${DB_PATH}" backup --destination "${BACKUP_DIR}"
  PRE_BACKUP="$(find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'netforge-*.db' -printf '%T@ %p\n' | sort -nr | head -n 1 | cut -d' ' -f2-)"
  if [[ -z "${PRE_BACKUP}" || ! -f "${PRE_BACKUP}" ]]; then
    echo "未找到已验证的部署前备份" >&2
    exit 1
  fi
  PREFLIGHT_DB="${DATA_DIR}/preflight-${STAMP}.db"
  cp "${PRE_BACKUP}" "${PREFLIGHT_DB}"
  chown netforge:netforge "${PREFLIGHT_DB}"
  runuser -u netforge -- python3 "${NEW_DIR}/scripts/netforge_v2_admin.py" --db "${PREFLIGHT_DB}" migrate
  runuser -u netforge -- python3 "${NEW_DIR}/scripts/netforge_v2_admin.py" --db "${PREFLIGHT_DB}" import-questions --bank "${NEW_DIR}/question_bank.js"
  rm -f "${PREFLIGHT_DB}" "${PREFLIGHT_DB}-wal" "${PREFLIGHT_DB}-shm"
fi

cat > "${SERVICE_FILE}" <<'EOF'
[Unit]
Description=NetForge Huawei ICT Network Quiz
After=network.target

[Service]
Type=simple
User=netforge
Group=netforge
WorkingDirectory=/opt/netforge/app
EnvironmentFile=/etc/netforge.env
ExecStart=/usr/bin/python3 /opt/netforge/app/server.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/netforge

[Install]
WantedBy=multi-user.target
EOF

echo "[6/9] 配置 Nginx"
if [[ -e "${NGINX_AVAILABLE}" ]]; then
  cp -a "${NGINX_AVAILABLE}" "${NGINX_AVAILABLE}.backup.${STAMP}"
fi
cat > "${NGINX_AVAILABLE}" <<'EOF'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    client_max_body_size 12m;
    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
EOF
ln -sfn "${NGINX_AVAILABLE}" "${NGINX_ENABLED}"
rm -f /etc/nginx/sites-enabled/default
nginx -t

echo "[7/9] 停止写入、切换版本并执行幂等迁移"
systemctl stop netforge 2>/dev/null || true
if [[ -d "${APP_DIR}" ]]; then
  mv "${APP_DIR}" "${OLD_DIR}"
fi
mv "${NEW_DIR}" "${APP_DIR}"
SWITCHED=1
runuser -u netforge -- python3 "${APP_DIR}/scripts/netforge_v2_admin.py" --db "${DB_PATH}" migrate
runuser -u netforge -- python3 "${APP_DIR}/scripts/netforge_v2_admin.py" --db "${DB_PATH}" import-questions --bank "${APP_DIR}/question_bank.js"

echo "[8/9] 启动并验证"
systemctl daemon-reload
systemctl enable netforge
systemctl restart netforge
systemctl enable --now nginx
systemctl reload nginx
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
  ufw allow 80/tcp >/dev/null
fi
for _ in {1..30}; do
  if curl -fsS http://127.0.0.1:8787/api/health >/dev/null; then
    break
  fi
  sleep 1
done
HEALTH="$(curl -fsS http://127.0.0.1:8787/api/health)"
PYTHONPATH="${APP_DIR}" python3 -c '
import json
import sys
from netforge_v2 import load_question_bank

health = json.loads(sys.argv[1])
expected = len(load_question_bank(sys.argv[2]))
if health.get("ok") is not True or health.get("questions") != expected:
    sys.exit("健康检查题数与部署题库不匹配")
' "${HEALTH}" "${APP_DIR}/question_bank.js"

echo "[9/9] 升级完成"
echo "健康检查：${HEALTH}"
echo "管理后台路径：/admin"
echo "部署前备份目录：${BACKUP_DIR}"
echo "管理员凭据与注册码仅保存在 ${ENV_FILE}，本脚本不会输出明文。"
trap - ERR
