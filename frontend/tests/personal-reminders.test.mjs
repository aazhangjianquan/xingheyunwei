import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as vue from 'vue'
const script = readFileSync(new URL('../src/PersonalReminders.vue', import.meta.url), 'utf8').match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
function harness({ sharedId = null, read, write } = {}) {
  const calls = [], events = [], mounted = [], cleanup = []
  const people = [{id:7,name:'本人',department:'研发',is_self:true},{id:8,name:'同事',department:'业务',is_self:false}]
  const deps = {...vue, defineProps:()=>({user:{id:7},sharedId}), defineEmits:()=>(...args)=>events.push(args),
    onMounted:fn=>mounted.push(fn), onBeforeUnmount:fn=>cleanup.push(fn), document:{activeElement:null},
    get: async path=>{calls.push({method:'GET',path});return read ? read(path) : path.endsWith('/people') ? people : {items:[],total:0,total_pages:1,page:1}},
    post: async(path,body)=>{calls.push({method:'POST',path,body});return write ? write(path,body) : {id:12,state:'draft',recipients:[],...body}},
    put: async(path,body)=>{calls.push({method:'PUT',path,body});return write ? write(path,body) : {id:12,state:'draft',recipients:[],...body}},
    del:async path=>{calls.push({method:'DELETE',path});return {}},
  }
  const keys=Object.keys(deps).filter(k=>k!=='default'&&/^[A-Za-z_$][\w$]*$/.test(k))
  const factory=new Function('d', `const {${keys.join(',')}}=d;\n${script}\nreturn {items,people,content,remindAt,selectedIds,detail,formOpen,formError,uploadBusy,attachmentBlocked,saving,load,openNew,openItem,close,save,payload,filteredPeople,inputTime,cancel,remove};`)
  const scope=vue.effectScope(),s=scope.run(()=>factory(deps))
  return {s,calls,events,mount:async()=>{for(const fn of mounted)await fn()},dispose(){cleanup.forEach(fn=>fn());scope.stop()}}
}
function fill(s) { s.content.value='检查合同';s.remindAt.value='2099-01-02T09:30';s.selectedIds.value=[7,8] }

test('eligible self defaults to selected and Beijing time is explicit regardless of browser timezone', async()=>{
  const h=harness(); try {
    await h.mount();h.s.openNew();assert.deepEqual(h.s.selectedIds.value,[7]);fill(h.s)
    await h.s.save()
    assert.deepEqual(h.calls.find(c=>c.method==='POST').body,{content:'检查合同',remind_at:'2099-01-02T09:30:00+08:00',recipient_ids:[7,8]})
    assert.equal(h.s.inputTime('2099-01-02T01:30:00+00:00'),'2099-01-02T09:30')
    assert.equal(h.s.detail.value.id,12)
    assert.equal(h.calls.some(c=>c.path.endsWith('/activate')),false)
  }finally{h.dispose()}
})

test('a self without Feishu ID is never auto selected, and the shared files page loads no private lists',async()=>{
  const h=harness({read:async path=>path.endsWith('/people')?[{id:8,name:'同事',department:'业务'}]:{items:[],total:0,total_pages:1,page:1}})
  try {await h.mount();h.s.openNew();assert.deepEqual(h.s.selectedIds.value,[])}finally{h.dispose()}
  const shared=harness({sharedId:12});try{await shared.mount();assert.deepEqual(shared.calls,[])}finally{shared.dispose()}
})

test('failed activation retains the saved draft and successful activation updates the same reminder',async()=>{
  let fail=true
  const h=harness({write:async(path,body)=>{if(path.endsWith('/activate')){if(fail)throw new Error('请刷新人员列表');return {id:12,state:'scheduled',recipients:[]}}
    return {id:12,state:'draft',recipients:[],...body}}})
  try {
    await h.mount();h.s.openNew();fill(h.s);await h.s.save();await h.s.save(true)
    assert.equal(h.s.detail.value.state,'draft');assert.equal(h.s.formError.value,'请刷新人员列表')
    assert.equal(h.s.content.value,'检查合同');fail=false;await h.s.save(true)
    assert.equal(h.s.detail.value.state,'scheduled')
    assert.equal(h.calls.filter(c=>c.method==='POST'&&c.path==='/personal-reminders').length,1)
  }finally{h.dispose()}
})

test('upload failures block activation until resolved and pending save cannot duplicate or close',async()=>{
  let finish
  const h=harness({write:()=>new Promise(resolve=>finish=resolve)})
  try{
    await h.mount();h.s.openNew();fill(h.s);h.s.attachmentBlocked.value=true;await h.s.save(true)
    assert.equal(h.calls.some(c=>c.method==='POST'),false)
    assert.match(h.s.formError.value,/附件上传/)
    h.s.attachmentBlocked.value=false;const pending=h.s.save();await h.s.save();h.s.close()
    assert.equal(h.calls.filter(c=>c.method==='POST').length,1);assert.equal(h.s.formOpen.value,true)
    finish({id:12,state:'draft',recipients:[]});await pending
  }finally{h.dispose()}
})

test('past times and failed writes preserve entered content and unmounted sessions ignore late results',async()=>{
  const failed=harness({write:async()=>{throw new Error('保存失败')}})
  try{await failed.mount();failed.s.openNew();fill(failed.s);failed.s.remindAt.value='2020-01-02T09:30';await failed.s.save();assert.match(failed.s.formError.value,/未来/)
    fill(failed.s);await failed.s.save();assert.equal(failed.s.content.value,'检查合同');assert.equal(failed.s.formError.value,'保存失败')
  }finally{failed.dispose()}
  let finish
  const h=harness({write:()=>new Promise(resolve=>finish=resolve)});await h.mount();h.s.openNew();fill(h.s)
  const pending=h.s.save();h.dispose();finish({id:12,state:'draft',recipients:[]});await pending
  assert.equal(h.s.detail.value,null);assert.deepEqual(h.events,[])
})
