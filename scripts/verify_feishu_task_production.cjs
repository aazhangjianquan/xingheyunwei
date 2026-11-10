// Authorized production verification. No business writes or Feishu messages.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const cp = require('node:child_process')
const {chromium} = require('playwright')
const root = path.resolve(__dirname,'..')
const origin = 'https://xh.agentspro.cn'
const socket = '/tmp/yunwei-feishu-deploy-ssh.sock'
const errors=[], writes=[], checks=[]

function remotePython(source) {
  const command = "docker exec xinghe_api python -c '"+source.replaceAll("'","'\\''")+"'"
  return cp.execFileSync('ssh',['-S',socket,'root@101.126.67.61',command],{encoding:'utf8'}).trim()
}

async function main() {
  // Root-authorized deployment diagnostics; token remains in memory and is never written to reports.
  const credentials=JSON.parse(remotePython("import json; from app.database import SessionLocal; from app.models import User,ResourceRequest; from app.roles import has_role; from app.security import make_token; from sqlalchemy import select; db=SessionLocal(); user=next(u for u in db.scalars(select(User).where(User.active.is_(True))) if has_role(u,'admin')); item=db.scalar(select(ResourceRequest.id).limit(1)); print(json.dumps({'token':make_token(user.id),'task':'/resource/'+str(item)}))"))
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'})
  try {
    const normal=await browser.newContext({viewport:{width:1440,height:1080}})
    const login=await normal.newPage()
    login.on('pageerror',e=>errors.push(e.message))
    await login.goto(origin+credentials.task+'?feishu_task=1')
    await login.getByRole('heading',{name:'登录工作空间'}).waitFor()
    assert.equal(new URL(login.url()).origin,origin)
    assert.equal(await login.evaluate(()=>localStorage.getItem('yunwei_token')),null)
    await login.screenshot({path:path.join(root,'reports/feishu-task-production-login.png'),fullPage:true})
    checks.push('ordinary browser with task card URL requires platform password')
    const option=await normal.request.get(origin+'/api/auth/options')
    assert.equal(option.status(),200)
    const options=await option.json()
    assert.equal(options.feishu_task_login_enabled,true)
    assert.equal(options.local_login_enabled,true)
    assert.equal(options.feishu_login_enabled,false)
    checks.push('production public settings enable task login while preserving password login')
    const outside=await normal.request.get(origin+'/api/auth/feishu/task/start?next='+encodeURIComponent(credentials.task),{maxRedirects:0})
    assert.equal(outside.status(),303)
    assert.equal(outside.headers().location,origin+credentials.task)
    checks.push('external browser auth endpoint redirects to the original task password entry')
    await normal.close()

    const feishu=await browser.newContext({userAgent:'Mozilla/5.0 Feishu/7.40.0',viewport:{width:390,height:844}})
    const start=await feishu.request.get(origin+'/api/auth/feishu/task/start?next='+encodeURIComponent(credentials.task),{maxRedirects:0})
    assert.equal(start.status(),303)
    const authorization=new URL(start.headers().location)
    assert.equal(authorization.origin,'https://accounts.feishu.cn')
    assert.equal(authorization.searchParams.get('redirect_uri'),origin+'/api/auth/feishu/callback')
    assert.ok(start.headers()['set-cookie'].includes('Secure'))
    assert.ok(start.headers()['set-cookie'].includes('HttpOnly'))
    assert.ok(start.headers()['set-cookie'].includes('SameSite=lax'))
    checks.push('Feishu task entry redirects to official OAuth using the exact HTTPS callback and secure browser cookie')
    const oldIp=JSON.parse(remotePython("import httpx,json; r=httpx.get('http://frontend/api/auth/feishu/task/start',params={'next':"+JSON.stringify(credentials.task)+"},headers={'Host':'101.126.67.61','User-Agent':'Feishu/7.40.0'},follow_redirects=False); print(json.dumps({'status':r.status_code,'location':r.headers.get('location'),'cookie':r.headers.get('set-cookie')}))"))
    assert.equal(oldIp.status,303)
    assert.equal(new URL(oldIp.location).origin,origin)
    assert.equal(new URL(oldIp.location).pathname,'/api/auth/feishu/task/start')
    assert.equal(new URL(oldIp.location).searchParams.get('next'),credentials.task)
    assert.equal(oldIp.cookie,null)
    checks.push('old IP message links move to HTTPS domain before OAuth cookie binding')
    const denied=await feishu.request.get(origin+'/api/auth/feishu/callback?'+new URLSearchParams({
      state:authorization.searchParams.get('state'),error:'access_denied'}),{maxRedirects:0})
    assert.equal(denied.status(),303)
    assert.equal(denied.headers().location,origin+credentials.task+'#feishu_error=denied')
    const failure=await feishu.newPage()
    failure.on('pageerror',e=>errors.push(e.message))
    await failure.goto(denied.headers().location)
    await failure.getByRole('alert').filter({hasText:'飞书授权未完成'}).waitFor()
    await failure.getByRole('button',{name:'重新通过飞书进入任务'}).waitFor()
    assert.equal(new URL(failure.url()).hash,'')
    assert.equal(await failure.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
    await failure.screenshot({path:path.join(root,'reports/feishu-task-production-mobile.png'),fullPage:true})
    checks.push('denied OAuth safely returns to the same task with password/retry controls and no mobile overflow')
    // Only remove the exact disposable authentication state created by this check.
    const testState=authorization.searchParams.get('state')
    remotePython("from app.database import SessionLocal; from app.models import OAuthState; from app.auth_feishu import digest; from sqlalchemy import delete; db=SessionLocal(); db.execute(delete(OAuthState).where(OAuthState.state_hash==digest("+JSON.stringify(testState)+"))); db.commit()")
    await feishu.close()

    const admin=await browser.newContext({viewport:{width:1440,height:1080}})
    const page=await admin.newPage()
    page.on('pageerror',e=>errors.push(e.message))
    page.on('request',r=>{
      if(['POST','PUT','PATCH','DELETE'].includes(r.method())&&new URL(r.url()).pathname.startsWith('/api/'))writes.push(r.method()+' '+new URL(r.url()).pathname)
    })
    await page.addInitScript(token=>localStorage.setItem('yunwei_token',token),credentials.token)
    await page.goto(origin+'/settings')
    await page.getByRole('checkbox',{name:'启用飞书任务免密'}).waitFor()
    assert.equal(await page.getByRole('checkbox',{name:'启用飞书任务免密'}).isChecked(),true)
    await page.getByText(origin+'/api/auth/feishu/callback',{exact:true}).waitFor()
    await page.screenshot({path:path.join(root,'reports/feishu-task-production-settings.png'),fullPage:true})
    await page.setViewportSize({width:390,height:844})
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
    checks.push('production admin sees enabled switch and correct callback; desktop and mobile settings render')
    await admin.close()
    assert.deepEqual(errors,[])
    assert.deepEqual(writes,[])
    const report={result:'passed',url:origin,checks,errors,businessWrites:writes,
      realFeishuUserConsent:'requires the user to click a task in their signed-in Feishu client'}
    fs.writeFileSync(path.join(root,'reports/feishu-task-production-ui.json'),JSON.stringify(report,null,2))
    console.log(JSON.stringify(report))
  } finally {await browser.close()}
}
main().catch(e=>{console.error(e);process.exitCode=1})
