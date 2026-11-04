import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as vue from 'vue'
import * as icons from 'lucide-vue-next'
import { readBootstrapSettings } from '../src/bootstrap.js'
const script=readFileSync(new URL('../src/App.vue',import.meta.url),'utf8').match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm,'')
const name='元知星辰'
const member={id:42,display_name:'测试成员',roles:['applicant']}
const config={site_name:name,feishu_login_enabled:false,local_login_enabled:true}
function harness({token='existing-session',bootstrap=config,path='/projects',get,post}={}) {
  const values=new Map(token?[['yunwei_token',token]]:[])
  const calls=[],timers=new Set()
  const location={pathname:path,search:''}
  const listeners=new Map()
  const doc={title:'',getElementById:()=>bootstrap?{textContent:JSON.stringify(bootstrap)}:null}
  const routes={ '/auth/options':config,'/me':member,'/roles':[],'/projects':[],'/wiki':[] }
  const scope=vue.effectScope()
  const deps={...vue,...icons,readBootstrapSettings:()=>readBootstrapSettings(doc), onMounted:()=>{},
    get:async p=>{calls.push(p);return get?get(p,routes):routes[p]},post:post || (async()=>({user:member,token:'new-session'})),put:async()=>{},patch:async()=>{},
    document:doc,localStorage:{getItem:key=>values.get(key)||null,setItem:(key,v)=>values.set(key,v),removeItem:key=>values.delete(key)},
    window:{location,history:{pushState:(a,b,path)=>{location.pathname=path},replaceState(){}},
      setTimeout:(fn,ms)=>{const id=setTimeout(fn,ms);timers.add(id);return id},addEventListener:(key,fn)=>listeners.set(key,fn)},
  }
  const keys=Object.keys(deps).filter(k=>k!=='default'&&/^[A-Za-z_$][\w$]*$/.test(k))
  const factory=new Function('d',`const {${keys.join(',')}}=d;\n${script}\nreturn {user,authOptions,restoringSession,startupError,page,restoreSession,login,loginForm,loginError,saving,expireSession,logout,profileOpen,openProfile,applyProfile};`)
  const s=scope.run(()=>factory(deps))
  return {s,calls,values,doc,location,dispose(){scope.stop();for(const id of timers)clearTimeout(id)}}
}
const error=status=>Object.assign(new Error('接口暂不可用'),{status})
test('administrators, each business role and multiple-role members can open the same account settings',async()=>{
  const roles=['admin','applicant','department_head','ops_head','leader','operator','project_owner','project_manager','project_director','sales_head','sales_director','development_head','license_manager']
  for(const grants of [...roles.map(role=>[role]),['admin','operator','license_manager']]) {
    const identity={...member,roles:grants}
    const h=harness({get:(p,defaults)=>p==='/me'?identity:defaults[p]})
    try {
      await h.s.restoreSession()
      h.s.openProfile()
      assert.equal(h.s.profileOpen.value,true,grants.join(','))
      h.s.applyProfile({...identity,display_name:'更新姓名'})
      assert.equal(h.s.user.value.display_name,'更新姓名')
      assert.deepEqual([...h.s.user.value.roles],grants)
    } finally {h.dispose()}
  }
})
test('ordinary members can open personal settings, update their visible name and close it on logout',async()=>{
  const h=harness()
  try {
    await h.s.restoreSession()
    h.s.openProfile()
    assert.equal(h.s.profileOpen.value,true)
    h.s.applyProfile({...member,display_name:'新姓名'})
    assert.equal(h.s.user.value.display_name,'新姓名')
    h.s.applyProfile({...member,id:99,display_name:'其他会话'})
    assert.equal(h.s.user.value.display_name,'新姓名')
    h.s.logout()
    assert.equal(h.s.profileOpen.value,false)
    assert.equal(h.s.user.value,null)
  } finally {h.dispose()}
})
test('refresh waits for /me without showing login or a hardcoded brand, then retains /projects',async()=>{
  let finish
  const h=harness({get:(p,defaults)=>p==='/me'?new Promise(r=>finish=r):defaults[p]})
  try {
    assert.equal(h.s.restoringSession.value,true)
    assert.equal(h.s.authOptions.value.site_name,name)
    const loading=h.s.restoreSession();await new Promise(resolve=>setImmediate(resolve))
    assert.equal(h.s.restoringSession.value,true);assert.equal(h.s.user.value,null)
    assert.equal(h.doc.title.includes('星河'),false)
    finish(member);await loading
    assert.equal(h.s.restoringSession.value,false);assert.equal(h.s.user.value.id,member.id)
    assert.equal(h.s.page.value,'projects');assert.equal(h.location.pathname,'/projects')
    assert.equal(h.values.get('yunwei_token'),'existing-session')
  } finally {h.dispose()}
})
test('no bootstrap shows a neutral loading screen until DB settings arrive',async()=>{
  let finish
  const h=harness({token:null,bootstrap:null,get:(p,defaults)=>p==='/auth/options'?new Promise(r=>finish=r):defaults[p]})
  try {
    assert.equal(h.s.authOptions.value.site_name,'');const run=h.s.restoreSession()
    assert.equal(h.s.restoringSession.value,true);finish(config);await run
    assert.equal(h.s.authOptions.value.site_name,name);assert.equal(h.s.restoringSession.value,false)
    assert.equal(h.s.user.value,null)
  } finally {h.dispose()}
})
test('page failures (403/500/network) do not remove a valid session',async()=>{
  for(const failure of [error(403),error(500),new TypeError('Failed to fetch')]) {
    const h=harness({path:'/wiki',get:(p,defaults)=>{if(p==='/wiki')throw failure;return defaults[p]}})
    try {
      await h.s.restoreSession();assert.equal(h.s.user.value.id,member.id)
      assert.equal(h.s.startupError.value,'');assert.equal(h.values.get('yunwei_token'),'existing-session')
      assert.equal(h.location.pathname,'/wiki')
    } finally {h.dispose()}
  }
})
test('temporary /me failure retains URL/token and retries successfully without signing in',async()=>{
  let fail=true
  const h=harness({get:(p,defaults)=>{if(p==='/me'&&fail)throw error(503);return defaults[p]}})
  try {
    await h.s.restoreSession();assert.ok(h.s.startupError.value)
    assert.equal(h.values.get('yunwei_token'),'existing-session');assert.equal(h.location.pathname,'/projects')
    fail=false;await h.s.restoreSession();assert.equal(h.s.user.value.id,member.id);assert.equal(h.s.startupError.value,'')
  } finally {h.dispose()}
})

