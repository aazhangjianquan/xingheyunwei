"""Feishu task login; identities must already belong to an enabled platform account."""
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

import httpx
from cryptography.fernet import InvalidToken
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update

from .database import SessionLocal
from .models import LoginTicket, OAuthState, User
from .security import ACCOUNT_DISABLED_MESSAGE, decrypt_secret, encrypt_secret, make_token
from .settings import all_values, public_settings

RETIRED_MESSAGE = "飞书通用登录与自助绑定已停用，请使用账号密码登录；通知收件人由管理员配置"
COOKIE = 'yunwei_feishu_task'
COOKIE_PATH = '/api/auth'
STATE_SECONDS = 300
TICKET_SECONDS = 60
TASK_PATH = re.compile(r'/(?:resource|change|delivery|licenses)/[1-9][0-9]{0,18}')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def utc_now():
    # 历史状态表使用无时区 UTC 列，显式转换以兼容 MySQL 和 SQLite。
    return datetime.now(timezone.utc).replace(tzinfo=None)


def task_path(value):
    if not TASK_PATH.fullmatch(value):
        raise HTTPException(400, '任务地址无效，请从飞书任务消息重新打开')
    return value


def is_feishu(request):
    # 客户端标识仅决定交互入口，签发平台会话仍必须验证飞书官方凭据。
    return bool(re.search(r'(?:Feishu|Lark)/', request.headers.get('user-agent', ''), re.I))


def configured(settings):
    return bool(settings['feishu_task_login_enabled'] and settings['feishu_app_id'] and settings['feishu_app_secret'])


def browser_context(request):
    cookie = request.cookies.get(COOKIE, '')
    try:
        context = json.loads(decrypt_secret(cookie))
        task_path(context['next'])
        if not isinstance(context['nonce'], str) or len(context['nonce']) < 32:
            raise ValueError()
        if not isinstance(context['expires'], int) or context['expires'] <= int(time.time()):
            raise ValueError()
        return cookie, context
    except (InvalidToken, ValueError, TypeError, KeyError, HTTPException):
        raise HTTPException(400, '飞书登录已过期或浏览器不匹配，请从任务消息重试') from None


def finish_redirect(base, target, *, ticket=None, error=None):
    fragment = urlencode({'feishu_ticket': ticket} if ticket else {'feishu_error': error})
    response = RedirectResponse(f'{base}{target}#{fragment}', status_code=303)
    response.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})
    if not ticket:
        response.delete_cookie(COOKIE, path=COOKIE_PATH)
    return response


def feishu_identity(settings, code, redirect_uri):
    """Exchange a short-lived official code and read its actual app-scoped identity."""
    try:
        with httpx.Client(timeout=10) as client:
            response = client.post('https://open.feishu.cn/open-apis/authen/v2/oauth/token', json={
                'grant_type': 'authorization_code', 'client_id': settings['feishu_app_id'],
                'client_secret': settings['feishu_app_secret'], 'code': code, 'redirect_uri': redirect_uri,
            })
            response.raise_for_status()
            token = response.json()
            if token.get('code', 0) != 0 or not isinstance(token.get('access_token'), str) or not token['access_token']:
                raise ValueError()
            response = client.get('https://open.feishu.cn/open-apis/authen/v1/user_info',
                                  headers={'Authorization': 'Bearer ' + token['access_token']})
            response.raise_for_status()
            identity = response.json()
            data = identity.get('data')
            if (identity.get('code') != 0 or not isinstance(data, dict) or
                    not isinstance(data.get('open_id'), str) or not data['open_id'].startswith('ou_')):
                raise ValueError()
            if settings['feishu_tenant_key'] and data.get('tenant_key') != settings['feishu_tenant_key']:
                raise ValueError()
            return data['open_id']
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        # 不记录飞书原始返回、授权码、用户令牌或完整身份，避免进入日志。
        raise HTTPException(502, '飞书身份验证失败，请重试或使用账号密码登录') from None


class TicketIn(BaseModel):
    ticket: str = Field(min_length=32, max_length=200)


