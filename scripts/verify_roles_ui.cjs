// Local fixtures only. No platform accounts, roles or departments are modified.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')
const root = path.resolve(__dirname,'..'), dist=path.join(root,'frontend/dist'), reports=path.join(root,'reports')
const roles=JSON.parse(fs.readFileSync(path.join(reports,'roles-catalog.json')))
const origin='http://roles-ui.test'
const settings={site_name:'星河项目协作平台',local_login_enabled:true,feishu_login_enabled:false}
const admin={id:1,username:'admin',display_name:'系统管理员',department:'平台管理',roles:['admin'],active:true}
const head={id:3,username:'head',display_name:'技术负责人',department:'产品研发部',roles:['technical_head'],active:true}
let identity=admin
let employee={id:2,username:'member',display_name:'协作成员',department:'产品研发部',roles:['applicant','tester'],canonical_roles:['applicant','tester'],active:true,can_manage_roles:true,managed_roles:['product_developer']}
let saved=[]
const errors=[]
function departments(){return [{name:'产品研发部',member_count:2,heads:[{id:3,name:'陈负责人',roles:['technical_head'],role_names:['技术负责人']}]},{name:'商务部',member_count:0,heads:[]}]}
async function routeFixture(route){
  const req=route.request(),url=new URL(req.url()),name=url.pathname
  assert.equal(url.origin,origin,'No external platform requests')
  if(!name.startsWith('/api/')){
    const file=path.join(dist,name.startsWith('/assets/')?name.slice(1):'index.html')
    return route.fulfill({path:file,contentType:file.endsWith('.html')?'text/html':file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'image/svg+xml'})
  }
  let data
  if(name==='/api/me')data=identity
  else if(name==='/api/auth/options')data=settings
  else if(name==='/api/roles')data=roles
  else if(name==='/api/departments')data=departments()
  else if(name==='/api/users' && req.method()==='POST'){
    const body=req.postDataJSON();saved.push({method:'POST',body});employee={...employee,...body,id:4,canonical_roles:body.roles};data=employee
  }else if(name.startsWith('/api/users/') && req.method()==='PATCH'){
    const body=req.postDataJSON();saved.push({method:'PATCH',body});employee={...employee,...body,canonical_roles:body.roles};data=employee
  }else if(name==='/api/users')data=url.searchParams.has('page')?{items:[employee],total:1,page:1,page_size:10,total_pages:1}:[employee,head]
  else if(name==='/api/tasks')data={items:[],total:0,page:1,page_size:10,total_pages:1}
  else if(name==='/api/projects')data=[]
  else throw new Error('Unhandled route '+req.method()+' '+name)
  return route.fulfill({json:data})
}
;(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'})
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1080}})
    page.on('pageerror',e=>{errors.push(e.message);console.error('PAGE ERROR:',e.message)});page.on('console',m=>{if(m.type()==='error') console.error('BROWSER:',m.text())})
    await page.addInitScript(()=>localStorage.setItem('yunwei_token','ui-fixture'))
    await page.route('**/*',routeFixture)
    await page.goto(origin+'/users')
    await page.getByRole('button',{name:'添加成员',exact:true}).waitFor({timeout:8000}).catch(async error=>{console.error(await page.locator('body').innerText());await page.screenshot({path:path.join(reports,'roles-ui-debug.png'),fullPage:true});throw error})
    assert.equal(await page.locator('.department-responsibility-grid>div').count(),4)
    await page.screenshot({path:path.join(reports,'roles-departments-desktop.png'),fullPage:true})
    await page.getByRole('button',{name:'添加成员',exact:true}).click()
    const modal=page.locator('.member-panel')
    assert.deepEqual(await modal.locator('legend').allTextContents(),['基础与系统','审批与执行','项目与业务'])
    const business=modal.locator('fieldset').filter({has:page.locator('legend',{hasText:'项目与业务'})})
    assert.deepEqual(await business.locator('.role-card strong').allTextContents(),['销售/售前','项目经理','智能体开发','产品开发'])
    await modal.locator('.role-card').filter({has:page.locator('strong',{hasText:'商业化负责人'})}).locator('input').check()
    await modal.locator('.role-search input').fill('测试人员')
    await modal.locator('.role-card input').check()
    await modal.locator('.role-search input').clear()
    await modal.locator('input[autocomplete=off]').first().fill('new-member')
    await modal.locator('input[placeholder="成员真实姓名"]').fill('张同学')
    await modal.locator('input[list=member-departments]').fill('商务部')
    await modal.locator('input[autocomplete=new-password]').fill('NewMember123!')
    await modal.getByRole('button',{name:'创建成员',exact:true}).click()
    await modal.waitFor({state:'hidden'})
    assert.deepEqual(saved[0].body.roles,['applicant','commercial_head','tester'])
    assert.equal(saved[0].body.department,'商务部')
    await page.getByRole('button',{name:'管理角色',exact:true}).click()
    await modal.locator('input[list=edit-member-departments]').fill('产品研发部')
    await modal.locator('.role-search input').fill('智能体')
    assert.equal(await modal.locator('.role-card').count(),2)
    await modal.locator('.role-search input').clear()
    await page.screenshot({path:path.join(reports,'roles-picker-desktop.png'),fullPage:true})
    await modal.getByRole('button',{name:'保存角色',exact:true}).click()
    await modal.waitFor({state:'hidden'})
    assert.equal(saved.at(-1).body.department,'产品研发部')
    identity=head;employee={id:2,display_name:'协作成员',department:'产品研发部',roles:['applicant','tester'],canonical_roles:['applicant','tester'],active:true,can_manage_roles:true,managed_roles:['product_developer']}
    await page.reload()
    await page.getByRole('button',{name:'管理角色',exact:true}).waitFor()
    assert.equal(await page.getByRole('button',{name:'添加成员',exact:true}).count(),0)
    assert.equal(await page.getByRole('columnheader',{name:'飞书通知 open_id'}).count(),0)
    await page.getByRole('button',{name:'管理角色',exact:true}).click()
    assert.equal(await modal.locator('.role-card input:disabled').count(),2)
    const editable=modal.locator('.role-card').filter({has:page.locator('strong',{hasText:'产品开发',exact:true})}).locator('input')
    assert.equal(await editable.isEnabled(),true)
    await editable.check()
    await page.setViewportSize({width:390,height:844})
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
    assert.equal(await modal.evaluate(node=>node.scrollWidth<=node.clientWidth),true)
    await page.screenshot({path:path.join(reports,'roles-picker-mobile.png'),fullPage:true})
    await modal.getByRole('button',{name:'保存角色',exact:true}).click()
    await modal.waitFor({state:'hidden'})
    assert.deepEqual(saved.at(-1).body,{roles:['applicant','tester','product_developer']})
    assert.deepEqual(errors,[])
    const report={roleGroups:'passed',businessRoles:'passed',createMultipleRoles:'passed',adminChangesDepartment:'passed',managerLockedRoles:'passed',managerScopedSave:'passed',mobileOverflow:false,pageErrors:errors}
    fs.writeFileSync(path.join(reports,'roles-ui-verification.json'),JSON.stringify(report,null,2))
    console.log(JSON.stringify(report,null,2))
  }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1})
