#!/usr/bin/env bash
# Rebuild only this application's API; retain the database and instance volumes.
set -Eeuo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
mkdir -p reports
exec > >(tee reports/api-repair.log) 2>&1

on_failure() {
  task_exit=$?
  trap - ERR
  echo "修复未完成（退出码 ${task_exit}）。诊断记录：reports/api-repair.log"
  docker compose ps -a || true
  docker compose logs --no-color --tail 100 api mysql || true
  exit "$task_exit"
}
trap on_failure ERR

echo "检查 Docker 与项目配置"
docker info --format 'Docker server: {{.ServerVersion}}'
docker compose config --quiet

echo "核对本地启动修复与页面名称"
# The import must be at module scope so existing settings survive a restart.
if sed -n '/async def lifespan/,/^app = /p' backend/app/main.py | grep -E '^[[:space:]]+import json' >/dev/null; then
  echo "源码仍含旧启动错误，请先保存新版 backend/app/main.py。"
  false
fi

echo "重新构建 API；保留 MySQL 数据卷、加密密钥及端口配置"
export BUILDKIT_PROGRESS=plain
docker compose up --build -d api

echo "等待 API 健康并核对真实页面（最长约 90 秒）"
task_ready=0
for task_attempt in $(seq 1 30); do
  if docker compose exec -T api python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=2)' >/dev/null 2>&1; then
    task_ready=1
    break
  fi
  sleep 3
done
if [ "$task_ready" -ne 1 ]; then
  echo "API 未就绪，下面收集容器启动日志。"
  false
fi

# Inspect startup, persisted brand, database connection and compiled HTML without
# changing demo accounts, workflow versions, business data or notification rows.
docker compose exec -T api python - <<'PY'
import json
from html import escape
import urllib.request
from sqlalchemy import text
from app.database import engine

base = 'http://127.0.0.1:8000'
with urllib.request.urlopen(base + '/api/health', timeout=5) as response:
    assert json.load(response)['status'] == 'ok'
with urllib.request.urlopen(base + '/api/auth/options', timeout=5) as response:
    site_name = json.load(response)['site_name']
assert site_name.strip(), '后台平台名称为空'
with urllib.request.urlopen(base + '/', timeout=5) as response:
    html = response.read().decode()
assert '<title>' + escape(site_name) + '</title>' in html, '页面名称与后台配置不一致'
assert engine.dialect.name == 'mysql', 'API 未连接 MySQL'
with engine.connect() as conn:
    database, version = conn.execute(text('SELECT DATABASE(), VERSION()')).one()
assert database == 'yunweishenji', '数据库名不匹配'
print(json.dumps({'api_health': 'ok', 'site_name': site_name, 'database': database,
                  'mysql_version': version, 'compiled_page': 'ok'}, ensure_ascii=False, indent=2))
PY

echo "检查宿主机 8080 / 8008 端口的实际 HTTP 响应"
for task_port in 8080 8008; do
  curl --fail --silent --show-error --max-time 8 "http://127.0.0.1:${task_port}/api/health"
  echo " 端口 ${task_port}：可访问"
done

docker compose ps -a
echo "已恢复 Docker 版平台：http://127.0.0.1:8080"
echo "完整结果已保存到 reports/api-repair.log；5173 开发服务未改动。"
