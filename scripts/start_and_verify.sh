#!/usr/bin/env bash
# Run this on the Docker host. Logs remain in the project for diagnosis.
set -Eeuo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
mkdir -p reports
exec > >(tee reports/startup-diagnostics.log) 2>&1

on_failure() {
  task_exit=$?
  trap - ERR
  echo "启动或验证失败（退出码 ${task_exit}），诊断日志已保存：reports/startup-diagnostics.log"
  docker compose ps -a || true
  docker compose logs --no-color --tail 80 mysql api || true
  exit "$task_exit"
}
trap on_failure ERR

echo "检查 Docker 与 Compose"
docker info --format 'Docker server: {{.ServerVersion}}'
docker compose version
docker compose config --quiet

echo "构建并启动本项目服务"
export BUILDKIT_PROGRESS=plain
docker compose up --build -d

echo "等待 API 完成启动（最长约 90 秒）"
task_ready=0
for task_attempt in $(seq 1 30); do
  if docker compose exec -T api python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=2)' >/dev/null 2>&1; then
    task_ready=1
    break
  fi
  sleep 3
done
if [ "$task_ready" -ne 1 ]; then
  echo "API 未在等待时间内就绪"
  false
fi

echo "服务状态"
docker compose ps
echo "使用真实 MySQL 验证多角色、项目及 3 类可配置审批（会创建演示单据）"
docker compose exec -T api python scripts/verify_v3.py --report /app/reports/workflow-v3-mysql.json
echo "完成。平台地址：http://localhost:8080；报告：reports/workflow-v3-mysql.json"
