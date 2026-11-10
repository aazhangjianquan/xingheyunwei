"""Card content checks use saved messages and fixed clocks, without sending messages."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json
import unittest

from app.notification_cards import REQUEST_LABELS, PROGRESS_EVENTS, message_content


class NotificationCardTests(unittest.TestCase):
    def setUp(self):
        self.settings = {'site_name': '星河项目协作空间', 'public_base_url': 'https://ops.example.com/'}
        self.stamp = datetime(2026, 11, 5, 2, 35, tzinfo=timezone.utc)

    def approval(self, kind='license', *, execution=False, project='灵搭'):
        headline = '您有一项待执行任务' if execution else '您有一项待审批申请'
        return SimpleNamespace(event='approval_required', object_type=kind, object_id=2,
            message=f'【旧站点名称】{headline}\n申请类型：{REQUEST_LABELS[kind]}\n'
                    f'申请编号：LIC-2026-0002\n项目 / 标题：{project}\n申请人：测试账号\n'
                    '待处理环节：最终审批\n请登录平台查看并处理。\nhttps://old.example.com/licenses/2')

    def reminder(self, *, days=7, late=True):
        item = SimpleNamespace(code='LIC-2026-0002', project_name='灵搭', customer='测试客户', product='测试产品',
                               license_key_ciphertext='must-not-appear-in-card')
        plan = SimpleNamespace(rule='license_7d', object_type='license',
            expires_at=self.stamp + timedelta(days=days),
            scheduled_at=self.stamp - timedelta(hours=2) if late else self.stamp)
        log = SimpleNamespace(event=plan.rule, object_type='license', object_id=2, message='原提醒文本')
        return log, (item, plan, None)

    def progress(self, event='submitted', kind='resource', *, project='袋袋电商', reason='请补充用途\n保留第二行意见'):
        detail = ('当前环节：部门负责人审批\n下一位处理人：邹鹏飞' if event in
                  {'submitted', 'status_changed', 'approval_reassigned'} else
                  '请登录平台查看审批意见，修改后可重新提交。\n驳回原因：' + reason if event == 'rejected' else
                  '审批已通过，License 已生成，请登录平台查看。' if kind == 'license' else
                  '全部审批与执行环节已完成，请登录平台查看结果。')
        return SimpleNamespace(event=event, object_type=kind, object_id=3,
            message=f'【旧平台名称】{PROGRESS_EVENTS[event][0]}\n申请类型：{REQUEST_LABELS[kind]}\n'
                    f'申请编号：RES-2026-0003\n项目 / 标题：{project}\n申请人：张建全\n{detail}\n'
                    'https://old.example.com/resource/3')

    def content_strings(self, obj):
        if isinstance(obj, dict):
            return [obj['content']] if obj.get('tag') == 'plain_text' else [
                text for value in obj.values() for text in self.content_strings(value)]
        if isinstance(obj, list):
            return [text for value in obj for text in self.content_strings(value)]
        return []

    def test_all_pending_approval_types_use_snapshot_fields_and_current_detail_address(self):
        for kind, label in REQUEST_LABELS.items():
            with self.subTest(kind=kind):
                log = self.approval(kind)
                before = log.message
                msg_type, card = message_content(log, self.settings)
                self.assertEqual(msg_type, 'interactive')
                self.assertEqual(card['schema'], '2.0')
                self.assertEqual(card['header']['template'], 'blue')
                self.assertEqual(card['header']['title']['content'], '待审批 · ' + label)
                strings = self.content_strings(card)
                for field in ['灵搭', 'LIC-2026-0002', '测试账号', '最终审批']:
                    self.assertIn(field, strings)
                button = next(e for e in card['body']['elements'] if e['tag'] == 'button')
                path = 'licenses' if kind == 'license' else kind
                self.assertEqual(button['behaviors'], [
                    {'type': 'open_url', 'default_url': f'https://ops.example.com/{path}/2'}])
                self.assertEqual(log.message, before)

    def test_execution_has_a_processing_action_instead_of_an_approval_action(self):
        _, card = message_content(self.approval('resource', execution=True), self.settings)
        strings = self.content_strings(card)
        self.assertIn('待执行 · 资源申请', strings)
        self.assertIn('查看任务并处理', strings)
        self.assertNotIn('查看申请并审批', strings)

    def test_task_login_entry_marker_contains_only_task_address_and_respects_switch(self):
        for enabled in [True, False]:
            config = {**self.settings, 'feishu_task_login_enabled': enabled}
            for kind in REQUEST_LABELS:
                _, card = message_content(self.approval(kind), config)
                button = next(e for e in card['body']['elements'] if e['tag'] == 'button')
                section = 'licenses' if kind == 'license' else kind
                expected = f'https://ops.example.com/{section}/2' + ('?feishu_task=1' if enabled else '')
                self.assertEqual(button['behaviors'][0]['default_url'], expected)
            log, context = self.reminder()
            _, card = message_content(log, config, context=context, stamp=self.stamp)
            button = next(e for e in card['body']['elements'] if e['tag'] == 'button')
            self.assertEqual(button['behaviors'][0]['default_url'], 'https://ops.example.com/licenses/2')

    def test_all_four_application_types_and_five_progress_events_use_consistent_cards(self):
        for kind in REQUEST_LABELS:
            for event, (_, label, color, button_label) in PROGRESS_EVENTS.items():
                with self.subTest(kind=kind, event=event):
                    log = self.progress(event, kind)
                    before = log.message
                    transport, card = message_content(log, {**self.settings, 'feishu_task_login_enabled': True})
                    self.assertEqual(transport, 'interactive')
                    self.assertEqual(card['header']['title']['content'], f'{label} · {REQUEST_LABELS[kind]}')
                    self.assertEqual(card['header']['template'], color)
                    strings = self.content_strings(card)
                    for content in ['袋袋电商', 'RES-2026-0003', '张建全', REQUEST_LABELS[kind], button_label]:
                        self.assertIn(content, strings)
                    if event in {'submitted', 'status_changed', 'approval_reassigned'}:
                        for content in ['当前环节', '部门负责人审批', '下一位处理人', '邹鹏飞']:
                            self.assertIn(content, strings)
                    if event == 'rejected':
                        self.assertIn('驳回原因', strings)
                        self.assertIn('请补充用途\n保留第二行意见', strings)
                    if event == 'completed' and kind == 'license':
                        self.assertIn('审批已通过，License 已生成，请登录平台查看。', strings)
                    button = next(e for e in card['body']['elements'] if e['tag'] == 'button')
                    section = 'licenses' if kind == 'license' else kind
                    self.assertEqual(button['behaviors'][0]['default_url'],
                                     f'https://ops.example.com/{section}/3?feishu_task=1')
                    self.assertEqual(log.message, before)

    def test_progress_legacy_multiline_and_unknown_layout_preserve_all_text_inside_card(self):
        multiline = self.progress(project='项目第一行\n项目第二行')
        unknown = self.progress()
        unknown.message = '【历史平台】旧格式审批进度\n所有历史内容必须保留\nhttps://old.example.com/resource/3'
        for log in [multiline, unknown]:
            transport, card = message_content(log, self.settings)
            self.assertEqual(transport, 'interactive')
            self.assertIn(log.message, self.content_strings(card))

    def test_progress_fields_and_multiline_rejection_are_plain_text_not_markup(self):
        value = '<at id=all></at> **项目** [链接](https://untrusted.example)'
        for log in [self.progress(project=value), self.progress('rejected', reason=value + '\n第二行')]:
            transport, card = message_content(log, self.settings)
            self.assertEqual(transport, 'interactive')
            self.assertTrue(any(value in content for content in self.content_strings(card)))
            def check(node):
                if isinstance(node, dict):
                    self.assertNotIn(node.get('tag'), {'markdown', 'lark_md'})
                    for child in node.values(): check(child)
                elif isinstance(node, list):
                    for child in node: check(child)
            check(card)

    def test_business_markup_is_literal_and_cannot_create_links_or_mentions(self):
        project = '<at id=all></at> **项目** [钓鱼链接](https://untrusted.example)'
        _, card = message_content(self.approval(project=project), self.settings)
        self.assertIn(project, self.content_strings(card))
        def check(node):
            if isinstance(node, dict):
                self.assertNotIn(node.get('tag'), {'markdown', 'lark_md'})
                for value in node.values(): check(value)
            elif isinstance(node, list):
                for value in node: check(value)
        check(card)

    def test_legacy_multiline_or_mismatched_messages_fall_back_without_losing_text(self):
        mismatched = self.approval()
        mismatched.message = mismatched.message.replace('申请类型：License 授权', '申请类型：未知类型')
        legacy = self.approval()
        legacy.message = '【旧版】LIC-2026-0002 有待办需要您处理\nhttps://ops.example.com/licenses/2'
        for log in [self.approval(project='多行\n项目'), mismatched, legacy]:
            self.assertEqual(message_content(log, self.settings), ('text', {'text': log.message}))

    def test_other_events_and_unsafe_root_addresses_keep_existing_text_delivery(self):
        for event in ['request_rate_exceeded', 'unknown_event']:
            log = self.approval()
            log.event = event
            self.assertEqual(message_content(log, self.settings), ('text', {'text': log.message}))
        for base in ['javascript:alert(1)', 'https://name:password@ops.example.com',
                     'https://ops.example.com?other=1', 'http://[broken-host']:
            log = self.approval()
            self.assertEqual(message_content(log, {**self.settings, 'public_base_url': base}),
                             ('text', {'text': log.message}))

    def test_license_countdown_and_beijing_time_come_from_the_delivery_clock(self):
        log, context = self.reminder()
        context[1].expires_at = datetime(2026, 11, 12, 2, 30)  # DB timestamps can be naive UTC.
        msg_type, card = message_content(log, self.settings, context=context, stamp=self.stamp)
        self.assertEqual(msg_type, 'interactive')
        self.assertEqual(card['header']['template'], 'orange')
        strings = self.content_strings(card)
        for value in ['6 天 23 小时', '2026-11-12 10:30:00', '到期时间 · 北京时间', '测试客户', '测试产品',
                      '本次为生效或恢复后的补提醒。', '查看 License 详情']:
            self.assertIn(value, strings)
        self.assertNotIn('must-not-appear-in-card', json.dumps(card))
        _, later = message_content(log, self.settings, context=context, stamp=self.stamp + timedelta(hours=3))
        self.assertIn('6 天 20 小时', self.content_strings(later))

    def test_on_time_reminders_keep_the_node_label_and_sub_hour_precision(self):
        for minutes, expected in [(1, '1 分钟'), (59, '59 分钟'), (60, '1 小时 0 分钟'),
                                  (1439, '23 小时 59 分钟'), (1440, '1 天 0 小时')]:
            with self.subTest(minutes=minutes):
                log, context = self.reminder(late=False)
                context[1].expires_at = self.stamp + timedelta(minutes=minutes)
                _, card = message_content(log, self.settings, context=context, stamp=self.stamp)
                self.assertIn(expected, self.content_strings(card))
                self.assertIn('本次提醒：到期前 7 天', self.content_strings(card))

    def test_resource_reminders_and_unlinked_license_messages_stay_as_text(self):
        log, context = self.reminder()
        self.assertEqual(message_content(log, self.settings), ('text', {'text': log.message}))
        log.object_type, log.event, context[1].object_type, context[1].rule = 'resource', 'resource_7d', 'resource', 'resource_7d'
        self.assertEqual(message_content(log, self.settings, context=context, stamp=self.stamp),
                         ('text', {'text': log.message}))
