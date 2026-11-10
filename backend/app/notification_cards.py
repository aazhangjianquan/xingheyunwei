"""Presentation-only Feishu cards; durable notification text remains the audit source."""
import re
from datetime import timedelta
from urllib.parse import urlsplit

from . import reminders


REQUEST_LABELS = {'resource': '资源申请', 'change': '变更申请',
                  'delivery': '私有化交付', 'license': 'License 授权'}
APPROVAL_MESSAGE = re.compile(
    r'【[^\n]+】(?P<headline>您有一项待审批申请|您有一项待执行任务)\n'
    r'申请类型：(?P<kind>[^\n]+)\n申请编号：(?P<code>[^\n]+)\n'
    r'项目 / 标题：(?P<project>[^\n]*)\n申请人：(?P<applicant>[^\n]+)\n'
    r'待处理环节：(?P<stage>[^\n]+)\n请登录平台查看并处理。\nhttps?://[^\s]+'
)
PROGRESS_EVENTS = {
    'submitted': ('申请已提交', '已提交', 'blue', '查看申请进度'),
    'status_changed': ('审批进度已更新', '进度更新', 'blue', '查看申请进度'),
    'approval_reassigned': ('审批处理人已变更', '处理人已调整', 'orange', '查看申请进度'),
    'completed': ('流程已完成', '已完成', 'green', '查看处理结果'),
    'rejected': ('申请已驳回', '已驳回', 'red', '查看意见并修改'),
}
PROGRESS_MESSAGE = re.compile(
    r'【[^\n]+】(?P<headline>[^\n]+)\n'
    r'申请类型：(?P<kind>[^\n]+)\n申请编号：(?P<code>[^\n]+)\n'
    r'项目 / 标题：(?P<project>[^\n]*)\n申请人：(?P<applicant>[^\n]+)\n'
    r'(?P<progress>[\s\S]+)\nhttps?://[^\s]+'
)


def text(content, *, size='normal', color='default'):
    # 业务字段使用 plain_text，避免项目名等内容被解析为链接或 @ 提及。
    return {'tag': 'div', 'text': {'tag': 'plain_text', 'content': str(content),
            'text_size': size, 'text_color': color}}


def field(label, value, *, color='default'):
    return {'tag': 'column', 'width': 'weighted', 'weight': 1,
            'vertical_spacing': '4px', 'elements': [
                text(label, size='notation', color='grey'), text(value or '—', color=color)]}


def columns(*items):
    return {'tag': 'column_set', 'flex_mode': 'stretch',
            'horizontal_spacing': '16px', 'columns': list(items)}


