#!/usr/bin/env bash
# Measure the running version, deploy compiled changes, then verify the same session/data.
set -Eeuo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
mkdir -p reports
exec > >(tee reports/startup-update.log) 2>&1
on_failure() {
  task_exit=$?
  trap - ERR
  unset task_check_token
  echo "更新或验证失败（${task_exit}）；日志：reports/startup-update.log"
  docker compose ps -a || true
  docker compose logs --no-color --tail 80 api || true
  exit "$task_exit"
}
trap on_failure ERR

docker compose config --quiet
echo "在当前运行版本上测量刷新请求链（只读业务数据，正常记录审计）"
docker compose cp backend/scripts/profile_startup.py api:/app/scripts/profile_startup.py
task_check_token="$(docker compose exec -T api python - <<'PY'
from sqlalchemy import select
from app.database import SessionLocal
from app.models import User
from app.roles import has_role
from app.security import make_token
with SessionLocal() as db:
    admin = next((u for u in db.scalars(select(User).where(User.active == True)) if has_role(u,'admin')),None)
    assert admin, '没有启用的管理员'
    print(make_token(admin.id))
PY
)"
printf '%s\n' "$task_check_token" | docker compose exec -T api python scripts/profile_startup.py --phase before --token-stdin --report /app/reports/startup-profile-before.json
echo "部署刷新性能优化，保留 MySQL、密钥卷和后台名称"
export BUILDKIT_PROGRESS=plain
docker compose up --build -d api
task_ready=0
for task_attempt in $(seq 1 30); do
  if docker compose exec -T api python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/api/health",timeout=2)' >/dev/null 2>&1; then
    task_ready=1
    break
  fi
  sleep 3
done
if [ "$task_ready" -ne 1 ]; then
  echo "API 未就绪。"
  false
fi
echo "使用升级前会话复测，并核对刷新、后台名称和审计落库"
printf '%s\n' "$task_check_token" | docker compose exec -T api python scripts/profile_startup.py --phase after --token-stdin --baseline /app/reports/startup-profile-before.json --report /app/reports/startup-profile-after.json
printf '%s\n' "$task_check_token" | docker compose exec -T api python scripts/verify_refresh.py --token-stdin --report /app/reports/refresh-verification.json
unset task_check_token
curl --fail --silent --show-error --max-time 8 http://127.0.0.1:8080/api/health
echo
docker compose ps -a
echo "完成。对比计时在 reports/startup-profile-after.json；请刷新当前项目页面体验。"
