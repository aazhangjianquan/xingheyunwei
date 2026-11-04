import { test } from 'node:test'
import assert from 'node:assert/strict'
import { getBlob, request, uploadFile } from '../src/api.js'
function setup(status) {
  const values=new Map([['yunwei_token','valid-token']]),events=[],eventDetails=[]
  const original={fetch:globalThis.fetch,localStorage:globalThis.localStorage,window:globalThis.window}
  globalThis.localStorage={getItem:k=>values.get(k)||null,removeItem:k=>values.delete(k)}
  globalThis.window={dispatchEvent:e=>{events.push(e.type);eventDetails.push(e.detail)}}
  globalThis.fetch=async()=>({status,ok:status<400,json:async()=>({detail:'测试响应'})})
  return {values,events,eventDetails,done:()=>Object.assign(globalThis,original)}
}
test('file uploads encode names, send the raw body and preserve authorization and rate handling',async()=>{
  const h=setup(201)
  h.values.set('yunwei_token','attachment-upload-token')
  try {
    const file={name:'交付 & 清单.xlsx',size:20};let sent
    globalThis.fetch=async(path,options)=>{sent={path,options};return {status:201,ok:true,json:async()=>({id:1})}}
    assert.deepEqual(await uploadFile('/deliveries/9/attachments',file),{id:1})
    assert.equal(new URL('http://test'+sent.path).searchParams.get('filename'),file.name)
    assert.equal(sent.options.body,file)
    assert.equal(sent.options.headers['Content-Type'],'application/octet-stream')
    assert.equal(sent.options.headers.Authorization,'Bearer attachment-upload-token')
    globalThis.fetch=async()=>({status:413,ok:false,json:async()=>({detail:'单个附件不能超过 20 MB'})})
    await assert.rejects(uploadFile('/deliveries/9/attachments',file),e=>e.status===413 && /20 MB/.test(e.message))
  } finally {h.done()}
})
test('401 on a current authenticated request clears its token exactly once',async()=>{
  const h=setup(401);try{await assert.rejects(request('/me'),e=>e.status===401);assert.equal(h.values.has('yunwei_token'),false);assert.deepEqual(h.events,['yunwei-unauthorized'])}finally{h.done()}
})
test('network, forbidden and server failures preserve the current login',async()=>{
  for(const status of [403,500,503]) {const h=setup(status);try{await assert.rejects(request('/projects'));assert.equal(h.values.get('yunwei_token'),'valid-token');assert.deepEqual(h.events,[])}finally{h.done()}}
  const h=setup(200);try{globalThis.fetch=async()=>{throw new TypeError('offline')};await assert.rejects(request('/me'));assert.equal(h.values.get('yunwei_token'),'valid-token')}finally{h.done()}
})
test('a failed public login/ticket/options request does not expire an existing session',async()=>{
  for(const path of ['/login','/auth/ticket','/auth/options','/auth/feishu/start']) {const h=setup(401);try{await assert.rejects(request(path));assert.equal(h.values.get('yunwei_token'),'valid-token');assert.deepEqual(h.events,[])}finally{h.done()}}
})
test('late 401 from an older session cannot clear a freshly issued login',async()=>{
  const h=setup(401);try{
    let finish;globalThis.fetch=()=>new Promise(r=>finish=r)
    const run=request('/projects');h.values.set('yunwei_token','new-token')
    finish({status:401,ok:false,json:async()=>({detail:'old session expired'})})
    await assert.rejects(run);assert.equal(h.values.get('yunwei_token'),'new-token');assert.deepEqual(h.events,[])
  }finally{h.done()}
})
test('disabled password login preserves its actual error message and status',async()=>{
  const h=setup(403)
  try {
    const message='此账号已停用，请联系管理员启用后再登录'
    globalThis.fetch=async()=>({status:403,ok:false,json:async()=>({detail:message})})
    await assert.rejects(request('/login'),e=>e.status===403 && e.message===message)
    assert.deepEqual(h.events,[])
  } finally {h.done()}
})
test('a disabled authenticated session carries its reason to the login screen',async()=>{
  const h=setup(401)
  try {
    const message='此账号已停用，请联系管理员启用后再登录'
    globalThis.fetch=async()=>({status:401,ok:false,json:async()=>({detail:message})})
    await assert.rejects(request('/projects'),e=>e.message===message)
    assert.equal(h.values.has('yunwei_token'),false)
    assert.deepEqual(h.eventDetails,[{message}])
  } finally {h.done()}
})
test('reading a delayed error body cannot invalidate a newer login',async()=>{
  const h=setup(401)
  try {
    let finishBody
    globalThis.fetch=async()=>({status:401,ok:false,json:()=>new Promise(resolve=>{finishBody=resolve})})
    const run=request('/me')
    await new Promise(resolve=>setImmediate(resolve))
    h.values.set('yunwei_token','new-token')
    finishBody({detail:'此账号已停用，请联系管理员启用后再登录'})
    await assert.rejects(run)
    assert.equal(h.values.get('yunwei_token'),'new-token')
    assert.deepEqual(h.events,[])
  } finally {h.done()}
})

