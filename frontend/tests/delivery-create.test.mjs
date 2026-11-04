import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as vue from 'vue'
import * as icons from 'lucide-vue-next'

const script = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
  .match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
function harness() {
  const calls = [], timers = new Set(), pending = []
  const location = { pathname: '/deliveries' }
  const member = { id: 42, display_name: '申请人', roles: ['applicant'] }
  let post = async () => ({ id: 101, owner_id: 42 })
  const deps = { ...vue, ...icons, readBootstrapSettings: () => null, onMounted: () => {},
    get: async path => { calls.push(['GET', path]); return path === '/projects' ? [] : path.startsWith('/deliveries?')
      ? { items: [], total: 0, total_pages: 1, page: 1, counts: {} } : { id: 101, owner_id: 42 } },
    post: async (path, body) => { calls.push(['POST', path, body]); return post(path, body) },
    put: async () => {}, patch: async () => {},
    document: { title: '' }, localStorage: { removeItem() {}, getItem() { return null } },
    window: { location, history: { pushState: (a, b, path) => { location.pathname = path } },
      setTimeout: (fn, ms) => { const id = setTimeout(fn, ms); timers.add(id); return id }, addEventListener() {} },
  }
  const keys = Object.keys(deps).filter(key => key !== 'default' && /^[A-Za-z_$][\w$]*$/.test(key))
  const factory = new Function('d', `const {${keys.join(',')}}=d;\n${script}\nreturn {user,page,modal,form,saving,detail,deliveryAttachmentQueue,deliveryUploadId,deliveryAttachmentErrors,newForm,submitForm,closeModal,navigate,expireSession,resubmitId,toast};`)
  const scope = vue.effectScope(), s = scope.run(() => factory(deps))
  s.user.value = member; s.page.value = 'deliveries'; s.newForm('delivery')
  return { s, calls, setPost: value => { post = value }, dispose() { scope.stop(); for (const id of timers) clearTimeout(id) } }
}
const selected = () => ({ id: '1', name: '交付说明.txt', file: { name: '交付说明.txt', size: 16 }, status: 'waiting' })

test('delivery creation keeps selected files with the new ID and opens detail without putting files in JSON', async () => {
  const h = harness()
  try {
    h.s.deliveryAttachmentQueue.value.push(selected())
    h.s.form.value.name = '私有化项目'
    await h.s.submitForm()
    assert.equal(h.s.deliveryUploadId.value, 101)
    assert.equal(h.s.modal.value, 'detail')
    assert.equal(h.s.detail.value.id, 101)
    assert.equal(h.s.deliveryAttachmentQueue.value[0].file.name, '交付说明.txt')
    const posts = h.calls.filter(call => call[0] === 'POST')
    assert.equal(posts.length, 1)
    assert.equal(posts[0][1], '/deliveries')
    assert.equal(posts[0][2].name, '私有化项目')
    assert.equal('file' in posts[0][2], false)
    assert.equal('account_validation' in posts[0][2], false)
  } finally { h.dispose() }
})

test('validation or API failure keeps the create form and files available for correction', async () => {
  const h = harness()
  try {
    const entry = { ...selected(), status: 'invalid', message: '文件过大' }
    h.s.deliveryAttachmentQueue.value.push(entry)
    await h.s.submitForm()
    assert.equal(h.calls.some(call => call[0] === 'POST'), false)
    assert.equal(h.s.modal.value, 'delivery')
    h.s.deliveryAttachmentQueue.value[0].status = 'waiting'
    h.setPost(async () => { throw new Error('请先配置审批流程') })
    await h.s.submitForm()
    assert.equal(h.s.modal.value, 'delivery')
    assert.equal(h.s.deliveryUploadId.value, null)
    assert.equal(h.s.deliveryAttachmentQueue.value.length, 1)
    assert.equal(h.s.saving.value, false)
    assert.match(h.s.toast.value, /审批流程/)
  } finally { h.dispose() }
})

test('double submission and modal close while creating cannot duplicate the delivery or lose files', async () => {
  const h = harness(); let finish
  try {
    h.s.deliveryAttachmentQueue.value.push(selected())
    h.setPost(() => new Promise(resolve => { finish = resolve }))
    const first = h.s.submitForm()
    await h.s.submitForm()
    h.s.closeModal(); h.s.navigate('projects'); h.s.newForm('delivery')
    assert.equal(h.s.modal.value, 'delivery')
    assert.equal(h.s.deliveryAttachmentQueue.value.length, 1)
    assert.equal(h.calls.filter(call => call[0] === 'POST').length, 1)
    finish({ id: 101 }); await first
    assert.equal(h.s.modal.value, 'detail')
    assert.equal(h.s.deliveryUploadId.value, 101)
  } finally { h.dispose() }
})

test('resubmission binds new attachments to the existing ID and a new form clears the old selection', async () => {
  const h = harness()
  try {
    h.s.resubmitId.value = 101
    h.s.deliveryAttachmentQueue.value.push(selected())
    await h.s.submitForm()
    assert.equal(h.calls.find(call => call[0] === 'POST')[1], '/delivery/101/resubmit')
    assert.equal(h.s.deliveryUploadId.value, 101)
    h.s.newForm('delivery')
    assert.equal(h.s.deliveryUploadId.value, null)
    assert.equal(h.s.deliveryAttachmentQueue.value.length, 0)
  } finally { h.dispose() }
})

test('logout during creation discards file references and late results cannot reopen someone else’s delivery', async () => {
  const h = harness(); let finish
  try {
    h.s.deliveryAttachmentQueue.value.push(selected())
    h.setPost(() => new Promise(resolve => { finish = resolve }))
    const pending = h.s.submitForm()
    h.s.expireSession()
    finish({ id: 101 }); await pending
    assert.equal(h.s.modal.value, '')
    assert.equal(h.s.deliveryAttachmentQueue.value.length, 0)
    assert.equal(h.s.deliveryUploadId.value, null)
  } finally { h.dispose() }
})

test('cancelling a new delivery discards selected files without issuing any upload or creation request', () => {
  const h = harness()
  try {
    h.s.deliveryAttachmentQueue.value.push(selected())
    h.s.closeModal()
    assert.equal(h.s.modal.value, '')
    assert.equal(h.s.deliveryAttachmentQueue.value.length, 0)
    assert.equal(h.calls.some(call => call[0] === 'POST'), false)
  } finally { h.dispose() }
})