def detail_url(log, settings):
    base = settings['public_base_url'].rstrip('/')
    try:
        parsed = urlsplit(base)
    except ValueError:
        return None
    if (parsed.scheme not in {'http', 'https'} or not parsed.netloc or
            parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        return None
    section = 'licenses' if log.object_type == 'license' else log.object_type
    url = f'{base}/{section}/{log.object_id}'
    workflow_event = log.event == 'approval_required' or log.event in PROGRESS_EVENTS
    return url + '?feishu_task=1' if settings.get('feishu_task_login_enabled') and workflow_event else url


def card(title, color, project, code, elements, button, url, site_name, category):
    return {
        'schema': '2.0',
        'config': {'update_multi': True, 'summary': {'content': f'{title} · {project} · {code}'}},
        'header': {'template': color, 'title': {'tag': 'plain_text', 'content': title},
                   'padding': '12px 20px 12px 20px'},
        'body': {'direction': 'vertical', 'padding': '16px 20px 16px 20px',
                 'vertical_spacing': '12px', 'elements': [
                     text(project or '未填写项目', size='heading-2'),
                     text(code, size='notation', color='grey'),
                     *elements,
                     {'tag': 'button', 'text': {'tag': 'plain_text', 'content': button},
                      'type': 'primary', 'size': 'medium',
                      'behaviors': [{'type': 'open_url', 'default_url': url}]},
                     text(f'{site_name} · {category}', size='notation', color='grey'),
                 ]},
    }


def approval_card(log, settings, url):
    match = APPROVAL_MESSAGE.fullmatch(log.message)
    if not match or match['kind'] != REQUEST_LABELS[log.object_type]:
        # 历史消息或包含多行字段的消息保持原文投递，防止解析丢失信息。
        return None
    execution = match['headline'] == '您有一项待执行任务'
    title = f"{'待执行' if execution else '待审批'} · {match['kind']}"
    note = ('这项任务正在等待您执行，请查看申请详情后处理。' if execution else
            '这项申请正在等待您审批，请查看申请详情后处理。')
    return card(title, 'blue', match['project'], match['code'], [
        columns(field('申请人', match['applicant']), field('申请类型', match['kind'])),
        columns(field('当前待办', match['stage'], color='blue')),
        {'tag': 'hr'}, text(note, size='notation', color='grey'),
    ], '查看任务并处理' if execution else '查看申请并审批', url, settings['site_name'],
        '执行通知' if execution else '审批通知')


def progress_card(log, settings, url):
    """Present the saved workflow event without reading newer business state."""
    headline, label, color, button = PROGRESS_EVENTS[log.event]
    title = f'{label} · {REQUEST_LABELS[log.object_type]}'
    match = PROGRESS_MESSAGE.fullmatch(log.message)
    if not match or match['kind'] != REQUEST_LABELS[log.object_type] or match['headline'] != headline:
        # 旧格式或多行字段也展示为卡片，完整保留原文，避免丢失原因或误取当前流程状态。
        return card(title, color, '申请进度通知', '', [text(log.message)],
                    button, url, settings['site_name'], '申请进度')
    elements = [columns(field('申请人', match['applicant']), field('申请类型', match['kind']))]
    progress = match['progress']
    if log.event in {'submitted', 'status_changed', 'approval_reassigned'}:
        current = re.fullmatch(r'当前环节：(?P<stage>[^\n]*)\n下一位处理人：(?P<people>[\s\S]*)', progress)
        if current:
            elements.extend([
                columns(field('当前环节', current['stage'], color='blue')),
                columns(field('下一位处理人', current['people'])),
            ])
        else:
            elements.append(text(progress))
    elif log.event == 'rejected' and '\n驳回原因：' in progress:
        note, reason = progress.split('\n驳回原因：', 1)
        elements.extend([columns(field('驳回原因', reason, color='red')), {'tag': 'hr'},
                         text(note, size='notation', color='grey')])
    else:
        elements.extend([{'tag': 'hr'}, text(progress, size='notation', color='grey')])
    if log.event in {'submitted', 'status_changed', 'approval_reassigned'}:
        note = ('申请已成功提交，可随时查看审批与处理进度。' if log.event == 'submitted' else
                '处理人已调整，请以当前显示的下一位处理人为准。' if log.event == 'approval_reassigned' else
                '申请正在处理中，可查看最新审批进度。')
        elements.extend([{'tag': 'hr'}, text(note, size='notation', color='grey')])
    return card(title, color, match['project'], match['code'], elements, button, url,
                settings['site_name'], '申请进度')


def license_card(item, plan, stamp, settings, url):
    expiry = reminders.utc(plan.expires_at)
    minutes = max(1, int((expiry - stamp).total_seconds() // 60))
    remaining = (f'{minutes // 1440} 天 {minutes % 1440 // 60} 小时' if minutes >= 1440 else
                 f'{minutes // 60} 小时 {minutes % 60} 分钟' if minutes >= 60 else f'{minutes} 分钟')
    late = stamp - reminders.utc(plan.scheduled_at) > timedelta(minutes=5)
    source = '本次为生效或恢复后的补提醒。' if late else f'本次提醒：{reminders.LABELS[plan.rule]}'
    countdown = columns({'tag': 'column', 'width': 'weighted', 'weight': 1,
        'padding': '12px', 'vertical_spacing': '4px', 'background_style': 'expiry_surface',
        'elements': [text('剩余约', size='notation', color='expiry_ink'),
                     text(remaining, size='heading-2', color='expiry_ink')]})
    result = card('License 即将到期', 'orange', item.project_name, item.code, [
        countdown,
        columns(field('客户', item.customer), field('产品', item.product)),
        columns(field('到期时间 · 北京时间', f'{expiry.astimezone(reminders.BEIJING):%Y-%m-%d %H:%M:%S}')),
        {'tag': 'hr'},
        text('请提前安排续期，保障授权持续有效。\n无需继续提醒时，可在申请详情中关闭。',
             size='notation', color='grey'),
        text(source, size='notation', color='grey'),
    ], '查看 License 详情', url, settings['site_name'], '到期提醒')
    result['config']['style'] = {'color': {
        'expiry_surface': {'light_mode': 'rgba(255,242,220,1)', 'dark_mode': 'rgba(70,55,34,1)'},
        'expiry_ink': {'light_mode': 'rgba(155,85,0,1)', 'dark_mode': 'rgba(244,189,107,1)'},
    }}
    return result


def message_content(log, settings, *, context=None, stamp=None):
    """Return the transport type and body, preserving text for unsupported events."""
    result = None
    if log.object_type in REQUEST_LABELS:
        url = detail_url(log, settings)
        if url and log.event == 'approval_required':
            result = approval_card(log, settings, url)
        elif url and log.event in PROGRESS_EVENTS:
            result = progress_card(log, settings, url)
        elif (url and context and log.object_type == 'license' and
              context[1].object_type == 'license' and log.event == context[1].rule and
              log.event in reminders.LABELS):
            result = license_card(context[0], context[1], reminders.utc(stamp or reminders.now()), settings, url)
    return ('interactive', result) if result else ('text', {'text': log.message})
