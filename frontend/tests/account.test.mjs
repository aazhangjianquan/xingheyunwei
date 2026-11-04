import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as vue from 'vue'
import { validPassword } from '../src/password-policy.js'

const script = readFileSync(new URL('../src/AccountSettings.vue', import.meta.url), 'utf8')
  .match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
function harness({ write } = {}) {
  const user = { id: 7, username: 'member', display_name: '原姓名', roles: ['applicant'], department: '研发部' }
  const calls = [], events = [], cleanup = []
  const dependencies = {
    ...vue, validPassword, defineProps: () => ({ user }), defineEmits: () => (...args) => events.push(args),
    onMounted: () => {}, onBeforeUnmount: fn => cleanup.push(fn),
    patch: async (path, body) => { calls.push({ method: 'PATCH', path, body }); return write ? write(path, body) : { ...user, ...body } },
    post: async (path, body) => { calls.push({ method: 'POST', path, body }); return write ? write(path, body) : { message: '密码已修改，下次登录请使用新密码' } },
  }
  const keys = Object.keys(dependencies).filter(key => key !== 'default' && /^[A-Za-z_$][\w$]*$/.test(key))
  const factory = new Function('d', `const {${keys.join(',')}}=d;\n${script}\nreturn {tab,displayName,passwords,saving,error,success,nameValid,nameChanged,passwordValid,saveName,savePassword,selectTab,close};`)
  const scope = vue.effectScope(), state = scope.run(() => factory(dependencies))
  let disposed = false
  return { state, calls, events, dispose() { if (!disposed) { disposed = true; cleanup.forEach(fn => fn()); scope.stop() } } }
}

test('ordinary member saves a trimmed name using only the self-service name field', async () => {
  const h = harness()
  try {
    h.state.displayName.value = ' 新姓名 '
    await h.state.saveName()
    assert.deepEqual(h.calls, [{ method: 'PATCH', path: '/me', body: { display_name: '新姓名' } }])
    assert.equal(h.state.displayName.value, '新姓名')
    assert.equal(h.state.success.value, '姓名已保存')
    assert.equal(h.events[0][0], 'saved')
    assert.equal(h.events[0][1].id, 7)
    assert.deepEqual(h.events[0][1].roles, ['applicant'])
  } finally { h.dispose() }
})

test('empty, oversized and unchanged names do not create requests', async () => {
  const h = harness()
  try {
    for (const name of ['', '   ', '名'.repeat(81)]) {
      h.state.displayName.value = name
      await h.state.saveName()
      assert.match(h.state.error.value, /姓名不能为空/)
    }
    h.state.displayName.value = ' 原姓名 '
    await h.state.saveName()
    assert.equal(h.state.success.value, '姓名没有变化')
    assert.equal(h.calls.length, 0)
  } finally { h.dispose() }
})

test('password validation blocks weak, mismatched, missing-current and unchanged passwords', async () => {
  const h = harness()
  try {
    for (const [values, message] of [
      [{ current: '', next: 'ChangedPass123!', confirm: 'ChangedPass123!' }, /请输入当前密码/],
      [{ current: 'Demo@2026', next: 'Aa1!short', confirm: 'Aa1!short' }, /12–128 位/],
      [{ current: 'Demo@2026', next: 'ChangedPass123!', confirm: 'ChangedPass123' }, /不一致/],
      [{ current: 'ChangedPass123!', next: 'ChangedPass123!', confirm: 'ChangedPass123!' }, /不能与当前密码相同/],
    ]) {
      h.state.passwords.value = values
      await h.state.savePassword()
      assert.match(h.state.error.value, message)
      assert.equal(h.state.passwordValid.value, false)
    }
    assert.equal(h.calls.length, 0)
  } finally { h.dispose() }
})

test('successful password update sends current/new passwords and clears all secret inputs', async () => {
  const h = harness()
  try {
    h.state.passwords.value = { current: 'Demo@2026', next: 'ChangedPass123!', confirm: 'ChangedPass123!' }
    assert.equal(h.state.passwordValid.value, true)
    await h.state.savePassword()
    assert.deepEqual(h.calls, [{ method: 'POST', path: '/me/password', body: { current_password: 'Demo@2026', new_password: 'ChangedPass123!' } }])
    assert.deepEqual(h.state.passwords.value, { current: '', next: '', confirm: '' })
    assert.deepEqual(h.events, [['password-changed']])
    assert.match(h.state.success.value, /密码已修改/)
    assert.equal(h.state.displayName.value, '原姓名')
  } finally { h.dispose() }
})

test('incorrect current password and throttling retain the actual API message without retries', async () => {
  for (const message of ['当前密码不正确，请重新输入', '请求过于频繁，请勿连续操作，请在 5 秒后重试。']) {
    const h = harness({ write: async () => { throw new Error(message) } })
    try {
      h.state.passwords.value = { current: 'wrong', next: 'ChangedPass123!', confirm: 'ChangedPass123!' }
      await h.state.savePassword()
      assert.equal(h.state.error.value, message)
      assert.equal(h.state.saving.value, false)
      assert.equal(h.calls.length, 1)
      assert.deepEqual(h.events, [])
      h.state.passwords.value.current = 'correct'
      assert.equal(h.state.error.value, '')
    } finally { h.dispose() }
  }
})

test('pending writes prevent duplicate submission or close and late responses do not update a disposed account', async () => {
  for (const kind of ['name', 'password']) {
    let finish
    const h = harness({ write: () => new Promise(resolve => { finish = resolve }) })
    h.state.displayName.value = '新姓名'
    h.state.passwords.value = { current: 'Demo@2026', next: 'ChangedPass123!', confirm: 'ChangedPass123!' }
    const save = kind === 'name' ? h.state.saveName : h.state.savePassword
    const pending = save()
    await save(); h.state.close(); await h.state.selectTab('security')
    assert.equal(h.calls.length, 1)
    assert.equal(h.state.tab.value, 'profile')
    assert.deepEqual(h.events, [])
    h.dispose()
    finish({ id: 7, display_name: '迟到姓名', message: '迟到修改' })
    await pending
    assert.deepEqual(h.events, [])
    assert.deepEqual(h.state.passwords.value, { current: '', next: '', confirm: '' })
  }
})

test('closing or leaving the password tab clears secrets', async () => {
  const h = harness()
  try {
    h.state.passwords.value = { current: 'Demo@2026', next: 'ChangedPass123!', confirm: 'ChangedPass123!' }
    await h.state.selectTab('profile')
    assert.deepEqual(h.state.passwords.value, { current: '', next: '', confirm: '' })
    h.state.passwords.value.current = 'Demo@2026'
    h.state.close()
    assert.deepEqual(h.state.passwords.value, { current: '', next: '', confirm: '' })
    assert.deepEqual(h.events, [['close']])
  } finally { h.dispose() }
})
