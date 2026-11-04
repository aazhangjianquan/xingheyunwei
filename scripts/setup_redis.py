"""Keep literal Compose credentials; initialize .env only for legacy templates."""
from pathlib import Path
import os
import re
import secrets

root = Path(__file__).resolve().parents[1]
compose = (root / 'docker-compose.yml').read_text()
if '${REDIS_PASSWORD' not in compose:
    print('Redis 密码已直接填写在 docker-compose.yml，无需生成 .env；修改密码时同步修改 redis 和 api 两处。')
    raise SystemExit(0)
path = root / '.env'
source = path.read_text() if path.exists() else ''
if not os.getenv('REDIS_PASSWORD'):
    match = re.search(r'^REDIS_PASSWORD=(.*)$', source, re.M)
    if not match or not match.group(1).strip().strip('\"\''):
        source = re.sub(r'^REDIS_PASSWORD=.*\n?', '', source, flags=re.M)
        source = source.rstrip() + '\nREDIS_PASSWORD=' + secrets.token_urlsafe(48) + '\n'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as file:
            file.write(source)
        path.chmod(0o600)
print('Redis 部署凭据已就绪；原有配置保持不变。')
