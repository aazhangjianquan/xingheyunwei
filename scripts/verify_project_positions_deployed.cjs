// Read-only local Compose smoke check. Tokens stay in memory; forms are never saved.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process')
const {chromium}=require('playwright'),root=path.resolve(__dirname,'..')
async function main(){
 const code="from app.database import SessionLocal; from app.models import User; from app.roles import has_role; from app.security import make_token; from sqlalchemy import select; db=SessionLocal(); user=next(u for u in db.scalars(select(User).where(User.active==True)) if has_role(u,'admin')); print(make_token(user.id))"
 const token=cp.execFileSync('docker',['compose','exec','-T','--index','1','api','python','-c',code],{cwd:root,encoding:'utf8'}).trim()
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'}),errors=[],writes=[],checks=[]
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1080}})
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(['POST','PUT','PATCH','DELETE'].includes(r.method())&&new URL(r.url()).pathname.startsWith('/api/'))writes.push(r.method()+' '+new URL(r.url()).pathname)})
  await page.addInitScript(value=>localStorage.setItem('yunwei_token',value),token)
  await page.goto('http://localhost:8080/projects');await page.getByRole('button',{name:/创建项目/}).click()
  const modal=page.locator('.modal-panel');await modal.getByRole('button',{name:'销售',exact:true}).waitFor()
  for(const name of ['销售','售前','项目经理','申请人','技术负责人','智能体开发','运维负责人','运维人员','测试人员'])await modal.getByRole('button',{name,exact:true}).waitFor()
  for(const name of ['所属部门','销售/售前','管理者','部门负责人','产品开发'])assert.equal(await modal.getByText(name,{exact:true}).count(),0)
  checks.push('deployed project creation uses the independent positions and has no department field')
  const candidates=await page.evaluate(async()=>{const r=await fetch('/api/project-candidates',{headers:{Authorization:'Bearer '+localStorage.yunwei_token}});if(!r.ok)throw Error('candidate API failed');return r.json()})
  assert.ok(candidates.length);assert.ok(candidates.every(p=>p.active));assert.deepEqual(Object.keys(candidates[0]).sort(),['active','department','display_name','id'])
  await modal.getByRole('button',{name:'运维人员',exact:true}).click()
  const group=modal.getByRole('group',{name:'运维人员候选人员',exact:true});assert.equal(await group.getByRole('checkbox').count(),candidates.length)
  await modal.getByRole('textbox',{name:'搜索运维人员人员',exact:true}).fill(candidates[0].display_name)
  assert.ok(await group.getByRole('checkbox').count()>0)
  await group.scrollIntoViewIfNeeded()
  await page.screenshot({path:path.join(root,'reports/project-positions-deployed-desktop.png'),fullPage:true})
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().right<=1)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await page.screenshot({path:path.join(root,'reports/project-positions-deployed-mobile.png'),fullPage:true})
  checks.push('deployed dropdown lists all active platform members, supports search and fits mobile')
  await modal.getByRole('button',{name:'关闭',exact:true}).click();await page.setViewportSize({width:1440,height:1080})
  await page.goto('http://localhost:8080/settings');await page.getByRole('button',{name:'审批流程',exact:true}).click()
  const nodes=page.locator('.flow-node');await nodes.first().getByText(/固定首审/).waitFor()
  assert.equal(await nodes.first().locator('select').count(),0)
  const last=nodes.last();assert.equal(await last.locator('select').first().inputValue(),'project_role')
  assert.equal(await last.locator('select').last().inputValue(),'operator')
  await page.screenshot({path:path.join(root,'reports/project-positions-deployed-workflow.png'),fullPage:true})
  checks.push('deployed workflow keeps organization first review and routes execution to project operators')
  assert.deepEqual(errors,[]);assert.deepEqual(writes,[])
  const report={result:'passed',checks:checks.length,cases:checks,url:'http://localhost:8080',production_deployed:false,errors,writes}
  fs.writeFileSync(path.join(root,'reports/project-positions-deployed-ui.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report))
 }finally{await browser.close()}
}
main().catch(e=>{console.error(e);process.exitCode=1})
