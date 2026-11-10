"""Read-only preflight for legacy department labels; never starts the application."""
import argparse
import json
import sys
from collections import defaultdict, Counter
from pathlib import Path
from sqlalchemy import inspect, text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.database import engine
from app.organization import name_key, ancestor_ids


def inspect_organization(connection):
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    names, counts = defaultdict(set), {}
    for table in ('users', 'projects'):
        if table not in tables:
            continue
        values = Counter(connection.execute(text(f'SELECT department FROM {table}')).scalars())
        rows = [{'department': name, 'total': count} for name, count in values.items()]
        counts[table] = {'total': sum(r['total'] for r in rows),
                         'unassigned': sum(r['total'] for r in rows if not (r['department'] or '').strip())}
        for row in rows:
            if (row['department'] or '').strip():
                names[name_key(row['department'])].add(row['department'])
    conflicts = [sorted(values) for values in names.values() if len(values) > 1]
    report = {'mode': 'read_only', 'department_count': len(names), 'counts': counts, 'name_conflicts': conflicts,
              'organization_installed': 'departments' in tables,
              'status': 'needs_name_review' if conflicts else 'ready'}
    if 'departments' in tables:
        rows = connection.execute(text('SELECT id, name, approval_mode FROM departments ORDER BY id')).mappings().all()
        report['departments_pending_confirmation'] = [dict(r) for r in rows if r['approval_mode'] == 'legacy_roles']
    if 'department_user_positions' in tables:
        report['members_without_positions'] = connection.scalar(text('SELECT COUNT(*) FROM users u WHERE NOT EXISTS '
            '(SELECT 1 FROM department_user_positions p WHERE p.user_id=u.id)'))
        report['position_assignment_scope_installed'] = 'department_id' in {
            column['name'] for column in inspector.get_columns('department_user_positions')}
    if {'departments', 'department_positions'} <= tables:
        parents = dict(connection.execute(text('SELECT id, parent_id FROM departments')).all())
        positions = connection.execute(text('SELECT p.id, p.department_id, p.name, d.name AS department_name '
            'FROM department_positions p JOIN departments d ON d.id=p.department_id ORDER BY p.id')).mappings().all()
        by_name = defaultdict(list)
        for position in positions:
            by_name[name_key(position['name'])].append(position)
        report['existing_position_name_conflicts'] = [
            {'name': left['name'], 'positions': [dict(left), dict(right)]}
            for group in by_name.values() for index, left in enumerate(group) for right in group[index + 1:]
            if left['department_id'] in ancestor_ids(right['department_id'], parents)
            or right['department_id'] in ancestor_ids(left['department_id'], parents)]
        # 历史重名只报告，不合并岗位、不撤销授权，也不阻止无关的升级。
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    with engine.connect() as connection:
        result = inspect_organization(connection)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + '\n')
    print(rendered)
    raise SystemExit(0 if result['status'] == 'ready' else 2)
