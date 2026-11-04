import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as vue from 'vue'

const script = readFileSync(new URL('../src/LicenseManagement.vue', import.meta.url), 'utf8')
  .match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
function setup() {
  const calls = [], notices = [], cleanup = []
  const key = JSON.stringify({ license: { body: 'signed-body', key: 'public-key' } })
  const list = { items: [], counts: { all: 1, pending: 0, generated: 1 }, total: 1, total_pages: 1, page: 1, can_manage: true }
  let read = async path => path === '/projects' ? [{id:4,name:'项目',department:'研发部',active:true},{id:5,name:'停用项目',active:false}] : path.includes('?') ? list : { id: 1, status: 'generated', license_key: key }
  let write = async () => ({ id: 1, status: 'generated', license_key: key })
  let copied
  const dependencies = {
    ...vue, defineProps: () => ({user:{id:2,roles:['applicant']}}), defineEmits: () => (_, message) => notices.push(message),
    onMounted: () => {}, onBeforeUnmount: fn => cleanup.push(fn),
    get: path => { calls.push({ method: 'GET', path }); return read(path) },
    post: (path, body) => { calls.push({ method: 'POST', path, body }); return write(path, body) },
    window: { setTimeout, location: { pathname: '/licenses' }, history: { pushState() {} }, removeEventListener() {} },
    navigator: { clipboard: { writeText: async value => { copied = value } } },
  }
  const factory = new Function('d', `const {${Object.keys(dependencies).filter(key => key !== 'default' && /^[A-Za-z_$][\w$]*$/.test(key)).join(',')}}=d;\n${script}\nreturn {form,detail,canManage,modalError,saving,page,status,load,openDetail,submitApplication,submitApproval,myTask,copyKey,dateTime,newApplication,projects,projectsLoading,projectsError,loadProjects,selectedProject,editRejected,submitReview,legacyProjectId,closeModal};`)
  const scope = vue.effectScope(), state = scope.run(() => factory(dependencies))
  return { state, calls, notices, key, setRead: fn => { read = fn }, setWrite: fn => { write = fn }, getCopied: () => copied, cleanup() { cleanup.forEach(fn => fn()); scope.stop() } }
}

test('Beijing expiration input is submitted as exact Unix milliseconds with requested quotas', async () => {
  const h = setup()
  try {
    await h.state.newApplication()
    h.state.form.value = { project_id:4, customer: '客户', product: '产品', tenant_id: 7, expire_at: '2030-01-01T12:30', account_number: -1, agent_number: 50, purpose: '使用' }
    await h.state.submitApplication()
    const posted = h.calls.find(call => call.method === 'POST')
    assert.equal(posted.body.expire_time, Date.parse('2030-01-01T04:30:00Z'))
    assert.equal(posted.body.tenant_id, 7)
    assert.equal(posted.body.account_number, -1)
    assert.equal(posted.body.agent_number, 50)
    assert.equal('expire_at' in posted.body, false)
    assert.equal(posted.body.project_id,4)
    assert.equal('project_name' in posted.body,false)
    assert.match(h.state.dateTime(posted.body.expire_time), /12:30/)
  } finally { h.cleanup() }
})

test('generation and refresh display and copy the same complete signed license', async () => {
  const h = setup()
  try {
    h.state.detail.value = { id: 1, status: 'in_progress', license_key: null, my_tasks:[{id:42,eligible:true}] }
    await h.state.submitApproval('approve')
    assert.equal(h.state.detail.value.license_key, h.key)
    await h.state.openDetail(1, false)
    await h.state.copyKey()
    assert.equal(h.getCopied(), h.key)
    assert.deepEqual(h.calls.find(call => call.method === 'POST').body, {task_id:42,action:'approve',comment:''})
    assert.equal(h.calls.find(call => call.method === 'POST').path, '/license/1/action')
    assert.equal(h.state.saving.value, false)
  } finally { h.cleanup() }
})

