import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as vue from 'vue'
import { createClientId } from '../src/client-id.js'

const script=readFileSync(new URL('../src/DeliveryAttachments.vue',import.meta.url),'utf8').match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm,'')
function harness(props={deliveryId:9}) {
  const calls=[],cleanup=[],mounted=[],created=[],revoked=[],anchors=[],timers=[]
  let sequence=0,read=async()=>({items:[],can_upload:true,max_size:20*1024*1024,extensions:['.txt','.docx','.xlsx','.pdf']}),write=async(path,file)=>({id:++sequence,filename:file.name}),blob=async()=>new Blob(['preview'],{type:'application/pdf'})
  const deps={...vue,defineProps:()=>props,defineEmits:()=>()=>{},onMounted:fn=>mounted.push(fn),onBeforeUnmount:fn=>cleanup.push(fn),
    createClientId,
    get:path=>{calls.push({method:'GET',path});return read(path)},
    getBlob:path=>{calls.push({method:'BLOB',path});return blob(path)},
    uploadFile:(path,file)=>{calls.push({method:'UPLOAD',path,file});return write(path,file)},
    URL:{createObjectURL:value=>{const url='blob:attachment-'+created.length;created.push({url,value});return url},revokeObjectURL:url=>revoked.push(url)},
    document:{activeElement:null,body:{appendChild:()=>{}},createElement:()=>{const a={click(){a.clicked=true},remove(){a.removed=true}};anchors.push(a);return a}},
    window:{setTimeout:fn=>{timers.push(fn);return timers.length}},
  }
  const keys=Object.keys(deps).filter(k=>k!=='default'&&/^[A-Za-z_$][\w$]*$/.test(k))
  const factory=new Function('d',`const {${keys.join(',')}}=d;\n${script}\nreturn {items,canUpload,loading,error,queue,uploading,preview,previewUrl,previewText,previewTruncated,previewError,load,addFiles,removeFile,processQueue,clearQueue,openPreview,closePreview,download};`)
  const scope=vue.effectScope(),s=scope.run(()=>factory(deps))
  let disposed=false
  return {s,calls,created,revoked,anchors,timers,mount:async()=>{for(const fn of mounted)await fn()},setRead:fn=>read=fn,setWrite:fn=>write=fn,setBlob:fn=>blob=fn,dispose(){if(!disposed){disposed=true;cleanup.forEach(fn=>fn());scope.stop()}}}
}
const file=(name='交付.txt',size=20)=>({name,size})

test('attachment upload permission comes from the server, including for administrators',async()=>{
  const h=harness()
  try {
    h.setRead(async()=>({items:[{id:1}],can_upload:false,max_size:20,extensions:['.txt']}))
    await h.s.load();await h.s.addFiles([file()])
    assert.equal(h.s.canUpload.value,false)
    assert.equal(h.calls.some(c=>c.method==='UPLOAD'),false)
    assert.deepEqual(h.s.items.value,[{id:1}])
  } finally {h.dispose()}
})

test('multiple documents upload sequentially and completed files appear without refetching detail',async()=>{
  const h=harness()
  try {
    await h.s.load()
    const files=[file('说明.docx'),file('清单.xlsx')]
    await h.s.addFiles(files)
    assert.deepEqual(h.calls.filter(c=>c.method==='UPLOAD').map(c=>c.file.name),['说明.docx','清单.xlsx'])
    assert.deepEqual(h.s.items.value.map(i=>i.filename),['清单.xlsx','说明.docx'])
    assert.equal(h.calls.filter(c=>c.method==='GET').length,1)
    assert.ok(h.s.queue.value.every(e=>e.status==='done'&&e.file===null))
    assert.equal(h.s.uploading.value,false)
  } finally {h.dispose()}
})

