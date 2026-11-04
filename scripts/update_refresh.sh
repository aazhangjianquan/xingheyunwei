#!/usr/bin/env bash
# Keep credentials, configured site name, origin and business records intact.
set -Eeuo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
mkdir -p reports
exec > >(tee reports/refresh-update.log) 2>&1
on_failure() {
  task_exit=$?
  trap - ERR
  echo "更新未完成（${task_exit}）；日志：reports/refresh-update.log"
  docker compose ps -a || true
  docker compose logs --no-color --tail 80 api || true
  exit "$task_exit"
}
trap on_failure ERR

docker compose config --quiet
echo "保留后台名称与现有会话密钥，更新刷新恢复逻辑"
# Capture a session signed by the old container without printing or writing it.
task_check_token="$(docker compose exec -T api python - <<'PY'
from sqlalchemy import select
from app.database import SessionLocal
from app.models import User
from app.roles import has_role
from app.security import make_token
with SessionLocal() as db:
    admin = next((u for u in db.scalars(select(User).where(User.active == True)) if has_role(u, 'admin')), None)
    assert admin, '没有启用管理员'
    print(make_token(admin.id))
PY
)"
export BUILDKIT_PROGRESS=plain
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
  echo "API 未就绪。"
  false
fi
printf '%s\n' "$task_check_token" | docker compose exec -T api python scripts/verify_refresh.py --token-stdin --report /app/reports/refresh-verification.json
unset task_check_token
curl --fail --silent --show-error --max-time 8 http://127.0.0.1:8080/api/health
echo
docker compose ps -a
echo "完成。请在当前浏览器页面刷新；名称以后台保存值为准。报告：reports/refresh-verification.json"
