"""Loopback-only browser fixture with simulated Feishu identity; never sends messages."""
import os
from unittest.mock import patch

os.environ.update(REDIS_URL='', SEED_DEMO_DATA='true')
from test_auth_and_config import AuthConfigurationTests
from app.main import app


def run():
    AuthConfigurationTests.setUpClass()
    fixture = AuthConfigurationTests
    result = fixture.client.put('/api/admin/settings', headers=fixture.admin, json={'values': {
        'public_base_url': 'http://feishu-task.test', 'feishu_task_login_enabled': True,
        'feishu_notifications_enabled': False,
    }})
    assert result.status_code == 200, result.text
    item = fixture.client.post('/api/resources', headers=fixture.applicant, json={
        'project_id': fixture.project_id, 'project_name': '飞书任务免密验收', 'environment': '测试',
        'resource_type': 'Redis', 'specification': '1GB', 'start_date': '2026-11-09',
        'end_date': '2027-11-09', 'purpose': '隔离浏览器联调', 'capacity_basis': '10 个用户',
    })
    assert item.status_code == 200, item.text
    print('TASK_PATH=/resource/' + str(item.json()['id']), flush=True)
    import uvicorn
    try:
        with patch('app.auth_feishu.feishu_identity', return_value='ou_applicant'):
            uvicorn.run(app, host='127.0.0.1', port=18091, access_log=False, log_level='warning')
    finally:
        fixture.tearDownClass()


if __name__ == '__main__':
    run()
