"""Read-only acceptance of the deployed list APIs against actual MySQL rows.

No demo users/orders or workflow revisions are created. HTTP GETs are audited by
application policy. Run in api: python scripts/verify_pagination.py
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from sqlalchemy import select, text
from app.database import engine, SessionLocal
from app.main import OBJECTS, visible
from app.models import User
from app.roles import has_role
from app.security import make_token

parser = argparse.ArgumentParser()
parser.add_argument('--base-url', default='http://127.0.0.1:8000')
parser.add_argument('--report', default='/app/reports/pagination-verification.json')
args = parser.parse_args()
if engine.dialect.name != 'mysql':
    raise SystemExit('此验收需要真实 MySQL；请在 api 容器执行。')
report = {'database_engine': engine.dialect.name, 'cases': [], 'passed': False}


def fetch(path, token, **query):
    url = args.base_url.rstrip('/') + '/api/' + path + '?' + urlencode(query)
    request = Request(url, headers={'Authorization': 'Bearer ' + token})
    with urlopen(request, timeout=15) as response:
        result = json.load(response)
    assert isinstance(result, dict) and 'counts' in result and 'items' in result, '部署的 API 尚未更新分页功能'
    return result


try:
    with SessionLocal() as db:
        people = db.scalars(select(User).where(User.active == True).order_by(User.id)).all()
        admin = next((p for p in people if has_role(p, 'admin')), None)
        assert admin, '没有已启用管理员'
        token = make_token(admin.id)
        report['database_name'], report['mysql_version'] = db.execute(text('SELECT DATABASE(), VERSION()')).one()
        for kind, model in OBJECTS.items():
            path = {'resource': 'resources', 'change': 'changes', 'delivery': 'deliveries'}[kind]
            rows = db.execute(select(model.id, model.status, model.code).order_by(model.id.desc())).all()
            ids = [r.id for r in rows]
            counts = {'all': len(rows), 'pending': 0, 'in_progress': 0, 'completed': 0, 'rejected': 0}
            for r in rows: counts[r.status] += 1
            case = {'kind': kind, 'counts': counts, 'sizes': [], 'checks': []}
            for size in [10, 20, 50]:
                first = fetch(path, token, page=1, page_size=size)
                assert first['page_size'] == size and first['counts'] == counts and first['total'] == len(rows)
                assert len(first['items']) == min(size, len(rows))
                actual = []
                for page in range(1, first['total_pages'] + 1):
                    result = first if page == 1 else fetch(path, token, page=page, page_size=size)
                    assert result['page'] == page and result['counts'] == counts
                    actual.extend(item['id'] for item in result['items'])
                assert actual == ids, f'{path} 每页 {size} 条出现遗漏或重复'
                case['sizes'].append(size)
            for status in ['pending', 'in_progress', 'completed', 'rejected']:
                result = fetch(path, token, status=status, page_size=50)
                expected = [r.id for r in rows if r.status == status]
                assert result['total'] == len(expected) and result['counts'] == counts
                assert [x['id'] for x in result['items']] == expected[:50]
            case['checks'].append('各状态计数覆盖完整列表，切换状态与页数不改变计数')
            last = fetch(path, token, page=999999, page_size=20)
            assert last['page'] == last['total_pages'] and len(last['items']) <= 20
            empty = fetch(path, token, q='pagination-no-match-c1a274c865664c09', page=99)
            assert empty['items'] == [] and empty['total'] == 0 and empty['page'] == 1
            assert all(n == 0 for n in empty['counts'].values())
            if rows:
                result = fetch(path, token, q=rows[-1].code, page_size=10)
                expected = [r.id for r in rows if rows[-1].code.lower() in r.code.lower()]
                assert result['total'] == len(expected)
                assert [x['id'] for x in result['items']] == expected[:10]
            case['checks'].append('越界页、空结果、跨页搜索')
            member = next((p for p in people if not has_role(p, 'admin', 'ops_head', 'leader', 'operator')), None)
            if member:
                expected = db.scalars(visible(select(model.id).order_by(model.id.desc()), model, member)).all()
                result = fetch(path, make_token(member.id), page_size=10)
                assert result['total'] == result['counts']['all'] == len(expected)
                assert [x['id'] for x in result['items']] == expected[:10]
                case['checks'].append('普通成员列表与统计权限隔离')
            report['cases'].append(case)
        report['passed'] = True
finally:
    target = Path(args.report)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
