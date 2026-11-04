const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('/Users/zhangjianquan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const base=process.env.WIKI_UI_BASE||'http://127.0.0.1:18081';
assert.equal(new URL(base).port,'18081','Mutable UI verification is restricted to the disposable test application');
const out=path.resolve('reports');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const errors=[];
 async function login(username){const context=await browser.newContext({viewport:{width:1440,height:1000}});const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto(base+'/wiki');await page.getByRole('textbox',{name:'用户名',exact:true}).fill(username);await page.getByRole('textbox',{name:'密码',exact:true}).fill('Demo@2026');await page.getByRole('button',{name:'登录平台',exact:true}).click();await page.getByRole('tab',{name:/个人知识库/}).waitFor();await page.locator('.kb-space-select').filter({hasText:'我的个人知识库'}).waitFor();return {context,page,token:await page.evaluate(()=>localStorage.getItem('yunwei_token'))}}
 async function request(client,method,url,data){return client.context.request[method](base+'/api'+url,{headers:{Authorization:'Bearer '+client.token},...(data?{data}:{} )})}
 async function createDocument(page,title,content){await page.getByRole('button',{name:'新建文档',exact:true}).click();await page.getByLabel('文档标题 *',{exact:true}).fill(title);await page.getByLabel('正文内容 *',{exact:true}).fill(content);const response=page.waitForResponse(r=>r.url().endsWith('/api/wiki')&&r.request().method()==='POST');await page.getByRole('button',{name:'保存文档',exact:true}).click();const r=await response;assert.equal(r.status(),200,await r.text());await page.getByRole('dialog').waitFor({state:'hidden'});await page.locator('.kb-article-card').filter({hasText:title}).waitFor();return r.json()}
 async function settings(page){await page.getByRole('button',{name:'验收公共知识库空间设置',exact:true}).click();await page.getByRole('button',{name:'成员管理',exact:true}).click()}
 async function showShared(page){await page.getByRole('tab',{name:/公共知识库/}).click();await page.locator('.kb-space-select').filter({hasText:'验收公共知识库'}).click()}
 async function close(page){await page.getByRole('button',{name:'关闭弹窗',exact:true}).click()}
 try{
  const owner=await login('admin');
  const own=await createDocument(owner.page,'个人私密验收文档','PRIVATE_ISOLATED_UI_CONTENT');
  const reader=await login('operator'),outsider=await login('dept');
  assert.equal((await request(reader,'get',`/wiki/${own.id}`)).status(),404);
  assert.equal((await request(outsider,'get',`/wiki/${own.id}/versions`)).status(),404);
  await owner.page.getByRole('button',{name:'新建公共知识库',exact:true}).click();
  await owner.page.getByLabel('空间标识 *',{exact:true}).fill('UI_ACCEPTANCE');
  await owner.page.getByLabel('空间名称 *',{exact:true}).fill('验收公共知识库');
  await owner.page.getByRole('button',{name:'创建公共知识库',exact:true}).click();
  await owner.page.getByRole('dialog').waitFor({state:'hidden'});
  await settings(owner.page);
  await owner.page.getByPlaceholder('搜索姓名、用户名或部门').fill('operator');
  const candidate=owner.page.locator('select[aria-label="选择要添加的成员"] option').filter({hasText:'（operator）'});
  await candidate.waitFor({state:'attached'});await owner.page.getByRole('combobox',{name:'选择要添加的成员'}).selectOption(await candidate.getAttribute('value'));
  assert.equal(await owner.page.getByRole('combobox',{name:'新成员权限'}).inputValue(),'viewer');
  await owner.page.getByRole('button',{name:'添加成员',exact:true}).click();
  await owner.page.locator('.kb-member-row').filter({hasText:'operator'}).waitFor();
  await owner.page.screenshot({path:path.join(out,'wiki-members-desktop.png'),fullPage:true});
  await close(owner.page);
  const shared=await createDocument(owner.page,'公共协作验收文档','授权成员可见的团队经验');
  await reader.page.reload();await showShared(reader.page);
  await reader.page.locator('.kb-article-card').filter({hasText:'公共协作验收文档'}).waitFor();
  assert.equal(await reader.page.getByRole('button',{name:'新建文档',exact:true}).count(),0);
  await reader.page.locator('.kb-article-card').filter({hasText:'公共协作验收文档'}).click();
  await reader.page.locator('.article-content').filter({hasText:'授权成员可见的团队经验'}).waitFor();
  assert.equal(await reader.page.getByRole('button',{name:'编辑文档',exact:true}).count(),0);await close(reader.page);
  await outsider.page.getByRole('tab',{name:/公共知识库/}).click();
  assert.equal((await request(outsider,'get',`/wiki/${shared.id}`)).status(),404);
  await settings(owner.page);await owner.page.getByRole('combobox',{name:'林知行的权限'}).selectOption('editor');
  await owner.page.getByText('成员权限已保存',{exact:true}).waitFor();await close(owner.page);
  await reader.page.reload();await showShared(reader.page);
  await reader.page.getByRole('button',{name:'新建文档',exact:true}).waitFor();
  const contributed=await createDocument(reader.page,'成员自己的验收文档','编辑成员创建的内容');
  await reader.page.locator('.kb-article-card').filter({hasText:'成员自己的验收文档'}).click();await reader.page.getByRole('button',{name:'编辑文档',exact:true}).waitFor();await close(reader.page);
  await settings(owner.page);await owner.page.getByRole('button',{name:'移除',exact:true}).click();await owner.page.getByRole('button',{name:'确认移除',exact:true}).click();
  await owner.page.getByText('暂未添加其他成员。',{exact:true}).waitFor();await close(owner.page);
  assert.equal((await request(reader,'get',`/wiki/${contributed.id}`)).status(),404);
  assert.equal((await request(reader,'get',`/wiki/${contributed.id}/versions`)).status(),404);
  await reader.page.reload();await reader.page.getByRole('tab',{name:/公共知识库/}).click();
  await reader.page.locator('.kb-space-select').filter({hasText:'验收公共知识库'}).waitFor({state:'hidden'});
  await owner.page.screenshot({path:path.join(out,'wiki-libraries-desktop.png'),fullPage:true});
  const boxes=await owner.page.evaluate(()=>{const a=document.querySelector('.kb-toolbar .search-box').getBoundingClientRect(),b=document.querySelector('.kb-toolbar select').getBoundingClientRect(),c=document.querySelector('.kb-toolbar-right').getBoundingClientRect(),d=document.querySelector('.kb-toolbar').getBoundingClientRect();return {searchX:a.x,statusX:b.x,rightEdge:c.right,toolbarRight:d.right}});
  assert.ok(boxes.statusX>boxes.searchX+350);assert.ok(Math.abs(boxes.rightEdge-boxes.toolbarRight)<2);
  await owner.page.setViewportSize({width:390,height:844});
  assert.equal(await owner.page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false);
  await owner.page.screenshot({path:path.join(out,'wiki-libraries-mobile.png'),fullPage:true});
  await settings(owner.page);assert.equal(await owner.page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false);await owner.page.screenshot({path:path.join(out,'wiki-members-mobile.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  const report={environment:'isolated SQLite application, real browser UI',users:3,personalIsolation:true,defaultMemberRole:'viewer',viewerCannotWrite:true,editorCanCreateOwn:true,removedAuthorCannotRead:true,statusFilterRightAligned:boxes,mobileOverflow:false,pageErrors:errors};
  fs.writeFileSync(path.join(out,'wiki-ui-verification.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 } finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
