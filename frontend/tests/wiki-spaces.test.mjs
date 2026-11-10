import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as vue from 'vue'
import * as icons from 'lucide-vue-next'
import { parse, compileScript } from 'vue/compiler-sfc'
import { renderToString } from 'vue/server-renderer'
import { useWikiEditor, WIKI_TITLE_LIMIT, WIKI_CATEGORY_LIMIT, wikiTextLength } from '../src/wiki-editor.js'
import { renderMarkdown } from '../src/markdown.js'

const MarkdownContent = vue.defineComponent({ props: ['content'], setup: props => () => vue.h('div', { class: 'markdown-content', innerHTML: renderMarkdown(props.content) }) })

const source=readFileSync(new URL('../src/KnowledgeManagement.vue',import.meta.url),'utf8')
const descriptor=parse(source).descriptor
const bindings=Object.entries(compileScript(descriptor,{id:'knowledge-test'}).bindings).filter(([,type])=>type!=='props').map(([name])=>name)
const script=descriptor.scriptSetup.content.replace(/^import .*$/gm,'')
const render=vue.compile(descriptor.template.content)
const personal={id:1,key:'PERSONAL_TEST',kind:'personal',name:'我的个人知识库',description:'仅本人可见',owner_id:42,owner_name:'测试成员',can_manage:true,can_write:true,my_role:'owner',article_count:1}
const shared={id:2,key:'SRE',kind:'shared',name:'团队知识库',description:'团队文档',owner_id:42,owner_name:'测试成员',can_manage:true,can_write:true,my_role:'owner',article_count:0}
const article={id:10,title:'文档',content:'私密正文',category:'规范',status:'draft',version:1,revision:1,space_id:1,author_id:42,author_name:'测试成员',can_edit:true}
const tick=()=>new Promise(resolve=>setImmediate(resolve))
function harness({get,post,put,patch,del}={}) {
  const calls=[],notices=[],timers=new Set(),effects=vue.effectScope(),drafts=new Map();let mount,unmount
  const deps={...vue,...icons,Map,Set,useWikiEditor,WIKI_TITLE_LIMIT,WIKI_CATEGORY_LIMIT,wikiTextLength,MarkdownContent,defineProps:()=>({user:{id:42,display_name:'测试成员',roles:['applicant']}}),
    defineEmits:()=>((name,text)=>notices.push(text)),onMounted:fn=>mount=fn,onBeforeUnmount:fn=>unmount=fn,
    window:{addEventListener(){},removeEventListener(){},localStorage:{getItem:key=>drafts.get(key)||null,setItem:(key,value)=>drafts.set(key,value),removeItem:key=>drafts.delete(key)}},
    setTimeout:(fn,delay)=>{const id=setTimeout(fn,delay);timers.add(id);return id},clearTimeout,
    get:async(path,options)=>{calls.push(['get',path,options]);if(get)return get(path,options);if(path==='/wiki/spaces')return [personal,shared];if(path.startsWith('/wiki/access-options'))return {departments:[],people:[]};if(path.includes('/candidates'))return [];if(path.includes('/members'))return [];if(path.endsWith('/versions'))return [];if(/^\/wiki\/\d+$/.test(path))return article;return []},
    post:async(path,body)=>{calls.push(['post',path,body]);return post?post(path,body):shared},
    put:async(path,body)=>{calls.push(['put',path,body]);return put?put(path,body):shared},
    patch:async(path,body)=>{calls.push(['patch',path,body]);return patch?patch(path,body):{}},
    del:async(path)=>{calls.push(['delete',path]);return del?del(path):{ok:true}}}
  const names=Object.keys(deps).filter(key=>key!=='default'&&/^[A-Za-z_$][\w$]*$/.test(key))
  const factory=new Function('d',`const {${names.join(',')}}=d;${script};return {${bindings.join(',')}}`)
  const s=effects.run(()=>factory(deps))
  return {s,calls,notices,mount:()=>mount(),async html(){const app=vue.createSSRApp({setup:()=>s,render});app.component('MarkdownContent',MarkdownContent);for(const[name,icon]of Object.entries(icons))if(name!=='default')app.component(name,icon);return renderToString(app)},dispose(){unmount();effects.stop();for(const id of timers)clearTimeout(id)}}
}

