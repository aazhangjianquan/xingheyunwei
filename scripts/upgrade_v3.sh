#!/usr/bin/env bash
# Preserve data and encryption volumes. Back up MySQL before the additive upgrade.
set -Eeuo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
mkdir -p reports
exec > >(tee reports/upgrade-v3.log) 2>&1
on_failure() {
  task_exit=$?
  trap - ERR
  echo "升级/验收失败（${task_exit}），日志：reports/upgrade-v3.log"
  docker compose ps -a || true
  docker compose logs --no-color --tail 100 api mysql || true
  exit "$task_exit"
}
trap on_failure ERR
docker compose config --quiet
echo "停止 API 写入，备份数据库及密钥卷"
docker compose stop api
task_stamp="$(date +%Y%m%d-%H%M%S)"
task_backup="reports/pre-v3-${task_stamp}.sql"
if ! docker compose exec -T mysql sh -c 'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --no-tablespaces --set-gtid-purged=OFF --databases yunweishenji' > "$task_backup"; then
  echo "数据库备份失败，未执行升级"
  docker compose start api || true
  exit 1
fi
chmod 600 "$task_backup"
# The stopped container can still copy its existing persistent instance directory.
if ! docker compose cp api:/app/instance "reports/pre-v3-keys-${task_stamp}"; then
  echo "密钥备份失败，未执行升级"
  docker compose start api || true
  exit 1
fi
chmod -R go-rwx "reports/pre-v3-keys-${task_stamp}"
echo "数据库与密钥备份完成。保留原有 mysql_data / api_instance 卷。"
export BUILDKIT_PROGRESS=plain
docker compose up --build -d api
for task_attempt in $(seq 1 40); do
  if docker compose exec -T api python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=2)' >/dev/null 2>&1; then
    break
  fi
  sleep 3
done
docker compose exec -T api python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=3)'
echo "在真实 MySQL 验收 V3（创建验收账号和单据，通知仅记录，不向飞书发送）"
docker compose exec -T api python scripts/verify_v3.py --report /app/reports/workflow-v3-mysql.json
docker compose ps -a
echo "升级完成：http://localhost:8080；验收：reports/workflow-v3-mysql.json"
