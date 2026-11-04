import {test} from 'node:test'
import assert from 'node:assert/strict'
import {effectScope, ref} from 'vue'
import {useWikiEditor, WIKI_TITLE_LIMIT, WIKI_CATEGORY_LIMIT} from '../src/wiki-editor.js'

const remote={id:10,space_id:2,title:'标题',category:'规范',content:'原文\n',status:'published',parent_id:null,version:3,revision:7}
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms))
function harness({drafts=new Map(),userId=42,api={},unavailable=false}={}) {
  const scope=effectScope(),form=ref({}),calls=[],events=new Map(),saved=[],denied=[]
  const browser={localStorage:{getItem:key=>drafts.get(key)||null,setItem(key,value){if(unavailable)throw new Error('quota');drafts.set(key,value)},removeItem:key=>drafts.delete(key)},addEventListener:(name,fn)=>events.set(name,fn),removeEventListener:name=>events.delete(name)}
  const editor=scope.run(()=>useWikiEditor({form,userId,browser,delay:15,onSaved:item=>saved.push(item),onDenied:error=>denied.push(error),api:{
    put:async(path,body,options)=>{calls.push({path,body,options});return api.put?api.put(path,body):{...remote,...body,version:body.version+(body.save_mode==='manual'?1:0),revision:body.revision+1}},
    post:async(path,body)=>{calls.push({path,body});return api.post?api.post(path,body):{...body,id:20,version:1,revision:1}},
  }}))
  return {form,editor,calls,drafts,events,saved,denied,dispose(){editor.dispose();scope.stop()}}
}
test('typing immediately backs up text then debounce saves once with expected version',async()=>{
  const h=harness()
  try {
    h.editor.start(remote)
    h.form.value.content='输入一';h.form.value.content='输入二\n'
    assert.equal(h.drafts.size,1)
    assert.equal(JSON.parse([...h.drafts.values()][0]).form.content,'输入二\n')
    assert.equal(h.calls.length,0)
    await wait(45)
    assert.equal(h.calls.length,1)
    assert.equal(h.calls[0].body.version,3)
    assert.equal(h.calls[0].body.revision,7)
    assert.equal(h.calls[0].body.save_mode,'auto')
    assert.equal(h.calls[0].body.status,'published')
    assert.equal(h.form.value.version,3)
    assert.equal(h.form.value.revision,8)
    assert.equal(h.editor.dirty.value,false)
    assert.equal(h.drafts.size,0)
    await wait(30);assert.equal(h.calls.length,1)
  } finally {h.dispose()}
})
test('typing during autosave is retained and serialized using the new revision without adding versions',async()=>{
  let finish
  const h=harness({api:{put:(_path,body)=>new Promise(resolve=>finish=()=>resolve({...remote,...body,revision:body.revision+1}))}})
  try {
    h.editor.start(remote);h.form.value.content='第一段'
    const first=h.editor.save();h.form.value.content='第一段，第二段'
    assert.equal(h.calls.length,1)
    const flush=h.editor.save();finish();await first;await wait(0)
    assert.equal(h.form.value.content,'第一段，第二段')
    assert.equal(h.calls.length,2);assert.equal(h.calls[1].body.version,3);assert.equal(h.calls[1].body.revision,8)
    finish();assert.equal(await flush,true)
    assert.equal(h.form.value.version,3);assert.equal(h.form.value.revision,9);assert.equal(h.editor.dirty.value,false)
  } finally {h.dispose()}
})
test('manual save creates one version after any number of clean autosaves',async()=>{
  const h=harness()
  try {
    h.editor.start(remote)
    for(const text of ['第一段','第二段','第三段']){
      h.form.value.content=text
      assert.equal(await h.editor.save(),true)
    }
    assert.equal(h.form.value.version,3);assert.equal(h.form.value.revision,10)
    assert.equal(h.editor.dirty.value,false)
    assert.equal(await h.editor.save({manual:true}),true)
    assert.equal(h.calls.length,4)
    assert.equal(h.calls.at(-1).body.save_mode,'manual')
    assert.equal(h.calls.at(-1).body.revision,10)
    assert.equal(h.form.value.version,4);assert.equal(h.form.value.revision,11)
    await wait(40);assert.equal(h.calls.length,4)
    // A later explicit click is a new version, even with no further edits.
    assert.equal(await h.editor.save({manual:true}),true)
    assert.equal(h.form.value.version,5)
  } finally {h.dispose()}
})
test('manual click waits for an in-flight autosave then adds exactly one snapshot',async()=>{
  let finish
  const h=harness({api:{put:(_path,body)=>new Promise(resolve=>finish=()=>resolve({...remote,...body,
    version:body.version+(body.save_mode==='manual'?1:0),revision:body.revision+1}))}})
  try {
    h.editor.start(remote);h.form.value.content='最新内容'
    const auto=h.editor.save(),manual=h.editor.save({manual:true})
    assert.equal(h.calls.length,1);finish();await auto;await wait(0)
    assert.equal(h.calls.length,2)
    assert.equal(h.calls[1].body.save_mode,'manual')
    assert.equal(h.calls[1].body.revision,8)
    assert.equal(h.calls[1].body.content,'最新内容')
    finish();assert.equal(await manual,true)
    assert.equal(h.form.value.version,4)
    await wait(40);assert.equal(h.calls.length,2)
  } finally {h.dispose()}
})
test('draft restoration detects autosaves from another window with the same visible version',async()=>{
  const drafts=new Map(),h=harness({drafts,api:{put:()=>{throw new Error('offline')}}})
  h.editor.start(remote);h.form.value.content='本地未同步内容'
  await h.editor.save();h.dispose()
  const next=harness({drafts})
  try {
    next.editor.start({...remote,revision:8,content:'另一窗口自动保存'})
    assert.equal(next.form.value.content,'本地未同步内容')
    assert.equal(next.editor.conflict.value,true)
    assert.equal(await next.editor.save({manual:true}),false)
    assert.equal(next.calls.length,0)
  } finally {next.dispose()}
})
test('failed saves keep drafts, protect refresh, and recover without crossing accounts',async()=>{
  const drafts=new Map()
  const h=harness({drafts,api:{put:()=>{throw new Error('网络断开')}}})
  h.editor.start(remote);h.form.value.content='尚未同步的工作'
  assert.equal(await h.editor.save(),false)
  assert.equal(h.editor.backedUp.value,true);assert.match(h.editor.status.value,/同步失败/)
  let prevented=false
  h.events.get('beforeunload')({preventDefault(){prevented=true}})
  assert.equal(prevented,true);h.dispose()
  const other=harness({drafts,userId:99})
  other.editor.start(remote);assert.equal(other.form.value.content,remote.content);other.dispose()
  const recovered=harness({drafts})
  try {
    recovered.editor.start(remote)
    assert.equal(recovered.form.value.content,'尚未同步的工作')
    assert.equal(recovered.editor.restored.value,true)
    assert.equal(await recovered.editor.save(),true)
    assert.equal(drafts.size,0)
  } finally {recovered.dispose()}
})
test('new and incomplete documents stay local and can be restored after navigating away',async()=>{
  const drafts=new Map(),h=harness({drafts})
  const blank={title:'',category:'',content:'',space_id:2,status:'draft',parent_id:null}
  h.editor.start(blank);h.form.value.content='半篇新文档'
  assert.equal(h.drafts.size,1);assert.equal(await h.editor.save(),false)
  await wait(35);assert.equal(h.calls.length,0);h.dispose()
  const next=harness({drafts})
  try {
    next.editor.start(blank);assert.equal(next.form.value.content,'半篇新文档')
    next.form.value.title='新标题';next.form.value.category='经验'
    await wait(35);assert.equal(next.calls.length,0)
    assert.equal(await next.editor.save({create:true}),true)
    assert.equal(next.calls[0].path,'/wiki');assert.equal(drafts.size,0)
  } finally {next.dispose()}
})
test('conflicts never overwrite server content and restored old versions remain paused',async()=>{
  const drafts=new Map(),h=harness({drafts,api:{put:()=>{throw Object.assign(new Error('文档已更新'),{status:409})}}})
  h.editor.start(remote);h.form.value.content='我的草稿'
  await h.editor.save();assert.equal(h.editor.conflict.value,true)
  await wait(40);assert.equal(h.calls.length,1);h.dispose()
  const restored=harness({drafts})
  try {
    restored.editor.start({...remote,version:4,content:'另一位编辑的版本'})
    assert.equal(restored.form.value.content,'我的草稿')
    assert.equal(restored.editor.conflict.value,true)
    assert.equal(await restored.editor.save(),false);assert.equal(restored.calls.length,0)
  } finally {restored.dispose()}
})
test('revoked permissions stop autosave and erase the corresponding local draft',async()=>{
  const h=harness({api:{put:()=>{throw Object.assign(new Error('无权访问'),{status:403})}}})
  try {
    h.editor.start(remote);h.form.value.content='不能写入'
    await h.editor.save()
    assert.equal(h.denied.length,1);assert.equal(h.editor.active.value,false);assert.equal(h.drafts.size,0)
    await wait(40);assert.equal(h.calls.length,1)
  } finally {h.dispose()}
})
test('unmount keeps the newest backup and a late response cannot erase it',async()=>{
  let finish
  const drafts=new Map(),h=harness({drafts,api:{put:(_path,body)=>new Promise(resolve=>finish=()=>resolve({...remote,...body,version:4}))}})
  h.editor.start(remote);h.form.value.content='已发送段落'
  const saving=h.editor.save();h.form.value.content='还没发送的段落';h.dispose()
  finish();await saving
  assert.equal(JSON.parse([...drafts.values()][0]).form.content,'还没发送的段落')
  assert.equal(h.saved.length,0)
})
test('storage failures are visible and a successful cloud save still works',async()=>{
  const h=harness({unavailable:true})
  try {
    h.editor.start(remote);h.form.value.content='不能在本地保存'
    assert.equal(h.editor.backedUp.value,false);assert.match(h.editor.localError.value,/草稿保存失败/)
    assert.equal(await h.editor.save(),true);assert.equal(h.editor.dirty.value,false)
    assert.equal(h.editor.localError.value,'')
  } finally {h.dispose()}
})
test('oversized titles and categories stay in drafts without save attempts until corrected',async()=>{
  const h=harness()
  try {
    h.editor.start(remote)
    h.form.value.title='a'.repeat(WIKI_TITLE_LIMIT+1)
    assert.match(h.editor.fieldError.value,/标题最多 180/)
    assert.equal(await h.editor.save(),false)
    await wait(40);assert.equal(h.calls.length,0)
    assert.equal(JSON.parse([...h.drafts.values()][0]).form.title.length,WIKI_TITLE_LIMIT+1)
    h.form.value.title='项'.repeat(WIKI_TITLE_LIMIT)
    h.form.value.category='c'.repeat(WIKI_CATEGORY_LIMIT+1)
    assert.match(h.editor.fieldError.value,/分类最多 60/)
    await wait(40);assert.equal(h.calls.length,0)
    h.form.value.category='c'.repeat(WIKI_CATEGORY_LIMIT)
    assert.equal(h.editor.fieldError.value,'')
    assert.equal(await h.editor.save(),true)
    assert.equal(h.calls.length,1)
    assert.equal(h.calls[0].body.title.length,WIKI_TITLE_LIMIT)
    assert.equal(h.calls[0].body.category.length,WIKI_CATEGORY_LIMIT)
    assert.equal(h.drafts.size,0)
  } finally {h.dispose()}
})
