#!/usr/bin/env bash
# Upgrade the former combined service without deleting business data or keys.
set -Eeuo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
mkdir -p reports
exec > >(tee reports/services-update.log) 2>&1
trap 'echo "更新未完成，请查看 reports/services-update.log"; docker compose ps -a' ERR
python3 scripts/setup_redis.py
docker compose config --quiet
echo "先构建前端与 API；构建失败不会停止当前平台"
docker compose build api frontend
echo "预检新 API 的共享密钥配置"
docker compose run --rm --no-deps api python -c 'from app.config import KEYS; print("共享密钥配置有效")'

task_api="$(docker compose ps -q api | head -n 1)"
if [ -n "$task_api" ]; then
  docker exec "$task_api" python -c 'from app.database import engine; assert engine.url.host == "mysql" and engine.url.database == "yunweishenji", "当前使用外部数据库，请先单独备份该库再更新"'
  task_backup="reports/services-backups/$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$task_backup"
  chmod 700 reports/services-backups "$task_backup"
  echo "暂停写入，备份 MySQL 和应用主密钥"
  docker compose stop worker api
  if ! docker compose exec -T mysql sh -c 'exec mysqldump -u root -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines --triggers --no-tablespaces "$MYSQL_DATABASE"' > "$task_backup/database.sql"; then
    docker compose start api
    exit 1
  fi
  if ! docker cp "$task_api:/app/instance" "$task_backup/instance"; then
    docker compose start api
    exit 1
  fi
  chmod -R go-rwx "$task_backup"
  echo "备份完成：$task_backup"
fi

# Recreate the bridge once because the dedicated trusted proxy now has a fixed IP.
# Never use down -v: the original mysql_data/api_instance volumes must survive.
docker compose down
docker compose up -d --wait --wait-timeout 300
docker compose exec -T frontend nginx -t
curl --fail --silent --show-error http://127.0.0.1:8080/api/health
echo
docker compose ps
echo "部署完成：http://localhost:8080；独立 API 文档：http://localhost:8008/docs"
