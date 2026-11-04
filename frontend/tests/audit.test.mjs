import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as vue from 'vue'

const script = readFileSync(new URL('../src/AuditLog.vue', import.meta.url), 'utf8')
  .match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
const result = (items=[], more={}) => ({items,total:items.length,total_pages:1,page:1,types:[],actions:[],...more})
function setup() {
  const calls=[], cleanup=[], timers=new Map()
  let timerId=0, read=async()=>result()
  const dependencies={...vue,onMounted(){},onBeforeUnmount(fn){cleanup.push(fn)},
    get:path=>{calls.push(path);return read(path)},clearTimeout:id=>timers.delete(id),
    window:{setTimeout(fn){const id=++timerId;timers.set(id,fn);return id},removeEventListener(){}},
  }
  const factory=new Function('d',`const {${Object.keys(dependencies).filter(k=>k!=='default'&&/^[A-Za-z_$][\w$]*$/.test(k)).join(',')}}=d;${script};return {kind,search,actor,targetType,action,result,fromDate,toDate,page,pageSize,rows,total,totalPages,loading,error,detail,detailError,detailLoading,detailOpen,hasFilters,load,openDetail,closeDetail,resetFilters,dateTime};`)
  const scope=vue.effectScope(),state=scope.run(()=>factory(dependencies))
  return {state,calls,setRead(fn){read=fn},async flush(){const fn=[...timers.values()].at(-1);timers.clear();if(fn)await fn()},
    pending:()=>timers.size,cleanup(){cleanup.forEach(fn=>fn());scope.stop()}}
}

test('audit defaults to business operations and sends combined filters to server pagination',async()=>{
  const h=setup()
  try {
    await h.state.load()
    assert.equal(new URLSearchParams(h.calls[0].split('?')[1]).get('record_kind'),'business')
    h.state.search.value=' RES-2026-0001 '
    h.state.actor.value=' 张三 '
    h.state.targetType.value='resource';h.state.action.value='update'
    h.state.fromDate.value='2026-10-26';h.state.toDate.value='2026-10-27'
    h.state.pageSize.value=50
    h.setRead(async()=>result([{id:7}],{total:120,total_pages:3,page:2}))
    h.state.page.value=2
    await h.flush()
    const params=new URLSearchParams(h.calls.at(-1).split('?')[1])
    assert.deepEqual(Object.fromEntries(params),{record_kind:'business',page:'2',page_size:'50',q:'RES-2026-0001',actor:'张三',result:'all',target_type:'resource',action:'update',from_date:'2026-10-26',to_date:'2026-10-27'})
    assert.equal(h.state.total.value,120);assert.equal(h.state.page.value,2)
    assert.equal(h.calls.length,2)
  } finally {h.cleanup()}
})

test('filter changes reset page and server clamping does not create duplicate requests',async()=>{
  const h=setup()
  try {
    h.state.page.value=5
    h.setRead(async()=>result([{id:1}],{total:1,total_pages:1,page:1}))
    await h.flush()
    assert.equal(h.state.page.value,1);assert.equal(h.pending(),0)
    h.state.page.value=3;h.state.actor.value='李四'
    assert.equal(h.state.page.value,1)
    await h.flush()
    assert.equal(h.calls.length,2)
  } finally {h.cleanup()}
})

test('switching to request history clears business filters and supports failed attempts',async()=>{
  const h=setup()
  try {
    h.state.targetType.value='license';h.state.action.value='generate';h.state.search.value='LIC-1'
    h.state.kind.value='request'
    assert.equal(h.state.hasFilters.value,false)
    h.state.result.value='failure'
    await h.flush()
    const params=new URLSearchParams(h.calls.at(-1).split('?')[1])
    assert.equal(params.get('record_kind'),'request');assert.equal(params.get('result'),'failure')
    assert.equal(params.has('target_type'),false);assert.equal(params.has('action'),false)
    h.state.resetFilters();await h.flush()
    assert.equal(h.state.kind.value,'request');assert.equal(h.state.hasFilters.value,false)
  } finally {h.cleanup()}
})

test('late queries cannot replace current filters, and errors do not show stale rows as results',async()=>{
  const h=setup()
  try {
    let finish
    h.setRead(()=>new Promise(resolve=>{finish=resolve}))
    const first=h.state.load()
    h.state.search.value='新条件'
    finish(result([{id:1}]))
    await first
    assert.deepEqual(h.state.rows.value,[])
    h.setRead(async()=>result([{id:2}]))
    await h.flush()
    assert.equal(h.state.rows.value[0].id,2)
    h.setRead(async()=>{throw new Error('日志查询失败')})
    await h.state.load()
    assert.equal(h.state.error.value,'日志查询失败')
    assert.deepEqual(h.state.rows.value,[]);assert.equal(h.state.total.value,0)
    assert.equal(h.state.loading.value,false)
  } finally {h.cleanup()}
})

test('detail responses respect selection and close boundaries and show access errors',async()=>{
  const h=setup()
  try {
    let finish
    h.setRead(path=>path.endsWith('/1')?new Promise(resolve=>{finish=resolve}):Promise.resolve({id:2,changes:[]}))
    const old=h.state.openDetail({id:1});await vue.nextTick()
    await h.state.openDetail({id:2})
    finish({id:1,changes:[]});await old
    assert.equal(h.state.detail.value.id,2)
    h.state.closeDetail()
    assert.equal(h.state.detailOpen.value,false);assert.equal(h.state.detail.value,null)
    h.setRead(async()=>{throw new Error('当前角色无权执行此操作')})
    await h.state.openDetail({id:3})
    assert.equal(h.state.detail.value,null);assert.equal(h.state.detailLoading.value,false)
    assert.equal(h.state.detailError.value,'当前角色无权执行此操作')
  } finally {h.cleanup()}
})

test('query dates use Beijing calendar days and reject reversed ranges without a request',async()=>{
  const h=setup()
  try {
    assert.match(h.state.dateTime('2030-01-01T16:00:00Z'),/2030\/01\/02 00:00:00/)
    h.state.fromDate.value='2030-01-02';h.state.toDate.value='2030-01-01'
    await h.flush()
    assert.equal(h.calls.length,0);assert.equal(h.state.error.value,'开始日期不能晚于结束日期')
    assert.equal(h.state.loading.value,false)
  } finally {h.cleanup()}
})
