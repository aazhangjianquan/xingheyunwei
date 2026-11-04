"""Compatibility routes for installations upgraded to password login and Feishu notifications."""
from fastapi import Depends, HTTPException
from .database import SessionLocal
from .settings import public_settings

RETIRED_MESSAGE = "飞书登录与自助绑定已停用，请使用账号密码登录；通知收件人由管理员配置"


def register_routes(app, current_user, user_out):
    @app.get("/api/auth/options")
    def auth_options():
        with SessionLocal() as db:
            return public_settings(db)

    @app.get("/api/auth/feishu/start")
    def login_start():
        raise HTTPException(410, RETIRED_MESSAGE)

    @app.post("/api/auth/feishu/bind")
    def bind_start(user=Depends(current_user)):
        raise HTTPException(410, RETIRED_MESSAGE)

    @app.get("/api/auth/feishu/callback")
    def callback():
        raise HTTPException(410, RETIRED_MESSAGE)

    @app.post("/api/auth/ticket")
    def consume_ticket():
        raise HTTPException(410, RETIRED_MESSAGE)
