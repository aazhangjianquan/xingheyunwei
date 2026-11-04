// Read-only acceptance on the deployed service. Business data is never created here.
const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');
const {chromium}=require('/Users/zhangjianquan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const base='http://localhost:8080';
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});const errors=[];
 async function login(username){const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto(base+'/wiki');await page.getByRole('textbox',{name:'用户名',exact:true}).fill(username);await page.getByRole('textbox',{name:'密码',exact:true}).fill('Demo@2026');const pending=page.waitForResponse(r=>r.url().endsWith('/api/login'));await page.getByRole('button',{name:'登录平台',exact:true}).click();const response=await pending;assert.equal(response.status(),200,'The configured demo credential did not authenticate; stop without probing alternatives');await page.getByRole('tab',{name:/个人知识库/}).waitFor();await page.locator('.kb-space-select').filter({hasText:'我的个人知识库'}).waitFor();await page.locator('.kb-empty').waitFor();return {context,page,token:await page.evaluate(()=>localStorage.getItem('yunwei_token'))}}
 async function read(client,url){return client.context.request.get(base+'/api'+url,{headers:{Authorization:'Bearer '+client.token}})}
 try{
  const admin=await login('admin'),author=await login('operator'),outsider=await login('dept');
  const data={};
  for(const [name,client] of [['admin',admin],['author',author],['outsider',outsider]]){const response=await read(client,'/wiki/spaces');assert.equal(response.status(),200);data[name]=await response.json();assert.equal(data[name].filter(s=>s.kind==='personal').length,1)}
  const adminPrivate=data.admin.find(s=>s.kind==='personal'),authorPrivate=data.author.find(s=>s.kind==='personal');
  assert.equal((await read(admin,'/wiki?space_id='+authorPrivate.id)).status(),404);
  assert.equal((await read(author,'/wiki?space_id='+adminPrivate.id)).status(),404);
  assert.deepEqual(data.outsider.filter(s=>s.kind==='shared'),[]);
  assert.equal(data.admin.filter(s=>s.kind==='shared').length,2);
  assert.equal(data.author.filter(s=>s.kind==='shared').length,1);
  const legacy=data.author.find(s=>s.kind==='shared');assert.equal(legacy.my_role,'editor');
  await admin.page.getByRole('tab',{name:/公共知识库/}).click();await admin.page.locator('.kb-article-card').first().waitFor();
  const geometry=await admin.page.evaluate(()=>{const search=document.querySelector('.kb-toolbar .search-box').getBoundingClientRect(),status=document.querySelector('.kb-toolbar select').getBoundingClientRect(),right=document.querySelector('.kb-toolbar-right').getBoundingClientRect(),bar=document.querySelector('.kb-toolbar').getBoundingClientRect();return{searchX:search.x,statusX:status.x,rightEdge:right.right,toolbarRight:bar.right}});assert.ok(geometry.statusX>geometry.searchX+350);assert.ok(Math.abs(geometry.rightEdge-geometry.toolbarRight)<2);
  await admin.page.screenshot({path:path.resolve('reports/wiki-deployed-desktop.png'),fullPage:true});
  await admin.page.getByRole('button',{name:'运维知识库空间设置',exact:true}).click();await admin.page.getByRole('button',{name:'成员管理',exact:true}).click();await admin.page.locator('.kb-member-row').filter({hasText:'operator'}).waitFor();await admin.page.screenshot({path:path.resolve('reports/wiki-deployed-members.png'),fullPage:true});await admin.page.getByRole('button',{name:'关闭弹窗',exact:true}).click();
  await admin.page.setViewportSize({width:390,height:844});assert.equal(await admin.page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false);await admin.page.screenshot({path:path.resolve('reports/wiki-deployed-mobile.png'),fullPage:true});assert.deepEqual(errors,[]);
  const report={deployment:'local Docker, existing MySQL data',accounts:3,personalLibrariesIsolated:true,legacyPublicLibraries:2,legacyAuthorIsEditor:true,uninvitedUserHasNoSharedLibraries:true,statusFilterRightAligned:geometry,pageErrors:errors,businessDataCreated:false};fs.writeFileSync('reports/wiki-deployed-verification.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
