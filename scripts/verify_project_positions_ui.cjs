// Dedicated loopback fixture only; no production writes.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path')
const {chromium,request}=require('playwright')
const root=path.resolve(__dirname,'..'),origin='http://project-positions.test',apiOrigin='http://127.0.0.1:18078',errors=[],checks=[]
async function main(){
 const api=await request.newContext({baseURL:apiOrigin}),browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'})
 async function open(username,url){
  const login=await api.post('/api/login',{data:{username,password:'ProjectPositions123!'}});assert.equal(login.status(),200,await login.text())
  const token=(await login.json()).token,page=await browser.newPage({viewport:{width:1440,height:1080}})
  page.on('pageerror',e=>errors.push(e.message));await page.addInitScript(value=>localStorage.setItem('yunwei_token',value),token)
  await page.route('**/*',async route=>{
   const url=new URL(route.request().url());assert.equal(url.origin,origin)
   if(url.pathname.startsWith('/api/'))return route.fulfill({response:await route.fetch({url:apiOrigin+url.pathname+url.search})})
   const file=path.join(root,'frontend/dist',url.pathname.startsWith('/assets/')?url.pathname.slice(1):'index.html')
   return route.fulfill({path:file,contentType:file.endsWith('.html')?'text/html':file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'image/svg+xml'})
  });await page.goto(origin+url);await page.locator('.app-shell').waitFor();return {page,token}
 }
 const choose=async(page,position,person)=>{
  await page.getByRole('button',{name:position,exact:true}).click()
  await page.getByRole('textbox',{name:`搜索${position}人员`,exact:true}).fill(person)
  await page.getByRole('group',{name:position+'候选人员',exact:true}).getByRole('checkbox',{name:new RegExp(person)}).check()
  await page.getByRole('button',{name:/完成选择/}).click()
 }
 try{
  const {page,token}=await open('admin','/projects')
  const headers={Authorization:'Bearer '+token}
  await page.getByRole('button',{name:'维护项目',exact:true}).first().click()
  const modal=page.locator('.modal-panel')
  assert.equal(await modal.getByText('所属部门',{exact:true}).count(),0)
  for(const role of ['销售','售前','项目经理','申请人','技术负责人','智能体开发','运维负责人','运维人员','测试人员'])await modal.getByRole('button',{name:role,exact:true}).waitFor()
  for(const role of ['销售/售前','管理者','部门负责人','产品开发'])assert.equal(await modal.getByText(role,{exact:true}).count(),0)
  checks.push('project form exposes separate appointment catalogue and removes obsolete positions and department field')
  await modal.getByRole('button',{name:'销售',exact:true}).click()
  assert.equal(await modal.getByRole('group',{name:'销售候选人员',exact:true}).getByRole('checkbox',{name:/销售售前成员/}).isChecked(),true)
  await modal.getByRole('button',{name:/完成选择/}).click()
  await modal.getByRole('button',{name:'售前',exact:true}).click()
  assert.equal(await modal.getByRole('group',{name:'售前候选人员',exact:true}).getByRole('checkbox',{name:/销售售前成员/}).isChecked(),true)
  await modal.getByRole('button',{name:/完成选择/}).click()
  checks.push('merged sales-presales members migrate into both independent dropdowns')
  await choose(page,'项目经理','跨部门成员');await choose(page,'技术负责人','张建全');await choose(page,'智能体开发','跨部门成员')
  await page.screenshot({path:path.join(root,'reports/project-positions-desktop.png'),fullPage:true})
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().right<=1)
  await modal.getByRole('button',{name:'运维人员',exact:true}).click()
  await modal.getByRole('textbox',{name:'搜索运维人员人员',exact:true}).fill('张建全')
  assert.equal(await modal.getByRole('checkbox',{name:/张建全/}).isChecked(),true)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await page.screenshot({path:path.join(root,'reports/project-positions-mobile.png'),fullPage:true})
  await modal.getByRole('button',{name:/完成选择/}).click();await modal.getByRole('button',{name:'保存项目',exact:true}).click();await modal.waitFor({state:'hidden'})
  checks.push('all active platform members including other departments can be selected and saved without legacy roles')
  checks.push('mobile dropdown search and multiple selection fit the viewport')
  const projects=await(await api.get('/api/projects',{headers})).json(),project=projects.find(p=>p.name==='项目甲')
  assert.ok(project.members.some(m=>m.role==='technical_lead'&&m.name==='张建全（验证）'))
  assert.ok(project.members.some(m=>m.role==='project_manager'&&m.name==='跨部门成员'))
  assert.ok(project.members.every(m=>m.eligible))
  const users=await(await api.get('/api/users',{headers})).json(),worker=users.find(p=>p.display_name==='张建全（验证）')
  assert.deepEqual(worker.roles,['applicant']);assert.ok(worker.positions.some(p=>p.name==='运维实施工程师'))
  checks.push('saved appointments persist while original department job remains and old business grants are absent')
  await page.setViewportSize({width:1440,height:1080});await page.goto(origin+'/settings');await page.getByRole('button',{name:'审批流程',exact:true}).click()
  const nodes=page.locator('.flow-node');await nodes.first().getByText(/固定首审/).waitFor()
  assert.equal(await nodes.first().locator('select').count(),0)
  const last=nodes.last();assert.equal(await last.locator('select').first().inputValue(),'project_role')
  const select=last.locator('select').last();assert.equal(await select.inputValue(),'operator');assert.deepEqual(await select.locator('option').allTextContents(),['运维人员'])
  const ops=nodes.nth(1);assert.equal(await ops.locator('select').last().inputValue(),'ops_head')
  const labels=await ops.locator('select').last().locator('option').allTextContents()
  assert.ok(labels.includes('销售')&&labels.includes('售前')&&labels.includes('技术负责人'));assert.ok(!labels.includes('管理者')&&!labels.includes('产品开发'))
  await page.screenshot({path:path.join(root,'reports/project-positions-workflow.png'),fullPage:true})
  checks.push('workflow editor preserves organization first review and offers the new project positions and execution operator')
  const applicant=await open('owner','/changes'),member=applicant.page
  await member.getByRole('button',{name:/新建变更单/}).click()
  const form=member.locator('.modal-panel');await form.getByLabel(/^关联项目/).selectOption(String(project.id))
  assert.ok((await form.getByLabel(/^运维负责人/).locator('option').allTextContents()).includes('项目运维负责人'))
  const requestName='项目岗位流转验证-'+Date.now()
  await form.getByLabel(/^变更单名称/).fill(requestName)
  await form.getByLabel(/^维护窗口开始/).fill('2027-01-01T10:00')
  await form.getByLabel(/^维护窗口结束/).fill('2027-01-01T11:00')
  await form.getByLabel(/^变更问题说明/).fill('无旧角色的项目任命审批与回填')
  await form.getByRole('button',{name:/提交申请/}).click();await form.locator('form.form-body').waitFor({state:'hidden'})
  checks.push('submission operations dropdown uses the selected project instead of global roles')
  const bodyHeaders={Authorization:'Bearer '+applicant.token}
  const list=await(await api.get('/api/changes',{headers:bodyHeaders})).json(),item=(Array.isArray(list)?list:list.items).find(v=>v.name===requestName)
  assert.ok(item)
  for(const username of ['head','ops','worker']){
   const login=await api.post('/api/login',{data:{username,password:'ProjectPositions123!'}}),actorHeaders={Authorization:'Bearer '+(await login.json()).token}
   const detail=await(await api.get('/api/changes/'+item.id,{headers})).json(),task=detail.approvals.find(t=>t.action==='pending')
   const response=await api.post('/api/change/'+item.id+'/action',{headers:actorHeaders,data:{task_id:task.id,action:'approve',fulfillment:'执行完成',implementation_plan:'按计划执行',rollback_plan:'恢复原配置'}})
   assert.equal(response.status(),200,await response.text())
  }
  const final=await(await api.get('/api/changes/'+item.id,{headers})).json();assert.equal(final.status,'completed')
  checks.push('real request progresses through department review, project ops review and project execution without legacy grants')
  assert.deepEqual(errors,[])
  const report={result:'passed',checks:checks.length,cases:checks,errors};fs.writeFileSync(path.join(root,'reports/project-positions-ui.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report))
 }finally{await browser.close();await api.dispose()}
}
main().catch(e=>{console.error(e);process.exitCode=1})
