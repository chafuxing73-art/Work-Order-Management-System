#!/usr/bin/env bash
# Ubuntu 24.04 LTS 一键部署：sudo bash deploy/install.sh
# 只依赖系统自带 Python 3，无需 apt / pip / 外网
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="workorder"
RUN_USER="${SUDO_USER:-root}"
PORT="$(python3 -c "import json;print(json.load(open('$APP_DIR/config.json',encoding='utf-8'))['server']['port'])")"

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 sudo 运行：sudo bash deploy/install.sh"
  exit 1
fi

echo "==> 项目目录: $APP_DIR"
echo "==> 服务端口: $PORT / 运行用户: $RUN_USER"

python3 --version
command -v python3 >/dev/null || { echo "未找到 python3"; exit 1; }

# SLA 时限依赖服务器本地时间，统一到北京时间
if command -v timedatectl >/dev/null 2>&1; then
  timedatectl set-timezone Asia/Shanghai 2>/dev/null || true
fi

mkdir -p "$APP_DIR/data/uploads" "$APP_DIR/backups"
RUN_GROUP="$(id -gn "$RUN_USER")"
chown -R "$RUN_USER":"$RUN_GROUP" "$APP_DIR/data" "$APP_DIR/backups"

# 若防火墙开启则放行端口（纯内网访问可按需再收紧来源）
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "^Status: active"; then
  ufw allow "${PORT}/tcp" >/dev/null && echo "==> 已放行 ufw 端口 $PORT"
fi

cat > "/etc/systemd/system/${SERVICE}.service" <<EOF
[Unit]
Description=Floor Repair Work Order System
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONUTF8=1
ExecStart=/usr/bin/python3 ${APP_DIR}/server.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1
systemctl restart "$SERVICE"

READY=0
for i in 1 2 3 4 5 6 7 8; do
  if systemctl is-active --quiet "$SERVICE"; then READY=1; break; fi
  sleep 1
done
if [ "$READY" -ne 1 ]; then
  echo "服务启动失败，请查看：journalctl -u $SERVICE -n 50 --no-pager"
  exit 1
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
IP="${IP:-127.0.0.1}"
echo ""
echo "==================== 部署完成 ===================="
echo "  员工端(二维码指向):  http://${IP}:${PORT}/?f=2F"
echo "  管理端:              http://${IP}:${PORT}/admin"
echo "  初始管理员:          admin / 123456  ← 登录后请立即修改密码"
echo ""
echo "  改服务地址后，去管理端「二维码海报」重新生成并打印 2F-6F 五张码。"
echo "  企微推送需要服务器能访问 qyapi.weixin.qq.com；不配置也能正常使用。"
echo ""
echo "  自检:   python3 $APP_DIR/deploy/smoke_test.py http://127.0.0.1:$PORT"
echo "  日志:   journalctl -u $SERVICE -f"
echo "  备份:   python3 $APP_DIR/deploy/backup.py"
echo "  忘记管理员密码(重置为默认 123456):"
echo "          sudo systemctl stop $SERVICE && python3 $APP_DIR/server.py --reset-admin && sudo systemctl start $SERVICE"
echo "  或指定新密码: python3 $APP_DIR/server.py --reset-admin 你的新密码"
echo "=================================================="
