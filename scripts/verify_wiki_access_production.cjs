// Authorized production diagnostics: open and cancel settings, never save authorization changes.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process')
const {chromium}=require('playwright')
const origin='https://xh.agentspro.cn',socket='/tmp/yunwei-wiki-deploy-ssh.sock',root=path.resolve(__dirname,'..')
function remotePython(source){return cp.execFileSync('ssh',['-S',socket,'root@101.126.67.61','docker exec -i xinghe_api python -'],{input:source,encoding:'utf8'}).trim()}
async function main(){
 const credentials=JSON.parse(remotePython(`import json
from sqlalchemy import select
from app.database import SessionLocal
from app.models import User,WikiSpace,ResourceRequest
from app.security import make_token
with SessionLocal() as db:
 user=db.scalar(select(User).join(WikiSpace,WikiSpace.owner_id==User.id).where(User.active.is_(True),WikiSpace.kind=='shared',WikiSpace.deleted_at.is_(None)).order_by(User.id).limit(1))
 assert user is not None
 task=db.scalar(select(ResourceRequest.id).limit(1))
 print(json.dumps({'token':make_token(user.id),'task':'/resource/'+str(task)}))
`))
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'})
 const errors=[],writes=[],checks=[]
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1080}})
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message))
  await page.route('**/api/**',async route=>{
   const r=route.request()
   if(['POST','PUT','PATCH','DELETE'].includes(r.method())){writes.push(r.method()+' '+new URL(r.url()).pathname);return route.abort()}
   return route.continue()
  })
  await page.addInitScript(token=>localStorage.setItem('yunwei_token',token),credentials.token)
  await page.goto(origin+'/wiki');await page.getByRole('button',{name:'新建公共知识库',exact:true}).click()
  const dialog=page.getByRole('dialog')
  for(const label of ['所有人','指定部门','指定人员'])await dialog.getByRole('radio',{name:new RegExp(label)}).waitFor()
  assert.equal(await dialog.getByRole('radio',{name:/指定人员/}).isChecked(),true)
  await dialog.getByRole('radio',{name:/指定部门/}).check()
  await dialog.getByRole('checkbox',{name:'包含下级部门',exact:true}).waitFor()
  assert.equal(await dialog.getByRole('checkbox',{name:'包含下级部门',exact:true}).isChecked(),true)
  await dialog.getByText('正在加载权限选项…').waitFor({state:'hidden'})
  assert.equal(await dialog.getByRole('alert').count(),0)
  await page.screenshot({path:path.join(root,'reports/wiki-access-production-create-desktop.png'),fullPage:true})
  checks.push('new library offers all three scopes and defaults to specified people, with optional child departments enabled')
  await dialog.getByRole('button',{name:'取消',exact:true}).click()
  const spaces=await context.request.get(origin+'/api/wiki/spaces',{headers:{Authorization:'Bearer '+credentials.token}})
  assert.equal(spaces.status(),200);const data=await spaces.json()
  const shared=data.find(s=>s.kind==='shared'&&s.can_manage);assert.ok(shared)
  const buttons=page.locator('.kb-space-select');const position=data.findIndex(s=>s.id===shared.id)
  await buttons.nth(position).click();await page.getByRole('button',{name:'空间设置',exact:true}).click()
  assert.equal(await dialog.getByRole('radio',{name:/指定人员/}).isChecked(),true)
  await dialog.getByRole('radio',{name:/所有人/}).check()
  await dialog.getByText(/原有文档也会开放/).waitFor()
  await dialog.getByRole('radio',{name:/指定部门/}).check()
  await dialog.locator('.modal-body').evaluate(n=>n.scrollTop=0)
  await page.screenshot({path:path.join(root,'reports/wiki-access-production-settings-desktop.png'),fullPage:true})
  await page.setViewportSize({width:390,height:844});await dialog.locator('.modal-body').evaluate(n=>n.scrollTop=0)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  const sizes=await dialog.locator('input[type=radio]').evaluateAll(nodes=>nodes.map(n=>n.getBoundingClientRect().width))
  assert.ok(sizes.every(n=>n<=20))
  await page.screenshot({path:path.join(root,'reports/wiki-access-production-settings-mobile.png'),fullPage:true})
  await dialog.getByRole('button',{name:'取消',exact:true}).click()
  checks.push('existing owner can open scope settings; public-access explanation and mobile layout render without changing the space')
  await context.close()
  const anonymous=await browser.newContext({viewport:{width:1440,height:1080}}),login=await anonymous.newPage()
  login.on('pageerror',e=>errors.push(e.message))
  await login.goto(origin+credentials.task+'?feishu_task=1');await login.getByRole('heading',{name:'登录工作空间'}).waitFor()
  assert.equal(await login.evaluate(()=>localStorage.getItem('yunwei_token')),null)
  const options=await(await anonymous.request.get(origin+'/api/auth/options')).json()
  assert.equal(options.feishu_task_login_enabled,true);assert.equal(options.local_login_enabled,true);assert.equal(options.feishu_login_enabled,false)
  assert.equal((await anonymous.request.get(origin+'/api/wiki/spaces')).status(),401)
  assert.equal((await anonymous.request.get(origin+'/api/wiki/access-options')).status(),401)
  checks.push('anonymous wiki access remains denied and ordinary browser task links require password while Feishu task login stays enabled')
  await anonymous.close();assert.deepEqual(errors,[]);assert.deepEqual(writes,[])
  const report={result:'passed',url:origin,checks,pageErrors:errors,attemptedBusinessWrites:writes,spaceChangesSaved:false}
  fs.writeFileSync(path.join(root,'reports/wiki-access-production-ui.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report,null,2))
 }finally{await browser.close()}
}
main().catch(e=>{console.error(e);process.exitCode=1})