test('initial page is personal and a slow document request does not block discovery',async()=>{
  let finish
  const h=harness({get:p=>p==='/wiki/spaces'?[personal,shared]:new Promise(resolve=>finish=resolve)})
  try{h.mount();await tick();assert.equal(h.s.scope.value,'personal');assert.equal(h.s.selectedId.value,1);assert.equal(h.s.spacesLoading.value,false);assert.equal(h.s.articlesLoading.value,true);assert.deepEqual(h.calls.map(c=>c[1]),['/wiki/spaces','/wiki']);const html=await h.html();assert.match(html,/新建公共知识库/);assert.match(html,/仅自己可见/);finish([]);await tick()}finally{h.dispose()}
})
test('loading errors stop spinners, offer retries, and leave public creation available',async()=>{
  let fail=true
  const h=harness({get:(p,o)=>{assert.ok(o.signal instanceof AbortSignal);if(fail)throw new DOMException('timeout','TimeoutError');return p==='/wiki/spaces'?[personal]:[]}})
  try{await Promise.all([h.s.loadSpaces(),h.s.loadArticles()]);assert.equal(h.s.spacesLoading.value,false);assert.equal(h.s.articlesLoading.value,false);assert.match(h.s.spacesError.value,/超时/);assert.match(await h.html(),/重试加载空间/);h.s.newSpace();assert.equal(h.s.modal.value,'new-space');h.s.close();fail=false;await h.s.loadSpaces();assert.equal(h.s.spacesError.value,'');assert.equal(h.s.selectedId.value,1)}finally{h.dispose()}
})
test('scope and space selection clear previous data and send only valid encoded filters',async()=>{
  const h=harness()
  try{await h.s.loadSpaces();h.s.articles.value=[article];h.s.selectScope('shared');assert.equal(h.s.articles.value.length,0);assert.equal(h.s.selectedId.value,2);h.s.search.value=' 空格 & 中文 ';h.s.status.value='draft';await h.s.loadArticles();const url=new URL(h.calls.at(-1)[1],'https://example.test');assert.equal(url.searchParams.get('space_id'),'2');assert.equal(url.searchParams.get('q'),'空格 & 中文');assert.equal(url.searchParams.has('parent_id'),false);assert.equal(url.searchParams.get('status'),'draft');h.s.selectScope('personal');assert.equal(h.s.selectedId.value,1);assert.equal(h.s.search.value,'')}finally{h.dispose()}
})
test('readonly members have no create/edit/manage buttons while owners have member settings',async()=>{
  const h=harness()
  try{h.s.spaces.value=[personal,{...shared,can_manage:false,can_write:false,my_role:'viewer'}];h.s.selectScope('shared');assert.equal(h.s.canWrite.value,false);h.s.newArticle();assert.equal(h.s.modal.value,'');const html=await h.html();assert.match(html,/只读权限/);assert.doesNotMatch(html,/新建文档<\/button>/);assert.doesNotMatch(html,/团队知识库空间设置/);h.s.spaces.value=[personal,shared];await h.s.settings(shared);h.s.openMembers();assert.match(await h.html(),/成员管理/);assert.equal(h.s.candidateRole.value,'viewer');assert.equal(h.s.modal.value,'settings')}finally{h.dispose()}
})
test('inline public library creation preserves article draft and selects the committed space',async()=>{
  const h=harness()
  try{await h.s.loadSpaces();h.s.newArticle();h.s.articleForm.value.title='保留的标题';h.s.articleForm.value.content='保留的正文';h.s.newSpace();h.s.spaceForm.value={key:'SRE',name:'团队知识库',description:''};await h.s.saveSpace();assert.equal(h.s.modal.value,'create');assert.equal(h.s.articleForm.value.space_id,2);assert.equal(h.s.articleForm.value.title,'保留的标题');assert.equal(h.s.articleForm.value.content,'保留的正文');assert.equal(h.calls.filter(c=>c[0]==='post').length,1);assert.equal(h.calls.filter(c=>c[0]==='get'&&c[1]==='/wiki/spaces').length,1)}finally{h.dispose()}
})
test('cancel space creation retains draft, errors retain space form and double saves submit once',async()=>{
  let finish
  const h=harness({post:()=>new Promise(resolve=>finish=resolve)})
  try{await h.s.loadSpaces();h.s.newArticle();h.s.articleForm.value.title='保留';h.s.newSpace();h.s.close();assert.equal(h.s.modal.value,'create');assert.equal(h.s.articleForm.value.title,'保留');h.s.newSpace();h.s.spaceForm.value={key:'SRE',name:'团队'};const saving=h.s.saveSpace();await h.s.saveSpace();h.s.close();assert.equal(h.s.modal.value,'new-space');assert.equal(h.calls.filter(c=>c[0]==='post').length,1);finish(shared);await saving;assert.equal(h.s.modal.value,'create')}finally{h.dispose()}
  const failed=harness({post:()=>{throw new Error('空间标识已存在')}})
  try{failed.s.newSpace();failed.s.spaceForm.value={key:'SRE',name:'不丢失'};await failed.s.saveSpace();assert.equal(failed.s.saving.value,false);assert.equal(failed.s.spaceForm.value.name,'不丢失');assert.match(await failed.html(),/空间标识已存在/)}finally{failed.dispose()}
})
test('opening an old list item requires fresh detail authorization and clears stale content on denial',async()=>{
  let finish
  const h=harness({get:p=>new Promise((resolve,reject)=>finish={resolve,reject})})
  try{h.s.detail.value=article;const pending=h.s.openArticle(article);assert.equal(h.s.detail.value,null);assert.equal(h.s.versions.value.length,0);assert.equal(h.calls[0][1],'/wiki/10');finish.reject(Object.assign(new Error('内容不存在或无权访问'),{status:404}));await pending;assert.equal(h.s.detail.value,null);assert.equal(h.s.articleForm.value.title,undefined);assert.match(h.s.detailError.value,/无权访问/);assert.doesNotMatch(await h.html(),/私密正文/)}finally{h.dispose()}
})
test('history revocation hides newly loaded content and stale detail requests cannot reopen a closed dialog',async()=>{
  const h=harness({get:p=>p.endsWith('/versions')?Promise.reject(Object.assign(new Error('无权访问'),{status:404})):p==='/wiki/spaces'?[personal]:article})
  try{await h.s.openArticle(article);assert.equal(h.s.detail.value,null);assert.equal(h.s.versions.value.length,0);assert.match(h.s.detailError.value,/无权访问/)}finally{h.dispose()}
  let finish
  const late=harness({get:()=>new Promise(resolve=>finish=resolve)})
  try{const pending=late.s.openArticle(article);late.s.close();finish(article);await pending;assert.equal(late.s.modal.value,'');assert.equal(late.s.detail.value,null)}finally{late.dispose()}
})
test('editor sees edit action only from server capability and space stays immutable in edit form',async()=>{
  const h=harness()
  try{await h.s.loadSpaces();await h.s.openArticle(article,true);assert.equal(h.s.modal.value,'edit');assert.equal(h.s.articleForm.value.space_id,1);const html=await h.html();assert.match(html,/文档所属空间不可更换/);assert.match(html,/<select disabled required>/);assert.doesNotMatch(html,/＋ 新建公共知识库/)}finally{h.dispose()}
})
test('save document submits one manual version after autosave and ignores a second click while saving',async()=>{
  let finish
  const h=harness({put:(_path,body)=>body.save_mode==='auto'?{...article,...body,revision:2}:
    new Promise(resolve=>finish=()=>resolve({...article,...body,version:2,revision:3}))})
  try{
    await h.s.loadSpaces();await h.s.openArticle(article,true)
    h.s.articleForm.value.content='已自动保存的正文'
    await h.s.editor.save()
    assert.equal(h.s.editor.dirty.value,false)
    const saving=h.s.saveArticle();await h.s.saveArticle()
    assert.equal(h.calls.filter(c=>c[0]==='put').length,2)
    const [,path,payload]=h.calls.filter(c=>c[0]==='put').at(-1)
    assert.equal(path,'/wiki/10');assert.equal(payload.save_mode,'manual')
    assert.equal(payload.revision,2);assert.equal(payload.version,1)
    assert.match(await h.html(),/点击“保存文档”生成一个新版本/)
    finish();await saving
    assert.equal(h.s.modal.value,'');assert.equal(h.s.saving.value,false)
    assert.equal(h.calls.filter(c=>c[0]==='put'&&c[2].save_mode==='manual').length,1)
  }finally{h.dispose()}
})
test('document detail and editor ignore backdrop clicks but explicit close remains available',async()=>{
  const h=harness()
  try {
    await h.s.loadSpaces();await h.s.openArticle(article)
    h.s.backdropClose();assert.equal(h.s.modal.value,'detail');assert.equal(h.s.detail.value.id,article.id)
    await h.s.openArticle(article,true);h.s.articleForm.value.content='编辑中的未完成内容'
    h.s.backdropClose();assert.equal(h.s.modal.value,'edit');assert.equal(h.s.articleForm.value.content,'编辑中的未完成内容')
    assert.match(await h.html(),/等待同步/)
    h.s.editor.stop();await h.s.close();assert.equal(h.s.modal.value,'')
    h.s.newArticle();h.s.articleForm.value.content='新文档草稿';h.s.backdropClose()
    assert.equal(h.s.modal.value,'create');await h.s.close();assert.equal(h.s.modal.value,'')
    h.s.newArticle();assert.equal(h.s.articleForm.value.content,'新文档草稿')
  } finally {h.dispose()}
})
test('authorized detail and historical versions both render Markdown instead of its source markers',async()=>{
  const h=harness({get:path=>path.endsWith('/versions')?[{...article,id:88,content:'## 历史标题\n\n**历史重点**'}]:{...article,content:'### 修改姓名\n\n| 项目 | 结果 |\n| --- | --- |\n| 权限 | 允许 |'}})
  try {
    await h.s.openArticle(article)
    const html=await h.html()
    assert.match(html,/<h3>修改姓名<\/h3>/)
    assert.match(html,/<table>/)
    assert.match(html,/<h2>历史标题<\/h2>/)
    assert.match(html,/<strong>历史重点<\/strong>/)
    assert.doesNotMatch(html,/### 修改姓名/)
  } finally {h.dispose()}
})
test('editor preview renders unsaved text without changing the draft, writing or losing content on switching',async()=>{
  const h=harness()
  try {
    await h.s.loadSpaces();h.s.newArticle()
    h.s.articleForm.value.content='### 当前草稿\n\n**需要保存**'
    h.s.bodyMode.value='preview'
    const html=await h.html()
    assert.match(html,/aria-label="正文预览"/)
    assert.match(html,/<h3>当前草稿<\/h3>/)
    assert.match(html,/<strong>需要保存<\/strong>/)
    assert.equal(h.calls.filter(c=>c[0]==='post'||c[0]==='put').length,0)
    h.s.bodyMode.value='edit'
    assert.equal(h.s.articleForm.value.content,'### 当前草稿\n\n**需要保存**')
    h.s.bodyMode.value='preview';await h.s.close();h.s.newArticle()
    assert.equal(h.s.bodyMode.value,'edit')
    assert.equal(h.s.articleForm.value.content,'### 当前草稿\n\n**需要保存**')
  } finally {h.dispose()}
})
test('failed reload of a conflicting document retains the current editor and draft',async()=>{
  let offline=false
  const h=harness({get:path=>{if(path==='/wiki/spaces')return [personal,shared];if(path.startsWith('/wiki/access-options'))return {departments:[],people:[]};if(offline)throw new Error('网络暂不可用');return article}})
  try {
    await h.s.loadSpaces();await h.s.openArticle(article,true)
    h.s.articleForm.value.content='不能丢失的本地内容';h.s.editor.conflict.value=true
    offline=true;await h.s.reloadEditor()
    assert.equal(h.s.modal.value,'edit');assert.equal(h.s.articleForm.value.content,'不能丢失的本地内容')
    assert.equal(h.s.editor.backedUp.value,true);assert.match(h.s.formError.value,/网络暂不可用/)
  } finally {h.dispose()}
})
test('typing and paste limits title and category while preserving unfinished IME input',async()=>{
  const h=harness()
  try {
    const title={target:{value:'项'.repeat(181)}}
    h.s.limitArticleField('title',180,title)
    assert.equal(h.s.articleForm.value.title.length,180);assert.equal(title.target.value.length,180)
    const category={target:{value:'c'.repeat(61)}}
    h.s.limitArticleField('category',60,category)
    assert.equal(h.s.articleForm.value.category.length,60)
    h.s.limitArticleField('title',180,{isComposing:true,target:{value:'拼音输入'}})
    assert.equal(h.s.articleForm.value.title.length,180)
  } finally {h.dispose()}
})
test('member creation defaults to viewer, changes role and removes through separate API operations',async()=>{
  const h=harness()
  try{await h.s.loadSpaces();await h.s.settings(shared);h.s.candidateId.value=77;await h.s.mutateMember('add');const added=h.calls.find(c=>c[0]==='post');assert.deepEqual(added.slice(0,2),['post','/wiki/spaces/2/members']);assert.deepEqual(added[2],{user_id:77,role:'viewer'});await h.s.mutateMember('role',{user_id:77},'editor');assert.ok(h.calls.some(c=>c[0]==='patch'&&c[1]==='/wiki/spaces/2/members/77'&&c[2].role==='editor'));await h.s.mutateMember('remove',{user_id:77});assert.ok(h.calls.some(c=>c[0]==='delete'&&c[1]==='/wiki/spaces/2/members/77'));assert.equal(h.s.memberSaving.value,false)}finally{h.dispose()}
})
test('failed member updates retain current member list and show actual error',async()=>{
  const h=harness({patch:()=>{throw new Error('负责人权限已变更')}})
  try{await h.s.loadSpaces();await h.s.settings(shared);h.s.members.value=[{user_id:77,role:'viewer'}];await h.s.mutateMember('role',{user_id:77},'editor');assert.match(h.s.membersError.value,/负责人权限已变更/);assert.equal(h.s.members.value[0].role,'viewer');assert.equal(h.s.memberSaving.value,false)}finally{h.dispose()}
})
test('unmounted user session ignores pending space responses',async()=>{
  let finish
  const h=harness({get:()=>new Promise(resolve=>finish=resolve)})
  const pending=h.s.loadSpaces();h.dispose();finish([personal,shared]);await pending;assert.deepEqual(h.s.spaces.value,[])
})

test('shared creation offers all scopes, validates department selection and preserves member picks across scopes',async()=>{
  const h=harness()
  try {
    h.s.newSpace();await tick()
    assert.equal(h.s.spaceForm.value.access_scope,'members')
    assert.equal(h.s.spaceForm.value.include_subdepartments,true)
    const html=await h.html()
    for(const label of ['所有人','指定部门','指定人员'])assert.match(html,new RegExp(label))
    h.s.spaceForm.value.name='部门资料';h.s.spaceForm.value.access_scope='departments'
    await h.s.saveSpace();assert.match(h.s.formError.value,/至少选择一个/)
    assert.equal(h.calls.filter(c=>c[0]==='post').length,0)
    h.s.toggleAccess('department_ids',8);h.s.toggleAccess('member_ids',77)
    h.s.spaceForm.value.access_scope='all'
    assert.match(await h.html(),/原有文档也会开放/)
    h.s.spaceForm.value.access_scope='departments';await h.s.saveSpace()
    const payload=h.calls.find(c=>c[0]==='post')[2]
    assert.equal(payload.access_scope,'departments');assert.deepEqual(payload.department_ids,[8]);assert.deepEqual(payload.member_ids,[77])
  }finally{h.dispose()}
})
test('settings retains explicit editors and scope when opening, while personal library has no access choices',async()=>{
  const h=harness({get:p=>p.endsWith('/members')?[{user_id:77,display_name:'编辑人员',active:true,role:'editor'}]:p.startsWith('/wiki/access-options')?{departments:[{id:8,name:'研发部'}],people:[]}:[]})
  try {
    h.s.spaces.value=[personal,{...shared,access_scope:'departments',include_subdepartments:false,department_ids:[8],access_members:[{id:77,display_name:'编辑人员',role:'editor'}]}]
    await h.s.settings(h.s.spaces.value[1]);await tick()
    assert.deepEqual(h.s.spaceForm.value.member_ids,[77]);assert.equal(h.s.spaceForm.value.include_subdepartments,false)
    assert.match(await h.html(),/编辑人员 · 编辑/)
    h.s.toggleAccess('member_ids',77);assert.deepEqual(h.s.spaceForm.value.member_ids,[])
    await h.s.settings(personal)
    assert.doesNotMatch(await h.html(),/name="kb-access-scope"/)
  }finally{h.dispose()}
})
test('range save failure retains all selected values and directory failure never changes the current authorization',async()=>{
  const h=harness({get:()=>{throw new Error('目录暂不可用')},post:()=>{throw new Error('保存失败')}})
  try {
    h.s.newSpace();await tick();h.s.spaceForm.value.name='不能丢失'
    h.s.toggleAccess('member_ids',77);h.s.toggleAccess('department_ids',8);h.s.spaceForm.value.access_scope='departments'
    await h.s.saveSpace();assert.deepEqual(h.s.spaceForm.value.member_ids,[77]);assert.deepEqual(h.s.spaceForm.value.department_ids,[8])
    assert.equal(h.s.modal.value,'new-space');assert.equal(h.s.saving.value,false)
    assert.match(h.s.accessError.value,/目录暂不可用/);assert.match(h.s.formError.value,/保存失败/)
  }finally{h.dispose()}
})
test('directory searches retain selected people outside the result limit and ignore stale responses',async()=>{
  let resolveFirst
  const h=harness({get:()=>new Promise(resolve=>resolveFirst=resolve)})
  try {
    h.s.newSpace();h.s.selectedPeople.value[77]={id:77,display_name:'保留人员'};h.s.toggleAccess('member_ids',77)
    const pending=h.s.loadAccessOptions();resolveFirst({departments:[],people:[{id:78,display_name:'其他人员'}]});await pending
    assert.equal(h.s.selectedAccessPeople.value[0].display_name,'保留人员')
    const late=h.s.loadAccessOptions();h.s.close();resolveFirst({departments:[],people:[{id:99,display_name:'过期结果'}]});await late
    assert.equal(h.s.accessOptions.value.people.some(p=>p.id===99),false)
  }finally{h.dispose()}
})
test('removing a direct member accurately reports remaining range access',async()=>{
  const h=harness({del:()=>({ok:true,still_has_access:true})})
  try {
    h.s.spaces.value=[personal,shared];await h.s.settings(shared);await tick()
    h.s.spaceForm.value.member_ids=[77];await h.s.mutateMember('remove',{user_id:77})
    assert.ok(h.notices.includes('直接授权已移除，该成员仍可通过开放范围阅读'))
    assert.deepEqual(h.s.spaceForm.value.member_ids,[])
    h.s.removeTarget.value={display_name:'某人'};h.s.tab.value='members'
    assert.match(await h.html(),/若仍属于全员或部门开放范围/)
  }finally{h.dispose()}
})