test('revoked approval permissions clear a protected detail response', async () => {
  const h = setup()
  try {
    h.state.detail.value = { id: 1, status: 'in_progress', my_tasks:[{id:42,eligible:true}] }
    h.state.canManage.value = true
    h.setWrite(async () => { throw Object.assign(new Error('权限已撤销'), { status: 403 }) })
    h.setRead(async () => { throw Object.assign(new Error('无权查看'), { status: 404 }) })
    await h.state.submitApproval('approve')
    assert.equal(h.state.detail.value, null)
    assert.equal(h.state.modalError.value, '权限已撤销')
  } finally { h.cleanup() }
})

test('late detail responses cannot replace a newly selected application or key', async () => {
  const h = setup()
  try {
    let finish
    h.setRead(path => path.endsWith('/1') ? new Promise(resolve => { finish = resolve }) : Promise.resolve({ id: 2, license_key: 'second-key' }))
    const old = h.state.openDetail(1, false)
    await h.state.openDetail(2, false)
    finish({ id: 1, license_key: 'old-key' })
    await old
    assert.equal(h.state.detail.value.id, 2)
    assert.equal(h.state.detail.value.license_key, 'second-key')
  } finally { h.cleanup() }
})

test('project selection loads on demand, excludes disabled projects and blocks missing selections', async () => {
  const h=setup()
  try {
    await h.state.newApplication()
    assert.deepEqual(h.state.projects.value.map(p=>p.id),[4])
    assert.equal(h.state.form.value.project_id,null)
    assert.equal(h.calls.filter(c=>c.path==='/projects').length,1)
    await h.state.submitApplication()
    assert.match(h.state.modalError.value,/请选择有权限的启用项目/)
    assert.equal(h.calls.some(c=>c.method==='POST'),false)
  } finally {h.cleanup()}
})

test('project load failures can retry and a late project response cannot replace a newer form', async () => {
  const h=setup()
  try {
    h.setRead(async()=>{throw new Error('网络异常')})
    await h.state.newApplication()
    assert.equal(h.state.projectsError.value,'网络异常')
    let finish
    h.setRead(()=>new Promise(resolve=>{finish=resolve}))
    const old=h.state.loadProjects()
    h.state.closeModal()
    h.setRead(async()=>[{id:7,name:'新项目',active:true}])
    await h.state.newApplication()
    finish([{id:6,name:'旧请求',active:true}]);await old
    assert.deepEqual(h.state.projects.value.map(p=>p.id),[7])
    assert.equal(h.state.projectsError.value,'')
  } finally {h.cleanup()}
})

test('rejected applications retain their project id and reload current project permissions', async () => {
  const h=setup()
  try {
    h.state.detail.value={id:3,project_id:5,project_name:'旧项目',customer:'客户',product:'产品',tenant_id:1,
      expire_time:Date.parse('2030-01-01T04:30:00Z'),account_number:-1,agent_number:-1,purpose:'用途'}
    await h.state.editRejected()
    assert.equal(h.state.form.value.project_id,5)
    assert.equal(h.state.selectedProject.value,undefined)
    assert.deepEqual(h.state.projects.value.map(p=>p.id),[4])
    h.state.form.value.project_id=4
    await h.state.submitApplication()
    const posted=h.calls.find(c=>c.method==='POST')
    assert.equal(posted.path,'/licenses/3/resubmit')
    assert.equal(posted.body.project_id,4)
    assert.equal('project_name' in posted.body,false)
  } finally {h.cleanup()}
})

test('legacy review uses the applicants project options and submits the explicit association', async () => {
  const h=setup()
  try {
    h.setRead(async path=>path==='/licenses/9/projects'?[{id:8,name:'申请人的项目',active:true}]:path.includes('?')?
      {items:[],counts:{all:1},total:1,total_pages:1,page:1,can_manage:false}:
      {id:9,applicant_id:2,stage:'awaiting_workflow',project_id:null})
    await h.state.openDetail(9,false)
    assert.equal(h.state.legacyProjectId.value,null)
    await h.state.submitReview()
    assert.equal(h.calls.some(c=>c.method==='POST'),false)
    h.state.legacyProjectId.value=8
    await h.state.submitReview()
    assert.deepEqual(h.calls.find(c=>c.method==='POST'),{method:'POST',path:'/licenses/9/submit-review',body:{project_id:8}})
  } finally {h.cleanup()}
})