test('a rate-limited session refresh shows the actual retry instruction without signing out',async()=>{
  const message='请求过于频繁，请勿连续操作，请在 6 秒后重试。'
  const h=harness({get:(p,defaults)=>{if(p==='/me')throw Object.assign(new Error(message),{status:429});return defaults[p]}})
  try {
    await h.s.restoreSession()
    assert.ok(h.s.startupError.value.includes(message))
    assert.equal(h.values.get('yunwei_token'),'existing-session')
    assert.equal(h.location.pathname,'/projects')
    assert.equal(h.s.loginError.value,'')
  } finally {h.dispose()}
})
test('a missing bootstrap and public config failure retain the session and URL',async()=>{
  const h=harness({bootstrap:null,get:(p,defaults)=>{if(p==='/auth/options')throw error(500);return defaults[p]}})
  try {await h.s.restoreSession();assert.ok(h.s.startupError.value);assert.equal(h.values.get('yunwei_token'),'existing-session');assert.equal(h.s.authOptions.value.site_name,'');assert.equal(h.location.pathname,'/projects')}
  finally {h.dispose()}
})
test('only an expired login requires signing in; successful login returns to original deep link',async()=>{
  const h=harness({get:(p,defaults)=>{if(p==='/me')throw error(401);return defaults[p]}})
  try {
    await h.s.restoreSession();assert.equal(h.s.user.value,null);assert.equal(h.values.has('yunwei_token'),false)
    assert.equal(h.location.pathname,'/projects');assert.ok(h.s.loginError.value)
    await h.s.login();assert.equal(h.s.page.value,'projects');assert.equal(h.s.user.value.id,member.id);assert.equal(h.values.get('yunwei_token'),'new-session')
  } finally {h.dispose()}
})
test('public bootstrap is allowlisted and malformed JSON never becomes a fake identity',()=>{
  assert.equal(readBootstrapSettings({getElementById:()=>({textContent:'bad json'})}),null)
  const parsed=readBootstrapSettings({getElementById:()=>({textContent:JSON.stringify({...config,token:'bad',user:{roles:['admin']}})})})
  assert.deepEqual(parsed,config)
})

