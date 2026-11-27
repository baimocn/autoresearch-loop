#!/usr/bin/env bash
# setup_watch.sh —— 在目标服务器上部署 base_watch 定时告警
# 用法（本地）：bash scripts/setup_watch.sh
# 前置：本机已 lark-cli 授权；本机可 ssh 到目标服务器（免密）
# 必填环境变量（无默认值，缺失即报错退出）：
#   WATCH_SERVER     目标服务器的 ssh 别名或地址
#   WATCH_REPO       本机仓库路径
#   WATCH_USER_ID    接收告警的飞书 open_id
#   WATCH_BASE_TOKEN 多维表格 base-token
#   WATCH_TABLES     逗号分隔的表 ID（领题表,自行选题表）
set -euo pipefail

: "${WATCH_SERVER:?请设置 WATCH_SERVER（目标服务器的 ssh 别名或地址）}"
: "${WATCH_REPO:?请设置 WATCH_REPO（本机仓库路径）}"
: "${WATCH_USER_ID:?请设置 WATCH_USER_ID（接收告警的飞书 open_id）}"
: "${WATCH_BASE_TOKEN:?请设置 WATCH_BASE_TOKEN（多维表格 base-token）}"
: "${WATCH_TABLES:?请设置 WATCH_TABLES（逗号分隔的表 ID）}"
SERVER="$WATCH_SERVER"
REMOTE_DIR="${WATCH_REMOTE_DIR:-~/.base_watch}"
REPO="$WATCH_REPO"
USER_OPEN_ID="$WATCH_USER_ID"
BASE_TOKEN="$WATCH_BASE_TOKEN"
TABLES="$WATCH_TABLES"
FIRST_TABLE="${TABLES%%,*}"

echo "=== 1/5 复制脚本到服务器 ==="
ssh "$SERVER" "mkdir -p $REMOTE_DIR/scripts"
scp "$REPO/scripts/base_watch.py" "$SERVER:$REMOTE_DIR/scripts/base_watch.py"

echo "=== 2/5 复制飞书授权配置（不回显内容）==="
# lark-cli 的配置分两处：~/.lark-cli/config.json 与 ~/.local/share/lark-cli/（加密凭据/主密钥）
ssh "$SERVER" "mkdir -p ~/.lark-cli ~/.local/share/lark-cli"
scp ~/.lark-cli/config.json "$SERVER:~/.lark-cli/config.json"
scp -r ~/.local/share/lark-cli/. "$SERVER:~/.local/share/lark-cli/"

echo "=== 3/5 服务器端校验授权 ==="
# 真正的验证是实际读一次表；auth status 的 verified 字段只在 --verify 时出现。
if ! ssh "$SERVER" "export PATH=/usr/local/bin:\$PATH; lark-cli base +record-list --base-token $BASE_TOKEN --table-id $FIRST_TABLE --as user --format json --limit 1 --json" | python3 -c "
import json,sys
d=json.load(sys.stdin)
ok = d.get('ok') is True
print('read table:', 'OK' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
"; then
  echo "!! 复制的授权在服务器上读表失败（DPoP 软件密钥可能绑定本机）。"
  echo "   请在服务器上重新授权：ssh $SERVER 后运行 lark-cli auth login --domain base,im"
  exit 1
fi

echo "=== 4/5 建立基线 ==="
ssh "$SERVER" "export PATH=/usr/local/bin:\$PATH; cd $REMOTE_DIR && python3 scripts/base_watch.py --state $REMOTE_DIR/state.json snapshot --base $BASE_TOKEN --tables $TABLES --user $USER_OPEN_ID"

echo "=== 5/5 安装 crontab（每15分钟，只在变化时推送）==="
CRON_LINE="*/15 * * * * export PATH=/usr/local/bin:\$PATH; cd $REMOTE_DIR && python3 scripts/base_watch.py --state $REMOTE_DIR/state.json check --send --base $BASE_TOKEN --tables $TABLES --user $USER_OPEN_ID >> $REMOTE_DIR/watch.log 2>&1"
ssh "$SERVER" "crontab -l 2>/dev/null | grep -v 'base_watch.py' > /tmp/cron.basewatch || true; echo '$CRON_LINE' >> /tmp/cron.basewatch; crontab /tmp/cron.basewatch; echo '--- installed ---'; crontab -l | grep base_watch"

echo "完成。脚本在 $SERVER:$REMOTE_DIR，日志 watch.log，告警私聊发给 $USER_OPEN_ID。"
