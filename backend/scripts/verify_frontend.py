"""Read-only deployment check for the Vue page and its referenced assets."""
import argparse
import hashlib
import json
from html.parser import HTMLParser
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import urlopen


class PageAssets(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.paths = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        path = None
        if tag == "script":
            path = attrs.get("src")
        elif tag == "link" and {"stylesheet", "icon"}.intersection(attrs.get("rel", "").split()):
            path = attrs.get("href")
        if path:
            self.paths.append(path)


def fetch(base_url, path):
    with urlopen(base_url.rstrip("/") + path, timeout=15) as response:
        return response.read()


def verify(base_url, profile=False, attachments=False):
    public_dir = Path(__file__).resolve().parents[1] / "public"
    expected = PageAssets((public_dir / "index.html").read_text(encoding="utf-8")).paths
    if not expected or not any(path.endswith(".js") for path in expected):
        raise ValueError("镜像内缺少 Vue 构建资源，请重新构建 API 镜像")
    actual = PageAssets(fetch(base_url, "/").decode("utf-8")).paths
    if expected != actual:
        raise ValueError("运行中的页面与镜像构建不一致，请检查访问地址、反向代理及 API 是否已更新")
    scripts = []
    for path in expected:
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or not path.startswith("/assets/") or ".." in parsed.path.split("/"):
            raise ValueError("页面包含非预期的构建资源地址")
        packaged = (public_dir / parsed.path.lstrip("/")).read_bytes()
        served = fetch(base_url, path)
        if packaged != served:
            raise ValueError(f"运行中的资源与镜像不一致：{path}")
        if path.endswith(".js"):
            scripts.append(served.decode("utf-8"))
        print(f"资源一致：{path} · SHA256 {hashlib.sha256(served).hexdigest()[:12]}")
    javascript = "\n".join(scripts)
    for marker in ("role-card-grid", "设置至少 12 位的初始密码", "member-password-rules"):
        if marker not in javascript:
            raise ValueError("页面仍缺少新版角色选择或密码规则，请使用当前源码重新构建")
    checks = ['运行页面引用当前镜像的构建资源', 'JS/CSS 内容与镜像逐字节一致', '新版角色选择与强密码规则存在']
    schema = json.loads(fetch(base_url, '/openapi.json')) if profile or attachments else {}
    if profile:
        for marker in ('个人资料与安全', 'profile-avatar', 'account-title', 'account-security', '/me/password'):
            if marker not in javascript:
                raise ValueError(f'运行中的前端缺少个人资料功能：{marker}')
        paths = schema.get('paths', {})
        for path, method, fields in [('/api/me', 'patch', {'display_name'}),
                                     ('/api/me/password', 'post', {'current_password', 'new_password'})]:
            operation = paths.get(path, {}).get(method)
            if not operation:
                raise ValueError(f'运行中的 API 缺少个人资料接口：{method.upper()} {path}')
            body = operation['requestBody']['content']['application/json']['schema']
            if '$ref' in body:
                body = schema['components']['schemas'][body['$ref'].rsplit('/', 1)[-1]]
            if set(body.get('properties', {})) != fields or set(body.get('required', [])) != fields or body.get('additionalProperties') is not False:
                raise ValueError(f'个人资料接口字段不符合预期：{path}')
        checks += ['右上角个人资料入口及姓名/密码表单已打包', '运行中的姓名与密码接口存在，且仅接受各自允许的字段']
        print('核对通过：运行中的前端和 API 已包含个人资料与密码修改功能。')
    elif not attachments:
        print("核对通过：当前 API 已提供角色卡片多选和至少 12 位密码规则的新版页面。")
    if attachments:
        for marker in ('delivery-attachments', '添加附件', '下载原文件', 'attachment-preview-panel', '/attachments'):
            if marker not in javascript:
                raise ValueError(f'运行中的前端缺少交付附件功能：{marker}')
        for marker in ('随申请提交交付方案', '已选附件', '完成交付'):
            if marker not in javascript:
                raise ValueError(f'运行中的前端缺少交付创建附件功能：{marker}')
        if '数据库账号回填' in javascript or 'submitCredential' in javascript:
            raise ValueError('运行中的前端仍包含旧版数据库账号回填，请重新构建 API 镜像')
        paths = schema.get('paths', {})
        root = '/api/deliveries/{item_id}/attachments'
        for path, method in [(root, 'get'), (root, 'post'),
                             (root + '/{attachment_id}/download', 'get'),
                             (root + '/{attachment_id}/preview', 'get')]:
            if method not in paths.get(path, {}):
                raise ValueError(f'运行中的 API 缺少交付附件接口：{method.upper()} {path}')
        retired = paths.get('/api/deliveries/{item_id}/credentials', {}).get('post', {})
        if not retired.get('deprecated') or '410' not in retired.get('responses', {}):
            raise ValueError('运行中的 API 尚未取消数据库账号回填，请更新 API 镜像')
        from sqlalchemy import inspect
        from app.database import engine
        database = inspect(engine)
        if not database.has_table('delivery_attachments'):
            raise ValueError('数据库缺少交付附件表，请检查启动迁移')
        columns = {column['name'] for column in database.get_columns('delivery_attachments')}
        if not {'id', 'delivery_id', 'uploaded_by', 'filename', 'storage_key', 'storage_backend', 'storage_bucket', 'size', 'sha256', 'extension', 'content_type', 'created_at'} <= columns:
            raise ValueError('数据库缺少完整的交付附件表，请检查启动迁移')
        from app.attachment_storage import MinioDocuments, upload_backend
        from fastapi import HTTPException
        try:
            if upload_backend() == 'minio':
                MinioDocuments().check_bucket()
                checks.append('当前 API 的 MinIO 私有桶连接及访问检查通过')
        except HTTPException as exc:
            raise ValueError(exc.detail) from None
        binary = os.getenv('SOFFICE_BIN') or shutil.which('libreoffice') or shutil.which('soffice')
        if not binary:
            raise ValueError('API 环境缺少 LibreOffice，无法预览 Word 和 Excel；请重新构建 API 镜像')
        try:
            runtime = subprocess.run([binary, '--version'], stdin=subprocess.DEVNULL,
                                     capture_output=True, timeout=15, check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError('LibreOffice 无法运行，请检查 API 镜像中的文档预览依赖') from exc
        checks += ['交付附件上传、列表、下载及预览入口已打包', '运行中的交付附件四类接口存在',
                   '创建表单已提供附件选择，数据库账号回填入口及新增接口已停用',
                   '当前数据库的交付附件表结构完整', '当前 API 环境的 LibreOffice 可运行']
        print('预览运行时：' + runtime.stdout.decode('utf-8', errors='replace').strip())
        print('核对通过：交付附件页面、接口、数据库与本地文档预览运行时均已就绪。')
    return {'passed': True, 'checks': checks, 'assets': expected}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="要核对的页面地址")
    parser.add_argument('--profile', action='store_true', help='额外检查个人资料页面及姓名、密码接口结构；不修改任何账号')
    parser.add_argument('--attachments', action='store_true', help='检查交付附件页面、接口、数据库表、MinIO 连接及 LibreOffice；不上传文件或修改业务数据')
    parser.add_argument('--report', help='可选 JSON 验收报告路径')
    args = parser.parse_args()
    report = {'passed': False}
    exit_code = 0
    try:
        report = verify(args.base_url, profile=args.profile, attachments=args.attachments)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report['error'] = str(exc)
        print(f"前端核对失败：{exc}", file=sys.stderr)
        exit_code = 1
    if args.report:
        output = Path(args.report)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    sys.exit(exit_code)
