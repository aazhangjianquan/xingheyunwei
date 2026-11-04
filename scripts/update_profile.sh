#!/usr/bin/env bash
# Update an existing local Compose deployment without replacing its data volumes.
set -Eeuo pipefail
umask 077
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
task_mode=profile
if [ "${1:-}" = '--attachments' ] && [ "$#" -eq 1 ]; then
  task_mode=attachments
elif [ "$#" -ne 0 ]; then
  echo "用法：bash scripts/update_profile.sh [--attachments]"
  exit 2
fi
mkdir -p reports
exec > >(tee "reports/${task_mode}-update.log") 2>&1
task_api_stopped=0
task_update_started=0
task_backup_dir=''

on_exit() {
  task_exit=$?
  trap - EXIT
  if [ "$task_exit" -ne 0 ]; then
    echo "更新未完成（退出码 ${task_exit}），日志：reports/${task_mode}-update.log"
    if [ "$task_api_stopped" -eq 1 ] && [ "$task_update_started" -eq 0 ]; then
      echo "备份尚未完成，恢复原 API 容器；未替换镜像。"
      docker compose start api || true
    fi
    [ -z "$task_backup_dir" ] || echo "本次备份目录：${task_backup_dir}"
    docker compose ps -a || true
    docker compose logs --no-color --tail 60 api || true
  fi
  exit "$task_exit"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "检查当前项目与 Docker 服务"
docker compose config --quiet
docker info --format 'Docker server: {{.ServerVersion}}'
task_api_id="$(docker compose ps -q api)"
task_mysql_id="$(docker compose ps -q mysql)"
if [ -z "$task_api_id" ] || [ -z "$task_mysql_id" ]; then
  echo "本脚本用于升级已有平台，需要当前项目的 api 和 mysql 容器正在运行。"
  exit 1
fi
# Never claim to back up the application if its actual DB has been switched elsewhere.
docker compose exec -T api python - <<'PY'
from app.database import engine
if not (engine.dialect.name == 'mysql' and engine.url.host == 'mysql' and engine.url.database == 'yunweishenji'):
    raise SystemExit('当前平台使用自定义数据库，本脚本尚未备份该数据库，已在更新前停止。请先按实际数据库制定备份方案。')
print('确认平台使用本项目的 MySQL 数据库。')
PY

echo "先构建前端和 API 镜像，构建期间原平台保持运行"
export BUILDKIT_PROGRESS=plain
docker compose build api
if [ "$task_mode" = attachments ]; then
  echo "更新前检查新版 API 镜像能否访问 MinIO 私有桶"
  docker compose run --rm --no-deps -T api python -c 'from app.attachment_storage import MinioDocuments; MinioDocuments().check_bucket(); print("MinIO 私有桶连接检查通过")'
fi

task_stamp="$(date +%Y%m%d-%H%M%S)"
mkdir -p reports/profile-backups
task_backup_dir="$(mktemp -d "reports/profile-backups/${task_stamp}-XXXXXX")"
docker inspect --format '{{.Image}}' "$task_api_id" > "$task_backup_dir/previous-api-image.txt"
echo "短暂停止 API 写入，备份数据库、主密钥及全部交付附件"
task_api_stopped=1
docker compose stop api
docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; export MYSQL_PWD; exec mysqldump -uroot --single-transaction --no-tablespaces --set-gtid-purged=OFF --databases yunweishenji' > "$task_backup_dir/database.sql"
test -s "$task_backup_dir/database.sql"
docker compose cp api:/app/instance "$task_backup_dir/instance"
test -s "$task_backup_dir/instance/keys.json"
chmod -R go-rwx "$task_backup_dir"
echo "数据库与完整实例目录已备份：${task_backup_dir}"

echo "更新 API，沿用现有 MySQL 和实例数据卷"
task_update_started=1
docker compose up -d --no-deps api
task_api_stopped=0
task_ready=0
for task_attempt in $(seq 1 30); do
  if docker compose exec -T api python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=2)' >/dev/null 2>&1; then
    task_ready=1
    break
  fi
  sleep 2
done
if [ "$task_ready" -ne 1 ]; then
  echo "API 未在等待时间内就绪，请检查日志；备份已保留。"
  exit 1
fi

task_verify_args=(--profile)
if [ "$task_mode" = attachments ]; then
  task_verify_args+=(--attachments)
  echo "复制并校验历史本地附件后切换至 MinIO，保留本地原件；此步骤可重复执行"
  docker compose exec -T api python scripts/migrate_attachments_to_minio.py
fi
echo "核对运行中的页面、接口与所选功能依赖"
docker compose exec -T api python scripts/verify_frontend.py "${task_verify_args[@]}" --report "/app/reports/${task_mode}-verification.json"
docker compose ps -a
echo "部署完成：http://localhost:8080"
if [ "$task_mode" = attachments ]; then
  echo "刷新页面后，创建私有化交付单时即可选择项目附件；提交后自动上传，详情支持预览、下载与补充附件。"
else
  echo "刷新页面后，所有角色均可点击右上角头像 → 个人资料与安全。"
fi
echo "日志：reports/${task_mode}-update.log；验收报告：reports/${task_mode}-verification.json"
