// This verifier writes only to the isolated loopback fixture.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path')
const {chromium,request}=require('playwright')
const root=path.resolve(__dirname,'..'),origin='http://position-approval.test',apiOrigin='http://127.0.0.1:18077'
async function main(){
 const api=await request.newContext({baseURL:apiOrigin}),errors=[]
 const login=async username=>{const r=await api.post('/api/login',{data:{username,password:'PositionApproval123!'}});assert.equal(r.status(),200,await r.text());return(await r.json()).token}
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'})
 async function open(username,url){
  const page=await browser.newPage({viewport:{width:1440,height:1080}});page.on('pageerror',e=>errors.push(e.message))
  await page.addInitScript(token=>localStorage.setItem('yunwei_token',token),await login(username))
  await page.route('**/*',async route=>{
   const url=new URL(route.request().url());assert.equal(url.origin,origin)
   if(url.pathname.startsWith('/api/'))return route.fulfill({response:await route.fetch({url:apiOrigin+url.pathname+url.search})})
   const file=path.join(root,'frontend/dist',url.pathname.startsWith('/assets/')?url.pathname.slice(1):'index.html')
   return route.fulfill({path:file,contentType:file.endsWith('.html')?'text/html':file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'image/svg+xml'})
  })
  await page.goto(origin+url);await page.locator('.app-shell').waitFor();return page
 }
 try{
  const admin=await open('pa-admin','/users')
  await admin.getByRole('button',{name:/添加成员/,exact:false}).first().click()
  const picker=admin.locator('.role-picker');await picker.getByText('基础与系统',{exact:true}).waitFor()
  assert.equal(await picker.getByText('审批与执行',{exact:true}).count(),0)
  assert.equal(await picker.getByText('项目与业务',{exact:true}).count(),0)
  assert.equal(await picker.locator('input[type=checkbox]').count(),4)
  await admin.screenshot({path:path.join(root,'reports/position-approval-roles.png'),fullPage:true})
  await admin.locator('.modal-head .icon-button').click()
  await admin.goto(origin+'/settings');await admin.getByRole('button',{name:'审批流程',exact:true}).click();await admin.getByText('审批流程设计',{exact:true}).waitFor()
  const first=admin.locator('.flow-node').first();await first.getByText(/固定首审/).waitFor()
  assert.equal(await first.getByRole('button',{name:'删除',exact:true}).count(),0)
  assert.equal(await first.locator('select').count(),0)
  await admin.screenshot({path:path.join(root,'reports/position-approval-workflow.png'),fullPage:true})
  const member=await open('pa-member','/changes')
  await member.getByRole('button',{name:/新建|发起/}).first().click()
  const modal=member.locator('.modal-panel');const dept=modal.getByLabel('本次申请部门',{exact:true})
  assert.equal(await dept.inputValue(),'')
  const options=await dept.locator('option').allTextContents();assert.equal(options.length,3)
  const leaf=await dept.locator('option').filter({hasText:'后端组'}).getAttribute('value')
  await dept.selectOption(leaf)
  await modal.getByLabel(/^关联项目/).selectOption({label:'岗位审批验证项目 · 后端组'})
  await modal.getByLabel('变更单名称 *',{exact:true}).fill('组织岗位首审 UI 验证')
  await modal.getByLabel('维护窗口开始 *',{exact:true}).fill('2027-01-01T10:00')
  await modal.getByLabel('维护窗口结束 *',{exact:true}).fill('2027-01-01T11:00')
  await modal.getByLabel(/^变更问题说明/).fill('验证已选择申请部门和固定首审')
  await member.screenshot({path:path.join(root,'reports/position-approval-desktop.png'),fullPage:true})
  await member.setViewportSize({width:390,height:844})
  await member.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().right<=1)
  assert.equal(await member.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await member.screenshot({path:path.join(root,'reports/position-approval-mobile.png'),fullPage:true})
  await member.setViewportSize({width:1440,height:1080})
  const response=member.waitForResponse(r=>r.url().endsWith('/api/changes')&&r.request().method()==='POST')
  await modal.getByRole('button',{name:/提交|创建/}).last().click()
  const submitted=await response;assert.equal(submitted.status(),200,await submitted.text());const item=await submitted.json()
  const token=await login('pa-admin'),headers={Authorization:'Bearer '+token}
  const details=await api.get('/api/changes/'+item.id,{headers});assert.equal(details.status(),200)
  const detail=await details.json();assert.equal(detail.application_department_id,Number(leaf))
  assert.equal(detail.approval_department_source,'selected_membership')
  assert.deepEqual(detail.current_node.tasks.map(t=>t.assignee_name),['后端负责人'])
  const head=await open('pa-head','/changes')
  await head.getByRole('button',{name:/新建|发起/}).first().click()
  assert.equal(await head.getByLabel('本次申请部门',{exact:true}).inputValue(),leaf)
  await head.locator('.modal-head .icon-button').click()
  await head.goto(origin+'/users');await head.getByRole('button',{name:'部门管理',exact:true}).click()
  await head.getByRole('row').filter({hasText:'研发成员'}).getByRole('button',{name:'调整组织岗位',exact:true}).click()
  const dialog=head.getByRole('dialog');await dialog.getByRole('checkbox',{name:/运维人员/}).check()
  await dialog.getByRole('button',{name:'保存组织岗位',exact:true}).click();await dialog.waitFor({state:'hidden'})
  assert.deepEqual(errors,[])
  const report={checks:12,result:'passed',errors,first_approver:'后端负责人',department_selection:'explicit_for_multiple',role_picker:'system_only',head_position_delegation:'passed'}
  fs.writeFileSync(path.join(root,'reports/position-approval-ui.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report))
 }finally{await browser.close();await api.dispose()}
}
main().catch(e=>{console.error(e);process.exitCode=1})