test('empty, oversized and executable files are rejected before making upload requests',async()=>{
  const h=harness()
  try {
    await h.s.load()
    await h.s.addFiles([file('a.txt',0),file('a.txt',21*1024*1024),file('a.exe')])
    assert.equal(h.calls.some(c=>c.method==='UPLOAD'),false)
    assert.ok(h.s.queue.value.every(e=>e.status==='invalid'&&e.message))
  } finally {h.dispose()}
})

test('rate limiting pauses the queue, preserves successful uploads and requires a manual retry',async()=>{
  const h=harness();let count=0
  try {
    await h.s.load()
    h.setWrite(async(path,f)=>{if(++count===2)throw Object.assign(new Error('请求过于频繁，请稍后重试'),{status:429});return {id:count,filename:f.name}})
    await h.s.addFiles([file('1.txt'),file('2.txt'),file('3.txt')])
    assert.deepEqual(h.s.queue.value.map(e=>e.status),['done','failed','waiting'])
    assert.equal(count,2)
    assert.match(h.s.queue.value[1].message,/请求过于频繁/)
    await h.s.processQueue(h.s.queue.value[1].id)
    assert.deepEqual(h.s.queue.value.map(e=>e.status),['done','done','done'])
    assert.equal(h.s.items.value.length,3)
  } finally {h.dispose()}
})

test('text previews display plain text and large files explicitly truncate the preview',async()=>{
  const h=harness()
  try {
    h.setBlob(async()=>new Blob(['<script>alert(1)</script>']))
    await h.s.openPreview({id:1,filename:'a.txt',preview_type:'text'})
    assert.equal(h.s.previewText.value,'<script>alert(1)</script>')
    assert.equal(h.s.previewUrl.value,'')
    h.setBlob(async()=>new Blob(['a'.repeat(500010)]))
    await h.s.openPreview({id:2,filename:'b.txt',preview_type:'text'})
    assert.equal(h.s.previewText.value.length,500000)
    assert.equal(h.s.previewTruncated.value,true)
    h.s.closePreview()
    assert.equal(h.s.previewText.value,'')
  } finally {h.dispose()}
})

test('late preview responses cannot replace a new document and blob URLs are released',async()=>{
  const h=harness();let finish
  try {
    h.setBlob(path=>path.includes('/1/')?new Promise(resolve=>finish=resolve):Promise.resolve(new Blob(['second'],{type:'application/pdf'})))
    const old=h.s.openPreview({id:1,filename:'old.pdf',preview_type:'pdf'})
    await new Promise(resolve=>setImmediate(resolve))
    await h.s.openPreview({id:2,filename:'new.pdf',preview_type:'pdf'})
    finish(new Blob(['old']));await old
    assert.equal(h.s.preview.value.id,2)
    assert.equal(h.created.length,1)
    const url=h.s.previewUrl.value;h.s.closePreview()
    assert.ok(h.revoked.includes(url))
    assert.equal(h.s.preview.value,null)
  } finally {h.dispose()}
})

test('preview failures preserve download access and downloads use authenticated blobs',async()=>{
  const h=harness()
  try {
    const item={id:3,filename:'交付清单.xlsx',preview_type:'pdf'}
    h.setBlob(async path=>{if(path.endsWith('preview'))throw new Error('预览转换超时');return new Blob(['original'])})
    await h.s.openPreview(item)
    assert.equal(h.s.previewError.value,'预览转换超时')
    await h.s.download(item)
    assert.equal(h.anchors[0].download,'交付清单.xlsx')
    assert.equal(h.anchors[0].clicked,true)
    assert.equal(h.anchors[0].removed,true)
    assert.ok(h.calls.some(c=>c.path==='/deliveries/9/attachments/3/download'))
    h.timers.forEach(fn=>fn())
    assert.ok(h.revoked.includes(h.created[0].url))
  } finally {h.dispose()}
})

test('disposing an attachment view drops late uploads and clears document data',async()=>{
  const h=harness();let finish
  await h.s.load()
  h.setWrite(()=>new Promise(resolve=>finish=resolve))
  const pending=h.s.addFiles([file()])
  h.dispose();finish({id:4,filename:'late.txt'});await pending
  assert.equal(h.s.items.value.length,0)
  assert.equal(h.s.queue.value.length,0)
  assert.equal(h.s.preview.value,null)
})