def register_routes(app, current_user, user_out):
    @app.get('/api/auth/options')
    def auth_options():
        with SessionLocal() as db:
            return public_settings(db)

    @app.get('/api/auth/feishu/start')
    def login_start():
        raise HTTPException(410, RETIRED_MESSAGE)

    @app.post('/api/auth/feishu/bind')
    def bind_start(user=Depends(current_user)):
        raise HTTPException(410, RETIRED_MESSAGE)

    @app.get('/api/auth/feishu/task/start')
    def task_start(request: Request, next: str):
        target = task_path(next)
        with SessionLocal() as db:
            settings = all_values(db)
            base = settings['public_base_url'].rstrip('/')
            if not is_feishu(request):
                # 外部浏览器沿用平台登录页面，不引导飞书扫码登录。
                return RedirectResponse(f'{base}{target}', status_code=303)
            if not configured(settings):
                return finish_redirect(base, target, error='unavailable')
            if request.headers.get('host', '').lower() != urlsplit(base).netloc.lower():
                # 旧消息可能使用 IP/旧域名；先到正式域名再设置浏览器 Cookie，避免回调丢失绑定。
                return RedirectResponse(base + '/api/auth/feishu/task/start?' + urlencode({'next': target}),
                                        status_code=303, headers={'Cache-Control': 'no-store',
                                                                  'Referrer-Policy': 'no-referrer'})
            stamp = utc_now()
            state = secrets.token_urlsafe(32)
            cookie = encrypt_secret(json.dumps({'nonce': secrets.token_urlsafe(32), 'next': target,
                                                'expires': int(time.time()) + STATE_SECONDS}))
            # 状态与票据存于共享数据库，各 API 副本均可处理后续步骤。
            db.execute(delete(OAuthState).where(OAuthState.expires_at <= stamp))
            db.execute(delete(LoginTicket).where(LoginTicket.expires_at <= stamp))
            db.add(OAuthState(state_hash=digest(state), browser_hash=digest(cookie), purpose='task',
                              expires_at=stamp + timedelta(seconds=STATE_SECONDS)))
            db.commit()
            callback = base + '/api/auth/feishu/callback'
            response = RedirectResponse('https://accounts.feishu.cn/open-apis/authen/v1/authorize?' + urlencode({
                'client_id': settings['feishu_app_id'], 'response_type': 'code',
                'redirect_uri': callback, 'state': state,
            }), status_code=303)
            response.set_cookie(COOKIE, cookie, max_age=STATE_SECONDS, httponly=True,
                                secure=urlsplit(base).scheme == 'https', samesite='lax', path=COOKIE_PATH)
            response.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})
            return response

    @app.get('/api/auth/feishu/callback')
    def callback(request: Request, state: str = '', code: str = '', error: str = ''):
        with SessionLocal() as db:
            settings = all_values(db)
            if not settings['feishu_task_login_enabled']:
                raise HTTPException(410, RETIRED_MESSAGE)
            base = settings['public_base_url'].rstrip('/')
            try:
                cookie, context = browser_context(request)
            except HTTPException:
                return finish_redirect(base, '/', error='expired')
            target = context['next']
            if not configured(settings):
                return finish_redirect(base, target, error='unavailable')
            if not state or len(state) > 200:
                return finish_redirect(base, target, error='expired')
            # 原子消费同时验证浏览器、用途和时效；并发回调最多成功一次。
            claimed = db.execute(update(OAuthState).where(
                OAuthState.state_hash == digest(state), OAuthState.browser_hash == digest(cookie),
                OAuthState.purpose == 'task', OAuthState.consumed.is_(False),
                OAuthState.expires_at > utc_now()).values(consumed=True))
            db.commit()
            if claimed.rowcount != 1:
                return finish_redirect(base, target, error='expired')
            if error or not code or len(code) > 2048:
                return finish_redirect(base, target, error='denied')
            try:
                open_id = feishu_identity(settings, code, base + '/api/auth/feishu/callback')
            except HTTPException:
                return finish_redirect(base, target, error='verification')
            # open_id 加密使用随机 IV，不能按密文等值查询；仅内存匹配，不向前端返回。
            people = [person for person in db.scalars(select(User).where(User.feishu_open_id.is_not(None)))
                      if hmac.compare_digest(digest(person.feishu_open_id), digest(open_id))]
            if len(people) != 1:
                return finish_redirect(base, target, error='unbound')
            person = people[0]
            if not person.active:
                return finish_redirect(base, target, error='disabled')
            # 票据还绑定认证时的身份和应用，兑换前解绑、换绑或切换应用都会使其失效。
            cookie = encrypt_secret(json.dumps({**context, 'identity': digest(open_id),
                                                'app': digest(settings['feishu_app_id'])}))
            ticket = secrets.token_urlsafe(32)
            db.add(LoginTicket(ticket_hash=digest(ticket + ':' + cookie), user_id=person.id,
                               expires_at=utc_now() + timedelta(seconds=TICKET_SECONDS)))
            db.commit()
            request.state.actor_id = person.id
            # 临时票据放 URL fragment，不进入 HTTP 请求路径、访问日志或 Referer。
            response = finish_redirect(base, target, ticket=ticket)
            response.set_cookie(COOKIE, cookie, max_age=TICKET_SECONDS, httponly=True,
                                secure=urlsplit(base).scheme == 'https', samesite='lax', path=COOKIE_PATH)
            return response

    @app.post('/api/auth/ticket')
    def consume_ticket(data: TicketIn, request: Request):
        with SessionLocal() as db:
            settings = all_values(db)
            if not settings['feishu_task_login_enabled']:
                raise HTTPException(410, RETIRED_MESSAGE)
            if not configured(settings):
                raise HTTPException(400, '飞书任务免密尚未配置，请联系管理员或使用账号密码登录')
            if request.headers.get('origin') != settings['public_base_url'].rstrip('/'):
                raise HTTPException(403, '登录请求来源无效，请从平台任务页面重试')
            cookie, context = browser_context(request)
            key = digest(data.ticket + ':' + cookie)
            claimed = db.execute(update(LoginTicket).where(LoginTicket.ticket_hash == key,
                LoginTicket.consumed.is_(False), LoginTicket.expires_at > utc_now()).values(consumed=True))
            if claimed.rowcount != 1:
                db.rollback()
                raise HTTPException(401, '飞书登录票据已失效，请从任务消息重试')
            ticket = db.get(LoginTicket, key)
            person = db.get(User, ticket.user_id)
            # 兑换时再次检查停用和身份绑定；已消费票据不能重复使用。
            if not person or not person.active or not person.feishu_open_id:
                db.commit()
                raise HTTPException(403, ACCOUNT_DISABLED_MESSAGE if person and not person.active else
                                    '此账号的飞书绑定已解除，请使用账号密码登录')
            if (not hmac.compare_digest(context.get('identity', ''), digest(person.feishu_open_id)) or
                    not hmac.compare_digest(context.get('app', ''), digest(settings['feishu_app_id']))):
                db.commit()
                raise HTTPException(403, '飞书身份配置已变化，请从任务消息重新登录')
            result = {'token': make_token(person.id), 'user': user_out(person), 'next': context['next']}
            db.commit()
            request.state.actor_id = person.id
            response = JSONResponse(result)
            response.delete_cookie(COOKIE, path=COOKIE_PATH)
            return response
