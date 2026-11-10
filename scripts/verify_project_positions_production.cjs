// Authorized production, read-only UI verification. Authentication stays in memory.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process')
const {chromium}=require('playwright'),root=path.resolve(__dirname,'..'),origin=process.env.XINGHE_VERIFY_ORIGIN||'http://101.126.67.61'
async function main(){
 const code="from app.database import SessionLocal; from app.models import User; from app.roles import has_role; from app.security import make_token; from sqlalchemy import select; db=SessionLocal(); user=next(u for u in db.scalars(select(User).where(User.active==True)) if has_role(u,'admin')); print(make_token(user.id))"
 const token=cp.execFileSync('ssh',['-S','/tmp/codex-xinghe-v108.sock','root@101.126.67.61','docker','exec','xinghe_api','python','-c',"'"+code.replaceAll("'","'\\''")+"'"],{encoding:'utf8'}).trim()
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'}),errors=[],writes=[],checks=[]
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1080}})
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(['POST','PUT','PATCH','DELETE'].includes(r.method())&&new URL(r.url()).pathname.startsWith('/api/'))writes.push(r.method()+' '+new URL(r.url()).pathname)})
  await page.addInitScript(value=>localStorage.setItem('yunwei_token',value),token)
  await page.goto(origin+'/projects')
  await page.getByRole('button',{name:/创建项目/}).click()
  const modal=page.locator('.modal-panel');await modal.getByRole('button',{name:'销售',exact:true}).waitFor()
  for(const name of ['销售','售前','项目经理','申请人','技术负责人','智能体开发','运维负责人','运维人员','测试人员'])await modal.getByRole('button',{name,exact:true}).waitFor()
  for(const name of ['所属部门','销售/售前','管理者','部门负责人','产品开发'])assert.equal(await modal.getByText(name,{exact:true}).count(),0)
  checks.push('production project form exposes the new catalogue and removes obsolete fields')
  const data=await page.evaluate(async()=>{
   const headers={Authorization:'Bearer '+localStorage.yunwei_token},read=async url=>{const r=await fetch('/api'+url,{headers});if(!r.ok)throw Error('Read failed: '+url);return r.json()}
   return {candidates:await read('/project-candidates'),roles:await read('/roles'),projects:await read('/projects')}
  })
  assert.ok(data.candidates.length);assert.ok(data.candidates.every(p=>p.active));assert.deepEqual(Object.keys(data.candidates[0]).sort(),['active','department','display_name','id'])
  assert.deepEqual(data.roles.map(r=>r.id).sort(),['admin','applicant','department_head','knowledge_admin','license_manager'])
  const activeIds=new Set(data.candidates.map(p=>p.id))
  assert.ok(data.projects.every(p=>p.members.every(m=>m.eligible===activeIds.has(m.user_id))))
  await modal.getByRole('button',{name:'运维人员',exact:true}).click()
  const group=modal.getByRole('group',{name:'运维人员候选人员',exact:true})
  assert.equal(await group.getByRole('checkbox').count(),data.candidates.length)
  const person=data.candidates.find(p=>p.display_name.includes('张建全'))||data.candidates[0]
  await modal.getByRole('textbox',{name:'搜索运维人员人员',exact:true}).fill(person.display_name)
  assert.ok(await group.getByRole('checkbox').count()>0);await group.scrollIntoViewIfNeeded()
  await page.screenshot({path:path.join(root,'reports/project-positions-production-desktop.png'),fullPage:true})
  checks.push('production candidates include all active accounts and original project appointments remain eligible')
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().right<=1)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await page.screenshot({path:path.join(root,'reports/project-positions-production-mobile.png'),fullPage:true})
  checks.push('production member search and mobile layout work')
  await modal.getByRole('button',{name:'关闭',exact:true}).click();await page.setViewportSize({width:1440,height:1080})
  await page.goto(origin+'/settings');await page.getByRole('button',{name:'审批流程',exact:true}).click()
  const nodes=page.locator('.flow-node');await nodes.first().getByText(/固定首审/).waitFor();assert.equal(await nodes.first().locator('select').count(),0)
  const last=nodes.last();assert.equal(await last.locator('select').first().inputValue(),'project_role');assert.equal(await last.locator('select').last().inputValue(),'operator')
  await page.screenshot({path:path.join(root,'reports/project-positions-production-workflow.png'),fullPage:true})
  checks.push('production flow keeps organization first review and executes through project operators')
  assert.deepEqual(errors,[]);assert.deepEqual(writes,[])
  const report={result:'passed',checks:checks.length,cases:checks,url:'http://101.126.67.61',browser_origin:origin,errors,writes}
  fs.writeFileSync(path.join(root,'reports/project-positions-production-ui.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report))
 }finally{await browser.close()}
}
main().catch(e=>{console.error(e);process.exitCode=1})