test('create form selects and removes attachments locally without issuing API requests',async()=>{
  const selected=vue.reactive([])
  const props={deliveryId:null,uploadQueue:selected,disabled:false}
  const h=harness(props)
  try {
    await h.mount()
    assert.equal(h.s.loading.value,false)
    assert.equal(h.s.canUpload.value,true)
    await h.s.addFiles([file('交付.docx'),file('清单.xlsx')])
    assert.equal(selected.length,2)
    assert.ok(selected.every(entry=>entry.status==='waiting'&&entry.file))
    assert.deepEqual(h.calls,[])
    h.s.removeFile(selected[0].id)
    assert.equal(selected.length,1)
    props.disabled=true
    h.s.removeFile(selected[0].id);await h.s.addFiles([file()]);h.s.clearQueue()
    assert.equal(selected.length,1)
    props.disabled=false
    h.s.clearQueue()
    assert.equal(selected.length,0)
  } finally {h.dispose()}
})

test('selected files survive the creation transition, upload once, and failed uploads retry without creating another delivery',async()=>{
  const selected=vue.reactive([])
  const draft=harness({deliveryId:null,uploadQueue:selected})
  await draft.s.addFiles([file('1.txt'),file('2.txt'),file('3.txt')])
  draft.dispose()
  assert.equal(selected.length,3)
  const detail=harness({deliveryId:9,uploadQueue:selected});let count=0
  try {
    detail.setWrite(async(path,f)=>{if(++count===2)throw Object.assign(new Error('MinIO 暂不可用'),{status:503});return {id:count,filename:f.name}})
    await detail.mount()
    assert.deepEqual(selected.map(entry=>entry.status),['done','failed','done'])
    assert.equal(selected[0].file,null)
    assert.ok(selected[1].file)
    await detail.s.processQueue(selected[1].id)
    assert.deepEqual(selected.map(entry=>entry.status),['done','done','done'])
    assert.equal(detail.calls.filter(call=>call.method==='UPLOAD').length,4)
    assert.ok(detail.calls.every(call=>call.path.startsWith('/deliveries/9/attachments')))
  } finally {detail.dispose()}
  const reopened=harness({deliveryId:9,uploadQueue:selected})
  try {await reopened.mount();assert.equal(reopened.calls.some(call=>call.method==='UPLOAD'),false)} finally {reopened.dispose()}
})

test('failed detail authorization keeps selected files and never starts an upload',async()=>{
  const selected=vue.reactive([{id:'1',file:file(),name:'交付.txt',status:'waiting'}])
  const h=harness({deliveryId:9,uploadQueue:selected})
  try {
    h.setRead(async()=>{throw Object.assign(new Error('记录不存在或无权查看'),{status:404})})
    await h.mount()
    assert.equal(h.calls.some(call=>call.method==='UPLOAD'),false)
    assert.equal(selected[0].status,'waiting')
    assert.ok(selected[0].file)
  } finally {h.dispose()}
})

test('closing details during an upload records its eventual success so reopening cannot duplicate it',async()=>{
  const selected=vue.reactive([{id:'1',file:file(),name:'交付.txt',status:'waiting'}])
  const h=harness({deliveryId:9,uploadQueue:selected});let finish
  h.setWrite(()=>new Promise(resolve=>finish=resolve))
  const pending=h.mount()
  await new Promise(resolve=>setImmediate(resolve))
  h.dispose();finish({id:4,filename:'交付.txt'});await pending
  assert.equal(selected[0].status,'done')
  assert.equal(selected[0].file,null)
  const reopened=harness({deliveryId:9,uploadQueue:selected})
  try {await reopened.mount();assert.equal(reopened.calls.some(call=>call.method==='UPLOAD'),false)} finally {reopened.dispose()}
})
