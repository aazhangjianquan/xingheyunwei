// Only the dedicated loopback fixture is used; no real members are messaged.
const assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path')
const {chromium,request}=require('playwright')
const root=path.resolve(__dirname,'..'),origin='http://wiki-access.test',apiOrigin='http://127.0.0.1:18079'
async function main(){
 const api=await request.newContext({baseURL:apiOrigin}),errors=[],out=path.join(root,'reports')
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'})
 async function login(username){const r=await api.post('/api/login',{data:{username,password:'WikiAccessFixture123!'}});assert.equal(r.status(),200,await r.text());return(await r.json()).token}
 const token=await login('wiki-owner'),reader=await login('wiki-reader'),outsider=await login('wiki-outsider')
 const headers=token=>({Authorization:'Bearer '+token})
 async function read(token,id,status=200){for(const suffix of ['', '/versions'])assert.equal((await api.get('/api/wiki/'+id+suffix,{headers:headers(token)})).status(),status)}
 const page=await browser.newPage({viewport:{width:1440,height:1080}})
 page.on('pageerror',e=>errors.push(e.message))
 await page.addInitScript(value=>localStorage.setItem('yunwei_token',value),token)
 await page.route('**/*',async route=>{
  const url=new URL(route.request().url());assert.equal(url.origin,origin)
  if(url.pathname.startsWith('/api/'))return route.fulfill({response:await route.fetch({url:apiOrigin+url.pathname+url.search})})
  const file=path.join(root,'frontend/dist',url.pathname.startsWith('/assets/')?url.pathname.slice(1):'index.html')
  return route.fulfill({path:file,contentType:file.endsWith('.html')?'text/html':file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'image/svg+xml'})
 })
 try{
  await page.goto(origin+'/wiki');await page.getByRole('button',{name:'新建知识库',exact:true}).or(page.getByRole('button',{name:'新建公共知识库',exact:true})).click()
  const dialog=page.getByRole('dialog')
  await dialog.getByLabel('知识库名称 *',{exact:true}).fill('产品交付知识库')
  await dialog.getByPlaceholder('输入新分类或选择已有分类').fill('产品与交付')
  await dialog.getByRole('radio',{name:/所有人/}).check()
  await dialog.getByText(/原有文档也会开放/).waitFor()
  await dialog.locator('.modal-body').evaluate(node=>node.scrollTop=0);await page.screenshot({path:path.join(out,'wiki-access-all-desktop.png'),fullPage:true})
  const created=page.waitForResponse(r=>r.url().endsWith('/api/wiki/spaces')&&r.request().method()==='POST')
  await dialog.getByRole('button',{name:'创建知识库',exact:true}).click()
  const cr=await created;assert.equal(cr.status(),200,await cr.text());let space=await cr.json()
  await dialog.waitFor({state:'hidden'})
  const doc=await api.post('/api/wiki',{headers:headers(token),data:{space_id:space.id,title:'交付资料访问验收',category:'方案',content:'## 交付资料\n\n范围变更后按最新权限阅读。'}})
  assert.equal(doc.status(),200,await doc.text());const article=await doc.json()
  await read('',article.id,401);await read(reader,article.id);await read(outsider,article.id)
  async function settings(){await page.getByRole('button',{name:'空间设置',exact:true}).click();await dialog.getByRole('radio',{name:/指定部门/}).waitFor()}
  async function save(){const response=page.waitForResponse(r=>r.url().endsWith('/api/wiki/spaces/'+space.id)&&r.request().method()==='PUT');await dialog.getByRole('button',{name:'保存设置',exact:true}).click();const r=await response;assert.equal(r.status(),200,await r.text());space=await r.json();await dialog.waitFor({state:'hidden'})}
  await settings();await dialog.getByRole('radio',{name:/指定部门/}).check()
  await dialog.getByRole('checkbox',{name:'产品研发中心',exact:true}).check()
  assert.equal(await dialog.getByRole('checkbox',{name:'包含下级部门',exact:true}).isChecked(),true)
  await dialog.locator('.modal-body').evaluate(node=>node.scrollTop=0);await page.screenshot({path:path.join(out,'wiki-access-departments-desktop.png'),fullPage:true});await save()
  await read(reader,article.id);await read(outsider,article.id,404)
  await settings();await dialog.getByRole('checkbox',{name:'包含下级部门',exact:true}).uncheck();await save();await read(reader,article.id,404)
  await settings();await dialog.getByRole('radio',{name:/指定人员/}).check()
  await dialog.getByRole('textbox',{name:'搜索开放人员',exact:true}).fill('周晓宁')
  await dialog.getByRole('checkbox',{name:/周晓宁/}).check()
  await dialog.locator('.modal-body').evaluate(node=>node.scrollTop=0);await page.screenshot({path:path.join(out,'wiki-access-people-desktop.png'),fullPage:true})
  await page.setViewportSize({width:390,height:844})
  await dialog.locator('.modal-body').evaluate(node=>node.scrollTop=0);await page.screenshot({path:path.join(out,'wiki-access-people-mobile.png'),fullPage:true})
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false)
  const sizes=await dialog.locator('input[type=radio]').evaluateAll(nodes=>nodes.map(n=>({width:n.getBoundingClientRect().width,height:n.getBoundingClientRect().height})))
  assert.ok(sizes.every(s=>s.width<=20&&s.height<=20),JSON.stringify(sizes))
  await save();await read(reader,article.id,404);await read(outsider,article.id)
  await page.reload();await page.locator('.kb-space-select').filter({hasText:'产品交付知识库'}).last().click();await settings()
  assert.equal(await dialog.getByRole('radio',{name:/指定人员/}).isChecked(),true)
  await dialog.getByRole('button',{name:'移除授权人员周晓宁',exact:true}).waitFor()
  await dialog.getByRole('button',{name:'移除授权人员周晓宁',exact:true}).click();await save();await read(outsider,article.id,404)
  assert.deepEqual(errors,[])
  const report={environment:'isolated SQLite fixture + production frontend build',scopes:['all','departments','members'],allRequiresLogin:true,departmentInheritanceAndRevocation:true,personSelectionPersisted:true,removedPersonDenied:true,mobileOverflow:false,pageErrors:errors}
  fs.writeFileSync(path.join(out,'wiki-access-ui-verification.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report,null,2))
 }catch(error){await page.screenshot({path:path.join(out,'wiki-access-ui-error.png'),fullPage:true});throw error}finally{await browser.close();await api.dispose()}
}
main().catch(e=>{console.error(e);process.exit(1)})