test('verified identity releases the startup screen while a slow page is still loading',async()=>{
  let finish
  const h=harness({path:'/wiki',get:(p,d)=>p==='/wiki'?new Promise(r=>finish=r):d[p]})
  try {
    await h.s.restoreSession()
    assert.equal(h.s.restoringSession.value,false)
    assert.equal(h.s.user.value.id,member.id)
    assert.deepEqual(h.calls,['/me']) // Knowledge requests belong to the mounted knowledge component.
    if (finish) finish([])
  } finally {h.dispose()}
})
test('project refresh validates identity without duplicate directory requests',async()=>{
  const h=harness()
  try {await h.s.restoreSession();assert.deepEqual(h.calls,['/me'])}
  finally {h.dispose()}
})
test('development fallback starts public settings and identity requests concurrently',async()=>{
  let finishOptions,finishMe
  const h=harness({bootstrap:null,get:p=>new Promise(r=>{
    if(p==='/auth/options')finishOptions=r
    if(p==='/me')finishMe=r
  })})
  try {
    const run=h.s.restoreSession()
    assert.deepEqual(h.calls,['/auth/options','/me'])
    finishMe(member);finishOptions(config);await run
    assert.equal(h.s.user.value.id,member.id);assert.equal(h.s.authOptions.value.site_name,name)
  } finally {h.dispose()}
})

test('disabled account on refresh keeps the specific reason instead of a generic session error',async()=>{
  const message='此账号已停用，请联系管理员启用后再登录'
  const h=harness({get:(p,defaults)=>{if(p==='/me')throw Object.assign(new Error(message),{status:401});return defaults[p]}})
  try {
    await h.s.restoreSession()
    assert.equal(h.s.user.value,null)
    assert.equal(h.values.has('yunwei_token'),false)
    assert.equal(h.s.loginError.value,message)
    assert.equal(h.s.startupError.value,'')
    assert.equal(h.location.pathname,'/projects')
  } finally {h.dispose()}
})
test('session expiry event displays the API reason while closing the authenticated view',async()=>{
  const h=harness()
  try {
    await h.s.restoreSession()
    const message='此账号已停用，请联系管理员启用后再登录'
    h.s.expireSession({detail:{message}})
    assert.equal(h.s.user.value,null)
    assert.equal(h.values.has('yunwei_token'),false)
    assert.equal(h.s.loginError.value,message)
  } finally {h.dispose()}
})
test('password login shows actual failures and clears stale errors when credentials change',async()=>{
  for(const [status,message] of [[403,'此账号已停用，请联系管理员启用后再登录'],[401,'用户名或密码错误']]) {
    const h=harness({token:null,post:async()=>{throw Object.assign(new Error(message),{status})}})
    try {
      await h.s.restoreSession()
      await h.s.login()
      assert.equal(h.s.loginError.value,message)
      assert.equal(h.s.user.value,null)
      assert.equal(h.s.saving.value,false)
      h.s.loginForm.value.username='another-account'
      await vue.nextTick()
      assert.equal(h.s.loginError.value,'')
      assert.equal(h.values.has('yunwei_token'),false)
    } finally {h.dispose()}
  }
})
