from pathlib import Path
import os
import httpx
from fastapi import BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session
from . import settings
from .config import database_managed_by_environment, database_url, save_database_url
from .database import engine, get_db
from .feishu import send_notification
from .models import Approval, NotificationLog, User, WorkflowAssignment
from . import resource_catalog
from .workflows import FLOWS
from . import audit


class SettingsIn(BaseModel):
    values: dict


class RouteIn(BaseModel):
    object_type: str
    stage: str
    department: str = ""
    assignee_id: int


class DatabaseIn(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=3306, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=80)
    password: str = ""
    database: str = "yunweishenji"


def connection_summary(url):
    return {"host": url.host or "", "port": url.port or 3306, "username": url.username or "", "database": url.database or "", "password_set": bool(url.password)}


def register_routes(app, current_user, require_role):
    def admin(user=Depends(current_user)):
        require_role(user, "admin")
        return user

    @app.get("/api/admin/settings")
    def read_settings(db: Session = Depends(get_db), user=Depends(admin)):
        return settings.admin_settings(db)

    @app.put("/api/admin/settings")
    def write_settings(data: SettingsIn, db: Session = Depends(get_db), user=Depends(admin)):
        return settings.save_settings(db, data.values, user.id)

    @app.get("/api/admin/resource-catalog")
    def read_resource_catalog(db: Session = Depends(get_db), user=Depends(admin)):
        return {"items": resource_catalog.catalog(db, include_inactive=True)}

    @app.put("/api/admin/resource-catalog")
    def write_resource_catalog(data: dict, db: Session = Depends(get_db), user=Depends(admin)):
        return {"items": resource_catalog.save(db, data.get("items"), user)}

    @app.post("/api/admin/feishu/check")
    def check_feishu(db: Session = Depends(get_db), user=Depends(admin)):
        config = settings.all_values(db)
        if not config["feishu_app_id"] or not config["feishu_app_secret"]:
            raise HTTPException(400, "请先保存飞书应用凭据")
        try:
            with httpx.Client(timeout=12) as client:
                response = client.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal", json={"app_id": config["feishu_app_id"], "app_secret": config["feishu_app_secret"]})
                response.raise_for_status()
                if response.json().get("code") != 0:
                    raise ValueError("invalid credentials")
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, "飞书连接失败，请核对 App ID、App Secret 与网络")
        return {"message": "应用鉴权成功。请确认机器人已启用、应用已发布，并开通消息发送权限；无需配置登录回调地址。"}

    @app.get("/api/admin/workflows")
    def routes(db: Session = Depends(get_db), user=Depends(admin)):
        rows = db.scalars(select(WorkflowAssignment).order_by(WorkflowAssignment.object_type, WorkflowAssignment.id)).all()
        return [{"id": x.id, "object_type": x.object_type, "stage": x.stage, "department": x.department, "assignee_id": x.assignee_id, "assignee_name": db.get(User, x.assignee_id).display_name} for x in rows]

    @app.put("/api/admin/workflows")
    def save_route(data: RouteIn, db: Session = Depends(get_db), user=Depends(admin)):
        raise HTTPException(410, "审批人配置已停用，请使用审批流程编辑器发布版本")

    @app.post("/api/admin/approvals/{approval_id}/reassign")
    def reassign(approval_id: int, data: dict, tasks: BackgroundTasks, db: Session = Depends(get_db), user=Depends(admin)):
        from .flow_api import transfer
        from .main import OBJECTS, notify
        return transfer(db, approval_id, data, tasks, user, OBJECTS, notify)

    @app.post("/api/admin/notifications/{item_id}/retry")
    def retry_notification(item_id: int, tasks: BackgroundTasks, db: Session = Depends(get_db), user=Depends(admin)):
        item = db.scalar(select(NotificationLog).where(NotificationLog.id == item_id).with_for_update())
        if not item or item.status == "sent":
            raise HTTPException(400, "通知不存在或已经发送")
        if item.status == 'pending' and item.dispatch_ready:
            raise HTTPException(400, '通知已在队列中，请等待处理')
        item.status = "pending"
        item.error = ""
        item.attempt_count = 0
        item.next_attempt_at = None
        item.dispatch_ready = True
        audit.record(db, user, 'retry', 'notification', target_id=item.id,
                     target_name=f'{audit.TYPES.get(item.object_type, item.object_type)} #{item.object_id}', after={})
        db.commit()
        tasks.add_task(send_notification, item.id)
        return {"message": "已安排重试"}

    @app.get("/api/admin/database")
    def database_info(db: Session = Depends(get_db), user=Depends(admin)):
        active = connection_summary(engine.url)
        pending = connection_summary(make_url(database_url()))
        result = {"engine": engine.dialect.name, "active": active, "configured": pending, "pending_restart": engine.url != make_url(database_url()),
                  'managed_by_environment': database_managed_by_environment()}
        sql_dir = Path(os.getenv('SQL_DIR', Path(__file__).resolve().parents[2] / 'database'))
        result['sql_files'] = sorted(path.name for path in sql_dir.glob('*.sql') if path.is_file())
        if engine.dialect.name == "mysql":
            result["server_version"] = db.execute(text("SELECT VERSION()")).scalar()
            result["table_count"] = db.execute(text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE()" )).scalar()
        return result

    def candidate(data):
        if data.database != "yunweishenji":
            raise HTTPException(400, "数据库名必须为 yunweishenji")
        stored = make_url(database_url())
        password = data.password
        if not password and data.host == stored.host and data.username == stored.username:
            password = stored.password
        return URL.create("mysql+pymysql", username=data.username, password=password, host=data.host, port=data.port, database=data.database, query={"charset": "utf8mb4"})

    def test_connection(data):
        url = candidate(data)
        probe = create_engine(url, connect_args={"connect_timeout": 5}, pool_pre_ping=True)
        try:
            with probe.connect() as conn:
                name, version = conn.execute(text("SELECT DATABASE(), VERSION()")).one()
            return url, {"message": "MySQL 连接成功", "database": name, "server_version": version}
        except Exception:
            raise HTTPException(400, "连接失败，请检查数据库地址、账号、密码和数据库是否已创建")
        finally:
            probe.dispose()

    @app.post("/api/admin/database/check")
    def check_database(data: DatabaseIn, user=Depends(admin)):
        return test_connection(data)[1]

    @app.put("/api/admin/database")
    def save_database(data: DatabaseIn, db: Session = Depends(get_db), user=Depends(admin)):
        if database_managed_by_environment():
            raise HTTPException(409, '数据库由部署 Secret 统一管理，请更新 DATABASE_URL 后滚动重启 API 与 Worker')
        url, result = test_connection(data)
        save_database_url(url.render_as_string(hide_password=False))
        audit.record(db, user, 'configure', 'settings', target_id='database', target_name='数据库连接',
                     after={}, extra={'database': (None, '已更新')})
        db.commit()
        return {**result, "message": "连接配置已加密保存；重启 API 后生效。已有数据不会自动迁移。"}

    @app.get("/api/admin/sql/{filename}")
    def download_sql(filename: str, user=Depends(admin)):
        if filename not in {"v1.0.0.sql", "schema.sql", "demo_data.sql", "upgrade_v2.sql", "upgrade_v3.sql", "upgrade_license.sql", "upgrade_license_approval.sql", "upgrade_license_project.sql", "upgrade_audit.sql", "upgrade_request_limits.sql", "upgrade_delivery_attachments.sql", "upgrade_attachment_minio.sql", "upgrade_feishu_open_id.sql", "upgrade_wiki.sql", "install.sql"}:
            raise HTTPException(404, "SQL 文件不存在")
        path = Path(os.getenv("SQL_DIR", Path(__file__).resolve().parents[2] / "database")) / filename
        if not path.exists():
            raise HTTPException(404, "此部署中没有 SQL 文件")
        return FileResponse(path, media_type="application/sql", filename=filename)
