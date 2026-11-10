import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as vue from 'vue'

const script = readFileSync(new URL('../src/ExpiryReminder.vue', import.meta.url), 'utf8')
  .match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')

function harness({ kind = 'license', editable = true, write, read } = {}) {
  const props = vue.reactive({ kind, itemId: 12, value: {
    enabled: true, revision: 3, can_edit: editable, recipients: [{ id: 8, name: '申请人' }],
  } })
  const calls = [], events = []
  const dependencies = {
    ...vue, defineProps: () => props, defineEmits: () => (name, value) => { events.push({ name, value }); props.value = value },
    patch: async (path, body) => { calls.push({ method: 'PATCH', path, body }); return write ? write(path, body) : { ...props.value, enabled: body.enabled, revision: 4 } },
    get: async path => { calls.push({ method: 'GET', path }); return read ? read(path) : props.value },
  }
  const keys = Object.keys(dependencies).filter(key => key !== 'default' && /^[A-Za-z_$][\w$]*$/.test(key))
  const factory = new Function('d', `const {${keys.join(',')}}=d;\n${script}\nreturn {toggle,saving,error,notice,editContacts,saveContacts,contactIds,contactReason,editingContacts,nextReminderText};`)
  const scope = vue.effectScope(), state = scope.run(() => factory(dependencies))
  return { props, calls, events, state, close: () => scope.stop() }
}

test('shared reminder switch submits revision and displays the returned state', async () => {
  const h = harness()
  try {
    await h.state.toggle()
    assert.deepEqual(h.calls, [{ method: 'PATCH', path: '/licenses/12/reminder', body: { enabled: false, expected_revision: 3 } }])
    assert.equal(h.props.value.enabled, false)
    assert.equal(h.props.value.revision, 4)
    assert.match(h.state.notice.value, /所有接收人/)
  } finally { h.close() }
})

test('read-only users cannot submit reminder changes', async () => {
  const h = harness({ editable: false })
  try { await h.state.toggle(); assert.deepEqual(h.calls, []) } finally { h.close() }
})

test('failed updates preserve saved state and prevent concurrent submissions', async () => {
  let reject
  const h = harness({ kind: 'resource', write: () => new Promise((_, fail) => { reject = fail }) })
  try {
    const pending = h.state.toggle()
    await h.state.toggle()
    assert.equal(h.calls.length, 1)
    assert.equal(h.calls[0].path, '/resource/12/reminder')
    reject(new Error('网络错误'))
    await pending
    assert.equal(h.props.value.enabled, true)
    assert.equal(h.state.error.value, '网络错误')
    assert.equal(h.state.saving.value, false)
  } finally { h.close() }
})

test('revision conflict reloads the shared preference and shows the conflict', async () => {
  const h = harness({ write: async () => { throw Object.assign(new Error('设置已变更'), { status: 409 }) },
    read: async () => ({ enabled: false, revision: 9, can_edit: true }) })
  try {
    await h.state.toggle()
    assert.equal(h.calls[1].method, 'GET')
    assert.equal(h.props.value.revision, 9)
    assert.equal(h.state.error.value, '设置已变更')
  } finally { h.close() }
})

test('legacy operator assignment requires a reason and uses the resource endpoint', async () => {
  const h = harness({ kind: 'resource', write: async () => ({ enabled: true, revision: 3, recipients: [] }),
    read: async () => [{ id: 9, name: '运维人员' }] })
  try {
    await h.state.editContacts()
    h.state.contactIds.value = [9]
    await h.state.saveContacts()
    assert.equal(h.calls.length, 1)
    h.state.contactReason.value = ' 核对执行记录 '
    await h.state.saveContacts()
    assert.deepEqual(h.calls[1], { method: 'PATCH', path: '/resource/12/reminder/contacts', body: { user_ids: [9], reason: '核对执行记录' } })
    assert.equal(h.state.editingContacts.value, false)
  } finally { h.close() }
})

test('reading next milestone uses its scheduled time and never writes modification metadata', () => {
  const h = harness()
  try {
    h.props.value.next_at = '2026-11-11T02:00:00+00:00'
    h.props.value.updated_at = '2026-11-05T02:40:32+00:00'
    h.props.value.current_delivery = { recipients: [{ id: 9, status: 'unconfigured' }] }
    for (let i = 0; i < 3; i++) {
      assert.match(h.state.nextReminderText.value, /2026\/11\/11 10:00:00/)
      assert.equal(h.props.value.updated_at, '2026-11-05T02:40:32+00:00')
    }
    assert.deepEqual(h.calls, [])
    assert.deepEqual(h.events, [])
  } finally { h.close() }
})

test('no future milestone does not claim failed or unconfigured deliveries are complete', () => {
  const h = harness()
  try {
    h.props.value.effective = true
    h.props.value.next_at = null
    h.props.value.current_delivery = { recipients: [{ id: 9, status: 'failed' }] }
    assert.equal(h.state.nextReminderText.value, '无后续提醒节点')
    h.props.value.enabled = false
    assert.equal(h.state.nextReminderText.value, '已关闭')
    assert.deepEqual(h.calls, [])
  } finally { h.close() }
})
