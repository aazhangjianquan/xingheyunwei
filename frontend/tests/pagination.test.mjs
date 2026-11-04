import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as vue from 'vue'
import * as icons from 'lucide-vue-next'
import { readBootstrapSettings } from '../src/bootstrap.js'

// Run the actual component's state and request handlers with a stub transport.
// DOM layout is checked separately; these exercise filtering and request races.
const script = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
  .match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
function setup() {
  const calls = [], queue = []
  const result = (params) => ({ items: [{id:1,status:'pending'}], page: Number(params.get('page')), page_size:Number(params.get('page_size')), total:55, total_pages:Math.ceil(55/Number(params.get('page_size'))), counts:{all:55,pending:14,in_progress:14,completed:14,rejected:13} })
  const dependencies = { ...icons, ...vue, readBootstrapSettings:()=>null, onMounted:()=>{},
    get: async path => { if (path === '/resource-catalog') return []; calls.push(path); const params = new URLSearchParams(path.split('?')[1]); return queue.length ? await queue.shift()(params) : result(params) },
    post:async()=>{}, put:async()=>{}, patch:async()=>{},
    window:{setTimeout,history:{pushState(){}},location:{pathname:'/resources'}},
    document:{title:""},
    localStorage:{getItem(){return null},removeItem(){}},
  }
  const factory = new Function('d', `const {${Object.keys(dependencies).filter(key=>key!=='default' && /^[A-Za-z_$][\w$]*$/.test(key)).join(',')}} = d;\n${script}\nreturn {user,page,data,filterStatus,search,listPage,listPageSize,listTotal,listTotalPages,statusCounts,paginationNumbers,loadPage,navigate,logout};`)
  const scope = vue.effectScope()
  const state = scope.run(()=>factory(dependencies))
  state.user.value={id:1,roles:['admin']}; state.page.value='resources'
  return {state,calls,queue,result,cleanup:()=>{state.logout();scope.stop()}}
}
const tick=ms=>new Promise(resolve=>setTimeout(resolve,ms))

test('initial page, status badges and server pagination metadata', async()=>{
  const h=setup();try {
    await h.state.loadPage()
    const q=new URLSearchParams(h.calls[0].split('?')[1])
    assert.equal(q.get('page'),'1');assert.equal(q.get('page_size'),'10')
    assert.equal(h.state.statusCounts.value.all,55);assert.equal(h.state.statusCounts.value.pending,14)
    assert.equal(h.state.data.value.length,1);assert.equal(h.state.listTotalPages.value,6)
  } finally {h.cleanup()}
})
test('next page uses SQL query; size and status changes reset first page',async()=>{
  const h=setup();try {
    await h.state.loadPage();h.state.listPage.value=3;await tick(15)
    assert.match(h.calls.at(-1),/page=3/)
    h.state.listPageSize.value=20;await tick(15)
    let q=new URLSearchParams(h.calls.at(-1).split('?')[1]);assert.equal(q.get('page'),'1');assert.equal(q.get('page_size'),'20')
    h.state.listPage.value=2;await tick(15);h.state.filterStatus.value='completed';await tick(15)
    q=new URLSearchParams(h.calls.at(-1).split('?')[1]);assert.equal(q.get('page'),'1');assert.equal(q.get('status'),'completed')
    assert.equal(h.state.statusCounts.value.all,55)
  } finally {h.cleanup()}
})
test('search is debounced, encoded and reset to the first page',async()=>{
  const h=setup();try {
    await h.state.loadPage();h.state.listPage.value=3;await tick(15)
    const before=h.calls.length
    h.state.search.value='生产 & Redis';await tick(20)
    assert.equal(h.calls.length,before)
    await tick(310)
    const q=new URLSearchParams(h.calls.at(-1).split('?')[1])
    assert.equal(q.get('q'),'生产 & Redis');assert.equal(q.get('page'),'1')
  } finally {h.cleanup()}
})
test('late response cannot overwrite a newer filter request',async()=>{
  const h=setup();try {
    let finish
    h.queue.push(()=>new Promise(resolve=>{finish=resolve}))
    const first=h.state.loadPage()
    h.state.filterStatus.value='completed';await tick(15)
    finish({items:[{id:999}],page:1,total:999,total_pages:100,counts:{all:999}})
    await first
    assert.equal(h.state.statusCounts.value.all,55);assert.notEqual(h.state.data.value[0].id,999)
  } finally {h.cleanup()}
})
test('server clamps a shrunken page without issuing another request',async()=>{
  const h=setup();try {
    h.state.listPage.value=9
    h.queue.push(params=>({...h.result(params),page:2,total:14,total_pages:2}))
    await h.state.loadPage();await tick(15)
    assert.equal(h.state.listPage.value,2);assert.equal(h.calls.length,1)
    assert.deepEqual(h.state.paginationNumbers.value,[1,2])
  } finally {h.cleanup()}
})
test('changing business list clears status/search and fetches only first page',async()=>{
  const h=setup();try {
    h.state.filterStatus.value='pending';h.state.search.value='旧关键词';h.state.listPage.value=3
    h.state.navigate('changes');await tick(30)
    assert.equal(h.calls.length,1)
    const [path,query]=h.calls[0].split('?'),q=new URLSearchParams(query)
    assert.equal(path,'/changes');assert.equal(q.get('page'),'1');assert.equal(q.get('status'),'all');assert.equal(q.get('q'),'')
  } finally {h.cleanup()}
})
