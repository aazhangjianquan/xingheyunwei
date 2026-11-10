// This script writes only to the dedicated disposable position inheritance fixture.
const assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path')
const {chromium,request}=require('playwright')
const root=path.resolve(__dirname,'..'),apiOrigin='http://127.0.0.1:18076',origin='http://position-subtree.test'
async function main(){
 const api=await request.newContext({baseURL:apiOrigin}),errors=[]
 const login=await api.post('/api/login',{data:{username:'pi-admin',password:'PositionInheritance123!'}})
 assert.equal(login.status(),200);const token=(await login.json()).token,headers={Authorization:'Bearer '+token}
 async function call(method,url,data){const r=await api[method]('/api'+url,{headers,...(data?{data}:{})});assert.ok(r.ok(),await r.text());return r.json()}
 const departments=await call('get','/departments'),users=await call('get','/users')
 const department=name=>departments.find(d=>d.name===name),user=name=>users.find(u=>u.username===name)
 const source=department('产研中心'),middle=department('研发中心'),leaf=department('后端组'),second=department('测试组')
 const branch=await call('post','/departments',{name:'独立分支',parent_id:source.id})
 const position=await call('post',`/departments/${source.id}/positions`,{name:'统计岗位',roles:[]})
 for(const [name,target] of [['pi-member',leaf.id],['pi-member',second.id],['pi-other',second.id],['pi-head',branch.id]]){
  const person=(await call('get','/users')).find(p=>p.id===user(name).id)
  await call('put',`/users/${person.id}/organization`,{department_id:target,position_ids:[position.id],revision:person.organization_revision,set_primary:false})
 }
 const directory=await call('get','/departments')
 for(const [id,count] of [[source.id,3],[middle.id,2],[leaf.id,1],[second.id,2],[branch.id,1]]){
  const output=directory.find(d=>d.id===id).positions.find(p=>p.id===position.id)
  assert.equal(output.member_count,count)
 }
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'})
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1080}})
  page.on('pageerror',error=>errors.push(error.message))
  await page.addInitScript(value=>localStorage.setItem('yunwei_token',value),token)
  await page.route('**/*',async route=>{
   const url=new URL(route.request().url());assert.equal(url.origin,origin)
   if(url.pathname.startsWith('/api/'))return route.fulfill({response:await route.fetch({url:apiOrigin+url.pathname+url.search})})
   const file=path.join(root,'frontend/dist',url.pathname.startsWith('/assets/')?url.pathname.slice(1):'index.html')
   return route.fulfill({path:file,contentType:file.endsWith('.html')?'text/html':file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'image/svg+xml'})
  })
  await page.goto(origin+'/users');await page.getByRole('button',{name:'部门管理',exact:true}).click()
  await page.locator('.org-tree button').filter({hasText:'研发中心'}).click()
  await page.getByRole('tab',{name:/岗位/}).click()
  const card=page.locator('.org-position-list article').filter({hasText:'统计岗位'})
  await card.getByText('仅组织任职，不额外授予系统权限',{exact:true}).waitFor()
  await card.getByText(/直属 0 人 · 含下级共 2 人（去重）/).waitFor()
  assert.equal(await page.getByText('未关联权限模板',{exact:true}).count(),0)
  await page.screenshot({path:path.join(root,'reports/position-subtree-desktop.png'),fullPage:true})
  await page.setViewportSize({width:390,height:844})
  await page.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().right<=1)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await page.screenshot({path:path.join(root,'reports/position-subtree-mobile.png'),fullPage:true})
  await page.setViewportSize({width:1440,height:1080})
  await page.locator('.org-tree button').filter({hasText:'后端组'}).click()
  await card.getByText(/1 人（本部门）/).waitFor()
  assert.deepEqual(errors,[])
  const report={checks:6,status:'passed',errors,root:3,development_center:2,leaf:1,duplicates:'deduplicated',unrelated_branch:'excluded'}
  fs.writeFileSync(path.join(root,'reports/position-subtree-ui.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report))
 }finally{await browser.close();await api.dispose()}
}
main().catch(error=>{console.error(error);process.exitCode=1})