test('rate limiting shows retry guidance, preserves login and suppresses repeated network requests during cooldown',async()=>{
  const h=setup(429), originalNow=Date.now
  let now=100000, calls=0
  Date.now=()=>now
  h.values.set('yunwei_token','rate-cooldown-token')
  try {
    globalThis.fetch=async()=>{calls++;return {status:429,ok:false,headers:{get:()=> '10'},json:async()=>({
      detail:'请求过于频繁，请勿连续操作，请在 10 秒后重试。',code:'RATE_LIMITED',retry_after:10})}}
    await assert.rejects(request('/projects'),e=>e.status===429 && e.retryAfter===10 && e.code==='RATE_LIMITED')
    now+=2100
    await assert.rejects(request('/projects'),e=>e.status===429 && e.retryAfter===8 && /请勿连续操作/.test(e.message))
    assert.equal(calls,1)
    assert.equal(h.values.get('yunwei_token'),'rate-cooldown-token');assert.deepEqual(h.events,[])
    now=110000
    globalThis.fetch=async()=>{calls++;return {status:200,ok:true,json:async()=>({ok:true})}}
    assert.deepEqual(await request('/projects'),{ok:true});assert.equal(calls,2)
  } finally {Date.now=originalNow;h.done()}
})

test('a late rate-limit response cannot block a newly authenticated session',async()=>{
  const h=setup(429)
  h.values.set('yunwei_token','old-rate-token')
  try {
    let finish
    globalThis.fetch=()=>new Promise(resolve=>{finish=resolve})
    const old=request('/projects')
    h.values.set('yunwei_token','new-rate-token')
    globalThis.fetch=async()=>({status:200,ok:true,json:async()=>({session:'new'})})
    await request('/me')
    finish({status:429,ok:false,headers:{get:()=> '10'},json:async()=>({detail:'稍后重试',retry_after:10})})
    await assert.rejects(old,e=>e.status===429)
    assert.deepEqual(await request('/projects'),{session:'new'})
    assert.equal(h.values.get('yunwei_token'),'new-rate-token')
  } finally {h.done()}
})

test('rate-limited mutations are not retried automatically and anonymous retry metadata is respected',async()=>{
  const h=setup(429),originalNow=Date.now
  let now=200000,calls=0
  Date.now=()=>now;h.values.delete('yunwei_token')
  try {
    globalThis.fetch=async()=>{calls++;return {status:429,ok:false,json:async()=>({retry_after:3})}}
    await assert.rejects(request('/login',{method:'POST',body:'{}'}),e=>e.retryAfter===3 && /请求过于频繁/.test(e.message))
    await assert.rejects(request('/login',{method:'POST',body:'{}'}),e=>e.status===429)
    assert.equal(calls,1);assert.deepEqual(h.events,[])
    now+=3000
    await assert.rejects(request('/login',{method:'POST',body:'{}'}),e=>e.status===429)
    assert.equal(calls,2)
  } finally {Date.now=originalNow;h.done()}
})

test('SQL downloads share rate guidance and successful binary downloads do not parse JSON',async()=>{
  const h=setup(200)
  h.values.set('yunwei_token','download-rate-token')
  try {
    const binary=new Blob(['CREATE TABLE sample (id INTEGER);'])
    globalThis.fetch=async()=>({status:200,ok:true,json:async()=>{throw new Error('SQL is not JSON')},blob:async()=>binary})
    assert.equal(await getBlob('/admin/sql/schema.sql'),binary)
    globalThis.fetch=async()=>({status:429,ok:false,headers:{get:()=> '4'},json:async()=>({detail:'请求过于频繁，请勿连续操作',retry_after:4})})
    await assert.rejects(getBlob('/admin/sql/schema.sql'),e=>e.status===429 && e.retryAfter===4 && /请勿连续操作/.test(e.message))
    assert.equal(h.values.get('yunwei_token'),'download-rate-token')
  } finally {h.done()}
})
