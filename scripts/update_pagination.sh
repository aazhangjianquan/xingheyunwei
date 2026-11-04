#!/usr/bin/env bash
# Rebuild Vue and API, then validate real MySQL pagination.
set -Eeuo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
mkdir -p reports
exec > >(tee reports/pagination-update.log) 2>&1
on_failure() {
  task_exit=$?
  trap - ERR
  echo "更新未完成（${task_exit}），诊断：reports/pagination-update.log"
  docker compose ps -a || true
  docker compose logs --no-color --tail 80 api || true
  exit "$task_exit"
}
trap on_failure ERR

docker compose config --quiet
export BUILDKIT_PROGRESS=plain
echo "更新三类单据状态统计与分页；沿用现有数据库、端口和密钥卷"
docker compose up --build -d api
task_ready=0
for task_attempt in $(seq 1 30); do
  if docker compose exec -T api python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=2)' >/dev/null 2>&1; then
    task_ready=1
    break
  fi
  sleep 3
done
if [ "$task_ready" -ne 1 ]; then
  echo "API 未就绪，正在收集日志。"
  false
fi
echo "只读核对真实 MySQL 计数与 10/20/50 分页；不创建演示单据"
docker compose exec -T api python scripts/verify_pagination.py --report /app/reports/pagination-verification.json
curl --fail --silent --show-error --max-time 8 http://127.0.0.1:8080/api/health
echo
docker compose ps -a
echo "完成，请刷新 http://127.0.0.1:8080/resources；验证结果：reports/pagination-verification.json"
