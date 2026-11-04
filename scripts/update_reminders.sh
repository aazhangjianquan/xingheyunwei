#!/usr/bin/env bash
# Build, back up, migrate and deploy expiry reminders on the existing local stack.
set -Eeuo pipefail
umask 077
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
mkdir -p reports
exec > >(tee reports/reminders-update.log) 2>&1
task_stopped=0
task_replacing=0
task_backup=''
on_exit() {
  task_exit=$?
  if [ "$task_exit" -ne 0 ]; then
    echo "提醒更新未完成；日志 reports/reminders-update.log；备份 ${task_backup}"
    if [ "$task_stopped" -eq 1 ] && [ "$task_replacing" -eq 0 ]; then
      docker compose start api worker || true
    fi
    docker compose ps -a
  fi
  exit "$task_exit"
}
trap on_exit EXIT
docker compose config --quiet
task_api="$(docker compose ps -q api | head -n 1)"
test -n "$task_api"
docker exec -i "$task_api" python - <<'PY'
from app.database import engine
assert engine.dialect.name == 'mysql' and engine.url.host == 'mysql' and engine.url.database == 'yunweishenji', '请先备份实际使用的外部数据库'
print('已确认当前使用本项目 MySQL。')
PY
echo '构建镜像，原平台在构建期间继续运行'
docker compose build api frontend
task_backup="reports/reminder-backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$task_backup"
docker inspect --format '{{.Image}}' "$task_api" > "$task_backup/previous-api-image.txt"
docker compose ps -q frontend | xargs docker inspect --format '{{.Image}}' > "$task_backup/previous-frontend-image.txt"
echo '短暂停止业务写入与通知投递，备份数据库和原主密钥'
task_stopped=1
docker compose stop reminder-scheduler worker api
docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; export MYSQL_PWD; exec mysqldump -uroot --single-transaction --no-tablespaces --set-gtid-purged=OFF --databases yunweishenji' > "$task_backup/database.sql"
test -s "$task_backup/database.sql"
docker cp "$task_api:/app/instance" "$task_backup/instance"
test -s "$task_backup/instance/keys.json"
chmod -R go-rwx "$task_backup"
echo "备份完成：$task_backup"
task_replacing=1
docker compose up -d --no-deps --wait --wait-timeout 300 api
task_stopped=0
echo '预览历史到期计划，不提交通知和计划记录'
docker compose exec -T api python -m app.reminder_scheduler --preview
docker compose up -d --no-deps --wait --wait-timeout 120 frontend worker reminder-scheduler
docker compose exec -T frontend nginx -t
curl --fail --silent --show-error http://127.0.0.1:8080/api/health
echo
docker compose ps
echo '到期提醒已部署：http://localhost:8080；详情页可修改飞书提醒。'
