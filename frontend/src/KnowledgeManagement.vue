<script setup>
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { BookOpen, Plus, Search, SlidersHorizontal, X, LockKeyhole, Users, FileText, ChevronRight, Trash2, RotateCcw, FolderOpen } from 'lucide-vue-next'
import { get, post, put, patch, del } from './api'
import { useWikiEditor, WIKI_TITLE_LIMIT, WIKI_CATEGORY_LIMIT, wikiTextLength } from './wiki-editor'
import MarkdownContent from './MarkdownContent.vue'

const props = defineProps({user:{type:Object,required:true}})
const emit = defineEmits(['notice'])
const scope = ref('personal'), spaces = ref([]), selectedId = ref(null), articles = ref([])
const spacesLoading = ref(false), spacesError = ref(''), articlesLoading = ref(false), articlesError = ref('')
const search = ref(''), status = ref('all'), modal = ref(''), tab = ref('general')
const spaceForm = ref({}), articleForm = ref({}), detail = ref(null), versions = ref([])
const saving = ref(false), formError = ref(''), detailLoading = ref(false), detailError = ref('')
const members = ref([]), membersLoading = ref(false), membersError = ref('')
const candidates = ref([]), candidateSearch = ref(''), candidateId = ref(null), candidateRole = ref('viewer')
const candidatesLoading = ref(false), candidatesError = ref(''), memberSaving = ref(false), returnToArticle = ref(false)
const memberFilter = ref(''), removeTarget = ref(null)
const view = ref('all'), categoryFilter = ref(null), spaceSearch = ref('')
const trash = ref([]), trashLoading = ref(false), trashError = ref(''), deleteName = ref('')
const accessOptions = ref({departments:[],people:[]}), accessLoading = ref(false), accessError = ref('')
const accessSearch = ref(''), accessDepartmentSearch = ref(''), selectedPeople = ref({})
let accessRevision=0, accessTimer
const accessModes = [{value:'all',label:'所有人',hint:'平台内所有已登录用户可阅读'}, {value:'departments',label:'指定部门',hint:'按组织归属自动开放阅读'}, {value:'members',label:'指定人员',hint:'仅负责人和指定人员可访问'}]
const accessLabel = space=>space.kind==='personal'?'仅自己可见':({all:'所有人可见',departments:'指定部门可见',members:'指定人员可见'}[space.access_scope || 'members'])
const accessDepartmentChoices = computed(()=>accessOptions.value.departments.filter(d=>(d.path || d.name).includes(accessDepartmentSearch.value.trim())))
const selectedDepartments = computed(()=>(spaceForm.value.department_ids || []).map(id=>accessOptions.value.departments.find(d=>d.id===id) || spaceForm.value.access_departments?.find(d=>d.id===id) || {id,name:'部门 #'+id}))
const selectedAccessPeople = computed(()=>(spaceForm.value.member_ids || []).map(id=>selectedPeople.value[id] || {id,display_name:'成员 #'+id}))
async function loadAccessOptions() {
  const revision=++accessRevision
  accessLoading.value=true;accessError.value=''
  try {
    const data=await getTimed('/wiki/access-options?'+new URLSearchParams({q:accessSearch.value.trim()}))
    if(disposed || revision!==accessRevision)return
    if(!Array.isArray(data?.departments)||!Array.isArray(data?.people))throw new Error('权限选项加载失败，请重试')
    accessOptions.value=data
    for(const person of data.people)selectedPeople.value[person.id]=person
  } catch(error){if(!disposed && revision===accessRevision)accessError.value=message(error,'加载开放范围')}
  finally{if(!disposed && revision===accessRevision)accessLoading.value=false}
}
function resetAccessOptions(space={}) {
  ++accessRevision;clearTimeout(accessTimer);accessSearch.value='';accessDepartmentSearch.value='';accessError.value=''
  selectedPeople.value=Object.fromEntries((space.access_members || []).map(p=>[p.id,p]))
  void loadAccessOptions()
}
function toggleAccess(field,id) {
  const values=spaceForm.value[field] || []
  spaceForm.value[field]=values.includes(id)?values.filter(value=>value!==id):[...values,id]
}
watch(accessSearch,()=>{++accessRevision;clearTimeout(accessTimer);accessTimer=setTimeout(loadAccessOptions,300)})
const downloadedDraft = ref('')
const bodyMode = ref('edit')
const categories = computed(()=>[...new Set(spaces.value.map(space=>space.category || '').filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh-CN')))
const articleCategories = computed(()=>[...new Set(articles.value.map(article=>article.category).filter(Boolean))])
let listRevision=0, spaceRevision=0, detailRevision=0, memberRevision=0, candidateRevision=0, timer, candidateTimer, disposed=false
const selected = computed(()=>spaces.value.find(space=>space.id===selectedId.value) || null)
const visibleSpaces = computed(()=>spaces.value.filter(space=>
  (view.value==='all' || (view.value==='mine'?space.my_role==='owner':space.my_role!=='owner')) &&
  (categoryFilter.value===null || (space.category || '')===categoryFilter.value) &&
  (!spaceSearch.value.trim() || (space.name+' '+(space.category || '')).toLowerCase().includes(spaceSearch.value.trim().toLowerCase()))))
const writableSpaces = computed(()=>spaces.value.filter(space=>space.can_write))
const canWrite = computed(()=>Boolean(selected.value?.can_write))
const settingSpace = computed(()=>spaces.value.find(space=>space.id===spaceForm.value.id))
const filteredMembers = computed(()=>members.value.filter(person=>[person.display_name,person.username,person.department].join(' ').toLowerCase().includes(memberFilter.value.trim().toLowerCase())))
const ownerChoices = computed(()=>members.value.filter(person=>person.active))
const labels = {draft:'草稿',published:'已发布',archived:'已归档'}
const shortDate = value=>value?new Date(value).toLocaleDateString('zh-CN'):'—'
const notice = text=>emit('notice',text)
const getTimed = path=>get(path,{signal:AbortSignal.timeout(15000)})
const message = (error, label)=>error.name==='TimeoutError'?`${label}超时，请重试`:error.message || `${label}失败`
const unauthorized = error=>[403,404].includes(error.status)
function limitArticleField(field, limit, event) {
  // Let an IME finish composing before applying the same limit as the API.
  if(event.isComposing)return
  const value=Array.from(event.target.value).slice(0,limit).join('')
  if(event.target.value!==value)event.target.value=value
  articleForm.value[field]=value
}
const editor = useWikiEditor({form:articleForm,userId:props.user.id,browser:window,api:{post,put},
  onSaved(result) {
    const index=articles.value.findIndex(item=>item.id===result.id)
    if(index>=0)articles.value[index]=result
  },
  onDenied(error) {clearSensitive(true);notice(message(error,'保存文档'));void loadSpaces()},
})

async function loadSpaces() {
  const revision=++spaceRevision
  spacesLoading.value=true;spacesError.value=''
  try {
    const result=await getTimed('/wiki/spaces')
    if(disposed || revision!==spaceRevision)return
    if(!Array.isArray(result))throw new Error('空间接口返回格式无效')
    spaces.value=result
    const current=result.find(space=>space.id===selectedId.value)
    if(!current) {
      const next=result.find(space=>space.personal_user_id===props.user.id) || result[0]
      selectedId.value=next?.id || null;scope.value=next?.kind || 'personal'
      scheduleArticles()
    }
    if(!current && detail.value) {detail.value=null;versions.value=[];if(modal.value==='detail')modal.value=''}
    if(!current && modal.value==='edit') {clearSensitive(true)}
  } catch(error) {
    if(!disposed && revision===spaceRevision)spacesError.value=message(error,'知识空间加载')
  } finally {if(!disposed && revision===spaceRevision)spacesLoading.value=false}
}
async function loadArticles() {
  const revision=++listRevision
  articlesLoading.value=true;articlesError.value='';articles.value=[]
  if(!selectedId.value){articles.value=[];articlesLoading.value=false;return}
  const params=new URLSearchParams()
  if(selectedId.value && selected.value?.kind === 'shared')params.set('space_id',String(selectedId.value))

  if(status.value!=='all')params.set('status',status.value)
  if(search.value.trim())params.set('q',search.value.trim())
  try {
    const result=await getTimed(params.size?`/wiki?${params}`:'/wiki')
    if(!disposed && revision===listRevision)articles.value=result
  } catch(error) {
    if(disposed || revision!==listRevision)return
    articlesError.value=message(error,'文档加载')
    if(unauthorized(error)) {clearSensitive(true);void loadSpaces()}
  } finally {if(!disposed && revision===listRevision)articlesLoading.value=false}
}
function scheduleArticles(delay=0) {
  ++listRevision;clearTimeout(timer);articles.value=[]
  articlesLoading.value=true;timer=setTimeout(loadArticles,delay)
}
function selectScope(value) {
  if(saving.value || memberSaving.value)return
  scope.value=value;search.value='';status.value='all'
  selectedId.value=spaces.value.find(space=>space.kind===value)?.id || null
  clearSensitive();scheduleArticles()
}
function selectSpace(space) {
  if(saving.value || memberSaving.value)return
  scope.value=space.kind;selectedId.value=space.id;search.value='';status.value='all';clearSensitive();scheduleArticles()
}
function clearSensitive(revoked=false) {
  editor.stop({forget:revoked})
  ++detailRevision;detail.value=null;versions.value=[];articleForm.value={}
  if(['detail','edit'].includes(modal.value))modal.value=''
}
watch([search,status],()=>scheduleArticles(300))
watch([view, categoryFilter, spaceSearch], () => {
  const current = visibleSpaces.value.find(space => space.id === selectedId.value)
  if (!current) {
    const next = visibleSpaces.value[0]
    selectedId.value = next?.id || null
    scope.value = next?.kind || 'personal'
    clearSensitive()
    scheduleArticles()
  }
})

function newArticle() {
  if(!canWrite.value || saving.value)return
  downloadedDraft.value=''
  bodyMode.value='edit'
  formError.value=''
  editor.start({title:'',category:'',content:'',space_id:selected.value.id,parent_id:null,status:'draft'})
  modal.value='create'
}
function newSpace() {
  if(saving.value || memberSaving.value)return
  returnToArticle.value=['create','edit'].includes(modal.value)
  // Editing cannot move a document, so do not replace the editing form.
  if(modal.value==='edit')return
  formError.value='';spaceForm.value={key:'KB_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,8),name:'',description:'',category:categoryFilter.value || '',kind:'shared',owner_id:props.user.id,access_scope:'members',include_subdepartments:true,department_ids:[],member_ids:[]}
  modal.value='new-space';resetAccessOptions()
}
async function settings(space) {
  if(!space.can_manage || saving.value || memberSaving.value)return
  returnToArticle.value=false;formError.value='';members.value=[];membersError.value=''
  spaceForm.value={id:space.id,key:space.key,name:space.name,description:space.description,category:space.category || '',kind:space.kind,owner_id:space.owner_id,access_scope:space.access_scope || 'members',include_subdepartments:space.include_subdepartments ?? true,department_ids:[...(space.department_ids || [])],member_ids:(space.access_members || []).map(p=>p.id),access_departments:space.access_departments || []}
  tab.value='general';modal.value='settings'
  if(space.kind==='shared'){void loadMembers();resetAccessOptions(space)}
}
function backdropClose() {
  if(!['detail','create','edit'].includes(modal.value))void close()
}
async function close() {
  if(saving.value || memberSaving.value)return
  if(['create','edit'].includes(modal.value)) {
    if(modal.value==='edit' && editor.dirty.value) {
      saving.value=true
      await editor.save()
      saving.value=false
    }
    if(editor.dirty.value && !editor.backedUp.value && downloadedDraft.value!==JSON.stringify(articleForm.value)) {
      formError.value='当前内容尚未保存，请先保存文档或下载草稿后再关闭。';return
    }
    if(editor.dirty.value)notice(editor.backedUp.value?'未同步内容已保留在此浏览器，重新编辑时会恢复草稿':'当前草稿已下载，请保管下载的文件')
    editor.stop()
  }
  ++accessRevision;clearTimeout(accessTimer);++detailRevision;++memberRevision;++candidateRevision;clearTimeout(candidateTimer)
  if(modal.value==='new-space' && returnToArticle.value){modal.value='create';returnToArticle.value=false}
  else {modal.value='';returnToArticle.value=false;detail.value=null;versions.value=[];articleForm.value={}}
  formError.value='';detailError.value='';removeTarget.value=null
}
async function saveSpace() {
  if(saving.value)return
  if(!/^[A-Za-z0-9_-]{2,40}$/.test(spaceForm.value.key) || !spaceForm.value.name?.trim()) {
    formError.value='请填写空间名称及 2–40 位英文、数字、下划线或短横线标识';return
  }
  if(spaceForm.value.access_scope==='departments' && !spaceForm.value.department_ids?.length){formError.value='请至少选择一个开放部门';return}
  if(memberSaving.value || (modal.value==='settings' && membersLoading.value))return
  saving.value=true;formError.value=''
  const creating=modal.value==='new-space'
  try {
    const result=creating?await post('/wiki/spaces',spaceForm.value):await put(`/wiki/spaces/${spaceForm.value.id}`,spaceForm.value)
    if(disposed)return
    ++spaceRevision;spacesLoading.value=false;spacesError.value=''
    const index=spaces.value.findIndex(space=>space.id===result.id)
    if(index<0)spaces.value.push(result);else spaces.value[index]=result
    if(creating && returnToArticle.value) {
      articleForm.value.space_id=result.id;modal.value='create';returnToArticle.value=false
    } else {
      view.value='all';categoryFilter.value=null;spaceSearch.value='';scope.value=result.kind;selectedId.value=result.id;modal.value='';scheduleArticles()
    }
    notice(creating?'知识库已创建，'+accessLabel(result):'空间设置已保存')
  } catch(error){if(!disposed){formError.value=message(error,'保存空间');if(unauthorized(error))void refreshAccess()}}
  finally{if(!disposed)saving.value=false}
}
async function openTrash() {
  modal.value='trash';trash.value=[];trashError.value='';trashLoading.value=true
  try {const result=await getTimed('/wiki/spaces/trash');if(!disposed)trash.value=result}
  catch(error){if(!disposed)trashError.value=message(error,'回收站加载')}
  finally{if(!disposed)trashLoading.value=false}
}
function confirmDelete() {deleteName.value='';formError.value='';modal.value='delete-space'}
async function deleteSpace() {
  if(saving.value || deleteName.value!==spaceForm.value.name)return
  saving.value=true;formError.value=''
  try {
    await del(`/wiki/spaces/${spaceForm.value.id}`,{confirm_name:deleteName.value})
    if(disposed)return
    ++spaceRevision;clearSensitive();spaces.value=spaces.value.filter(space=>space.id!==spaceForm.value.id)
    view.value='all';categoryFilter.value=null;spaceSearch.value='';modal.value=''
    const next=spaces.value.find(space=>space.personal_user_id===props.user.id) || spaces.value[0]
    selectedId.value=next?.id || null;scope.value=next?.kind || 'personal'
    notice('知识库已移入回收站，成员访问权限已收回');await loadArticles()
  } catch(error){if(!disposed)formError.value=message(error,'删除知识库')}
  finally{if(!disposed)saving.value=false}
}
async function restoreSpace(space) {
  if(saving.value)return
  saving.value=true;trashError.value=''
  try {
    await post(`/wiki/spaces/${space.id}/restore`,{})
    if(disposed)return
    trash.value=trash.value.filter(item=>item.id!==space.id);notice('知识库已恢复，原文档和成员权限已保留')
    await loadSpaces()
  } catch(error){if(!disposed)trashError.value=message(error,'恢复知识库')}
  finally{if(!disposed)saving.value=false}
}
async function saveArticle() {
  if(saving.value)return
  saving.value=true;formError.value=''
  try {
    const ok=await editor.save({create:true,manual:true})
    if(disposed || !ok)return
    const space=spaces.value.find(space=>space.id===articleForm.value.space_id)
    if(space){scope.value=space.kind;selectedId.value=space.id}
    editor.stop()
    modal.value='';articleForm.value={};notice('文档已保存')
    await Promise.all([loadSpaces(),loadArticles()])
  } catch(error){if(!disposed){formError.value=message(error,'保存文档');if(unauthorized(error))void refreshAccess()}}
  finally{if(!disposed)saving.value=false}
}
async function openArticle(article, edit=false) {
  downloadedDraft.value=''
  bodyMode.value='edit'
  editor.stop()
  const revision=++detailRevision
  detail.value=null;versions.value=[];detailLoading.value=true;detailError.value='';formError.value='';modal.value='detail'
  try {
    const result=await getTimed(`/wiki/${article.id}`)
    if(disposed || revision!==detailRevision)return
    if(edit){if(!result.can_edit)throw Object.assign(new Error('当前没有编辑权限'),{status:403});editor.start(result);modal.value='edit'}
    else {
      // Do not reuse the list body or reveal it before fresh authorization.
      detail.value=result
      const history=await getTimed(`/wiki/${article.id}/versions`)
      if(!disposed && revision===detailRevision)versions.value=history
    }
  } catch(error){
    if(disposed || revision!==detailRevision)return
    detail.value=null;versions.value=[];articleForm.value={};modal.value='detail'
    detailError.value=message(error,'读取文档')
    if(unauthorized(error)){editor.stop({forget:true});articles.value=[];void loadSpaces()}
  } finally{if(!disposed && revision===detailRevision)detailLoading.value=false}
}
function downloadDraft() {
  const file=new Blob([`# ${articleForm.value.title || '未命名文档'}\n\n${articleForm.value.content || ''}`],{type:'text/markdown;charset=utf-8'})
  const url=URL.createObjectURL(file),link=document.createElement('a')
  link.href=url;link.download=(articleForm.value.title || '知识库草稿').replace(/[\\/:*?"<>|]/g,'_')+'.md'
  link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
  downloadedDraft.value=JSON.stringify(articleForm.value)
}
async function reloadEditor() {
  if(saving.value)return
  const id=articleForm.value.id
  saving.value=true
  try {
    const latest=await getTimed(`/wiki/${id}`)
    if(disposed)return
    if(!latest.can_edit)throw Object.assign(new Error('当前没有编辑权限'),{status:403})
    if(!window.confirm('当前未同步内容将被服务器版本替换，建议先下载草稿。确认读取最新版本？'))return
    editor.stop({forget:true});editor.start(latest);downloadedDraft.value='';formError.value=''
  } catch(error) {
    if(!disposed){formError.value=message(error,'读取最新版本');if(unauthorized(error))clearSensitive(true)}
  } finally {if(!disposed)saving.value=false}
}
async function loadMembers(syncAccess=true) {
  const id=spaceForm.value.id,revision=++memberRevision
  membersLoading.value=true;membersError.value=''
  try {
    const result=await getTimed(`/wiki/spaces/${id}/members`)
    if(!disposed && revision===memberRevision){members.value=result;if(syncAccess)spaceForm.value.member_ids=result.map(p=>p.user_id);for(const person of result)selectedPeople.value[person.user_id]={...person,id:person.user_id}}
  } catch(error){if(!disposed && revision===memberRevision){membersError.value=message(error,'成员加载');if(unauthorized(error))void refreshAccess()}}
  finally{if(!disposed && revision===memberRevision)membersLoading.value=false}
}
async function loadCandidates() {
  const id=spaceForm.value.id,revision=++candidateRevision
  candidatesLoading.value=true;candidatesError.value='';candidates.value=[];candidateId.value=null
  try {
    const result=await getTimed(`/wiki/spaces/${id}/candidates?${new URLSearchParams({q:candidateSearch.value.trim()})}`)
    if(!disposed && revision===candidateRevision)candidates.value=result
  } catch(error){if(!disposed && revision===candidateRevision)candidatesError.value=message(error,'成员目录加载')}
  finally{if(!disposed && revision===candidateRevision)candidatesLoading.value=false}
}
function openMembers() {
  tab.value='members';candidateSearch.value='';memberFilter.value='';candidateRole.value='viewer'
  void loadCandidates()
}
watch(candidateSearch,()=>{++candidateRevision;clearTimeout(candidateTimer);candidates.value=[];candidateId.value=null;candidateTimer=setTimeout(loadCandidates,300)})
async function mutateMember(action, person=null, role=null) {
  if(memberSaving.value)return
  if(action==='add' && !candidateId.value){membersError.value='请选择要添加的成员';return}
  memberSaving.value=true;membersError.value=''
  const path=`/wiki/spaces/${spaceForm.value.id}/members`
  try {
    let removed
    const addedId=candidateId.value
    if(action==='add')await post(path,{user_id:candidateId.value,role:candidateRole.value})
    else if(action==='remove')removed=await del(`${path}/${person.user_id}`)
    else await patch(`${path}/${person.user_id}`,{role})
    if(disposed)return
    if(action==='add' && !spaceForm.value.member_ids.includes(addedId))spaceForm.value.member_ids.push(addedId)
    if(action==='remove')spaceForm.value.member_ids=spaceForm.value.member_ids.filter(id=>id!==person.user_id)
    removeTarget.value=null;candidateId.value=null;candidateRole.value='viewer'
    notice(action==='remove'?(removed?.still_has_access?'直接授权已移除，该成员仍可通过开放范围阅读':'成员已移除，后续请求不再有访问权限'):'成员权限已保存')
    await Promise.all([loadMembers(false),loadCandidates()])
  } catch(error){if(!disposed){membersError.value=message(error,'修改成员');if(unauthorized(error))void refreshAccess()}}
  finally{if(!disposed)memberSaving.value=false}
}
async function refreshAccess() {
  await loadSpaces()
  if(disposed)return
  if(modal.value==='settings' && !spaces.value.find(space=>space.id===spaceForm.value.id)?.can_manage){modal.value='';members.value=[];candidates.value=[]}
  if(detail.value)void openArticle(detail.value)
  if(modal.value==='edit') {
    const draft={...articleForm.value}
    try {
      const latest=await getTimed(`/wiki/${draft.id}`)
      if(modal.value==='edit' && articleForm.value.id===draft.id) {
        if(!latest.can_edit)clearSensitive(true)
        else editor.retry()
      }
    }
    catch(error){if(unauthorized(error) && modal.value==='edit' && articleForm.value.id===draft.id)clearSensitive(true)}
  }
  void loadArticles()
}
onMounted(()=>{void loadSpaces();window.addEventListener('focus',refreshAccess)})
onBeforeUnmount(()=>{++accessRevision;clearTimeout(accessTimer);disposed=true;editor.dispose();++spaceRevision;++listRevision;++detailRevision;++memberRevision;++candidateRevision;clearTimeout(timer);clearTimeout(candidateTimer);window.removeEventListener('focus',refreshAccess)})
</script>

<template>
  <section class="knowledge-management">
    <div class="kb-heading"><div><h1>知识库</h1><p>按项目、主题自由分类，将知识积累在合适的地方。</p></div><div class="kb-heading-actions"><button class="secondary-button" @click="openTrash"><Trash2 :size="16"/>回收站</button><button class="primary-button" aria-label="新建公共知识库" @click="newSpace"><Plus :size="17"/>新建知识库</button></div></div>
    <div class="kb-layout">
      <aside class="kb-spaces">
        <div class="kb-spaces-heading"><strong>知识库目录</strong><span>{{spaces.length}}</span></div>
        <label class="kb-library-search"><Search :size="15"/><input v-model="spaceSearch" aria-label="查找知识库" placeholder="查找知识库…"/></label>
        <div class="kb-directory-filters"><select v-model="view" aria-label="知识库范围"><option value="all">全部可访问</option><option value="mine">我负责的</option><option value="joined">与我共享的</option></select><select v-model="categoryFilter" aria-label="知识库分类"><option :value="null">全部分类</option><option value="">未分类</option><option v-for="category in categories" :key="category" :value="category">{{category}}</option></select></div>
        <div v-if="spacesLoading" class="kb-hint" role="status">正在加载知识空间…</div>
        <div v-else-if="spacesError" class="kb-error" role="alert">{{spacesError}}<button class="text-button" @click="loadSpaces">重试加载空间</button></div>
        <div v-for="space in visibleSpaces" :key="space.id" class="kb-space" :class="{selected:space.id===selectedId}">
          <button class="kb-space-select" :aria-pressed="space.id===selectedId" @click="selectSpace(space)"><span class="kb-space-title"><LockKeyhole v-if="space.kind==='personal'" :size="15"/><Users v-else :size="15"/><strong>{{space.name}}</strong></span><small>{{space.category || '未分类'}} · {{space.article_count}} 篇</small></button>
          <button v-if="space.can_manage" class="icon-button" :aria-label="space.name+'空间设置'" title="空间设置" @click="settings(space)"><SlidersHorizontal :size="15"/></button>
        </div>
        <div v-if="!spacesLoading && !spacesError && !visibleSpaces.length" class="kb-hint">{{spaces.length?'没有匹配的知识库，请调整筛选条件。':'还没有知识库，创建一个开始积累吧。'}}</div>
      </aside>
      <section class="kb-main">
        <div v-if="selected" class="kb-space-banner"><div class="kb-banner-content"><div class="kb-library-meta"><span class="kb-category-label"><FolderOpen :size="14"/>{{selected.category || '未分类'}}</span><span class="kb-access-label"><LockKeyhole v-if="selected.kind==='personal'" :size="13"/><Users v-else :size="13"/>{{accessLabel(selected)}}</span><span class="kb-role-label">{{selected.my_role==='owner'?'负责人':selected.my_role==='editor'?'编辑成员':'只读成员'}}</span></div><h2>{{selected.name}}</h2><p>{{selected.description || '在这里整理文档、分享经验与记录想法。'}}</p></div><button v-if="selected.can_manage" class="secondary-button kb-settings-button" @click="settings(selected)"><SlidersHorizontal :size="15"/>空间设置</button></div>
        <div v-else class="kb-space-banner"><div><h2>选择一个知识库</h2><p>从左侧目录打开，或新建知识库。分类和访问范围可以分别设置。</p></div></div>
        <div class="kb-toolbar"><label class="search-box"><Search :size="17"/><input v-model="search" aria-label="搜索知识库" placeholder="搜索标题或正文…"/></label><div class="kb-toolbar-right"><select v-model="status" aria-label="文档状态"><option value="all">全部状态</option><option value="draft">草稿</option><option value="published">已发布</option><option value="archived">已归档</option></select><span>{{articlesLoading?'查询中…':'共 '+articles.length+' 篇文档'}}</span><button v-if="canWrite" class="primary-button" @click="newArticle"><Plus :size="16"/>新建文档</button></div></div>
        <p v-if="selected && !canWrite" class="kb-readonly">你拥有此空间的只读权限，如需创建文档请联系负责人。</p>
        <div v-if="articlesError" class="kb-error" role="alert">{{articlesError}}<button class="text-button" @click="loadArticles">重试加载文档</button></div>
        <div v-else-if="articlesLoading" class="kb-empty" role="status">正在加载文档…</div>
        <div v-else-if="!articles.length" class="kb-empty"><FileText :size="32"/><strong>{{search?'没有匹配的文档':'这里还没有文档'}}</strong><span>{{canWrite?'从一篇笔记、方案或复盘开始积累。':'负责人及编辑成员发布的内容会显示在这里。'}}</span><button v-if="canWrite" class="secondary-button" @click="newArticle">创建第一篇文档</button></div>
        <div v-else class="wiki-grid"><button v-for="article in articles" :key="article.id" class="wiki-card kb-article-card" @click="openArticle(article)"><div class="wiki-card-top"><span class="wiki-category" :title="article.category">{{article.category}}</span><span>{{labels[article.status]}}</span></div><div class="wiki-doc-icon"><FileText :size="24"/></div><h3 :title="article.title">{{article.title}}</h3><p>{{article.content.replace(/[#*`]/g,'').slice(0,100)}}</p><div class="wiki-card-bottom"><span :title="article.author_name">{{article.author_name}}</span><span class="wiki-date">v{{article.version}} · {{shortDate(article.updated_at)}}</span><ChevronRight :size="14"/></div></button></div>
      </section>
    </div>

    <div v-if="modal" class="modal-backdrop" @click.self="backdropClose"><section class="modal-panel kb-modal" :class="{'kb-wide':['detail','settings','create','edit'].includes(modal),'kb-editor':['create','edit'].includes(modal)}" role="dialog" aria-modal="true" :aria-label="modal==='new-space'?'新建知识库':modal==='settings'?'空间设置':modal==='trash'?'知识库回收站':modal==='delete-space'?'删除知识库':modal==='detail'?'文档详情':modal==='edit'?'编辑文档':'新建文档'">
      <header class="modal-head"><div><span class="eyebrow">{{modal==='settings'?'SPACE SETTINGS':'KNOWLEDGE BASE'}}</span><h2>{{modal==='new-space'?'新建知识库':modal==='settings'?'空间设置':modal==='trash'?'知识库回收站':modal==='delete-space'?'删除知识库':modal==='detail'?(detail?.title || '文档详情'):modal==='edit'?'编辑文档':'新建文档'}}</h2></div><button class="icon-button" aria-label="关闭弹窗" :disabled="saving||memberSaving" @click="close"><X :size="21"/></button></header>
      <div v-if="modal==='settings'" class="kb-settings-tabs"><button :class="{active:tab==='general'}" @click="tab='general'">基本信息</button><button v-if="settingSpace?.kind==='shared'" :class="{active:tab==='members'}" @click="openMembers">成员管理</button></div>
      <form v-if="modal==='new-space'||(modal==='settings'&&tab==='general')" class="modal-body form-body" @submit.prevent="saveSpace">
        <p class="kb-privacy-note">{{modal==='new-space'?'自由设置名称和分类。访问范围独立控制，创建后由你负责管理。':settingSpace?.kind==='personal'?'此空间仅本人可见，不支持添加成员或转让。':'成员权限在「成员管理」中配置；负责人可转让给已加入且启用的成员。'}}</p>
        <p v-if="formError" role="alert" class="kb-error">{{formError}}</p>
        <div class="form-grid">
          <label class="field full"><span>知识库名称 *</span><input v-model.trim="spaceForm.name" required maxlength="120" placeholder="例如 客户交付资料、产品设计" :disabled="saving"/></label>
          <label class="field full"><span>知识库分类</span><input v-model.trim="spaceForm.category" list="kb-category-suggestions" maxlength="60" placeholder="输入新分类或选择已有分类" :disabled="saving"/><datalist id="kb-category-suggestions"><option v-for="category in categories" :key="category" :value="category"/></datalist><small class="field-help">分类可以按项目、产品或主题自定义，在空间设置中随时调整。</small></label>
          <template v-if="spaceForm.kind==='shared'">
            <fieldset class="kb-visibility kb-access-modes field full"><legend>开放范围</legend><label v-for="mode in accessModes" :key="mode.value" :class="{active:spaceForm.access_scope===mode.value}"><input type="radio" name="kb-access-scope" v-model="spaceForm.access_scope" :value="mode.value" :disabled="saving"/><span><strong>{{mode.label}}</strong><small>{{mode.hint}}</small></span></label></fieldset>
            <div class="field full kb-access-note">开放范围授予阅读权限，包含文档及历史版本。编辑权限请在成员管理中单独设置。</div>
            <div v-if="spaceForm.access_scope==='all'" class="field full kb-access-notice">保存后，平台内所有已登录用户均可阅读此空间，原有文档也会开放。</div>
            <div v-if="spaceForm.access_scope==='departments'" class="field full kb-access-selector">
              <strong>开放部门（可多选）</strong><input v-model="accessDepartmentSearch" aria-label="搜索开放部门" placeholder="搜索部门名称或层级" :disabled="saving"/>
              <div class="kb-access-chips"><button v-for="department in selectedDepartments" :key="department.id" type="button" :disabled="saving" :aria-label="'移除开放部门'+department.name" @click="toggleAccess('department_ids',department.id)">{{department.path || department.name}}<X :size="12"/></button><small v-if="!selectedDepartments.length">尚未选择部门</small></div>
              <div class="kb-access-options"><label v-for="department in accessDepartmentChoices" :key="department.id"><input type="checkbox" :checked="spaceForm.department_ids.includes(department.id)" :disabled="saving" @change="toggleAccess('department_ids',department.id)"/><span>{{department.path || department.name}}</span></label><p v-if="!accessLoading&&!accessDepartmentChoices.length">没有匹配的部门</p></div>
              <label class="kb-access-subdepartments"><input type="checkbox" v-model="spaceForm.include_subdepartments" :disabled="saving"/>包含下级部门</label><small>成员加入任意所选部门即可阅读；离开后重新校验权限。</small>
            </div>
            <div class="field full kb-access-selector">
              <strong>{{spaceForm.access_scope==='members'?'指定人员（可多选）':'额外授权人员（可选）'}}</strong><small>已授权人员在所有范围下保留权限；新选人员默认为只读。</small>
              <div class="kb-access-chips"><button v-for="person in selectedAccessPeople" :key="person.id" type="button" :disabled="saving||membersLoading" :aria-label="'移除授权人员'+person.display_name" @click="toggleAccess('member_ids',person.id)">{{person.display_name}}{{person.role==='editor'?' · 编辑':''}}<X :size="12"/></button><small v-if="!selectedAccessPeople.length">暂未指定其他人员，仅负责人保留管理权限</small></div>
              <input v-model="accessSearch" aria-label="搜索开放人员" placeholder="搜索姓名、用户名或部门" :disabled="saving"/>
              <div class="kb-access-options"><label v-for="person in accessOptions.people" :key="person.id"><input type="checkbox" :checked="spaceForm.member_ids.includes(person.id)" :disabled="saving||membersLoading" @change="toggleAccess('member_ids',person.id)"/><span>{{person.display_name}}（{{person.username}}）<small>{{person.department || '未分配部门'}}</small></span></label><p v-if="!accessLoading&&!accessOptions.people.length">没有匹配的人员</p></div><small>最多显示 50 位候选人员，可搜索查找；已选人员始终保留在上方。</small>
            </div>
            <p v-if="accessLoading" class="field full kb-hint" role="status">正在加载权限选项…</p><p v-if="accessError" class="field full kb-error" role="alert">{{accessError}}<button type="button" class="text-button" @click="loadAccessOptions">重试</button></p>
          </template>
          <label class="field full"><span>空间说明</span><textarea v-model.trim="spaceForm.description" rows="3" maxlength="300" :disabled="saving"/></label>
          <label v-if="modal==='settings'&&settingSpace?.kind==='shared'" class="field full"><span>空间负责人</span><select v-model.number="spaceForm.owner_id" :disabled="saving||membersLoading||Boolean(membersError)"><option :value="settingSpace.owner_id">{{settingSpace.owner_name}}（当前负责人）</option><option v-for="person in ownerChoices" :key="person.user_id" :value="person.user_id">{{person.display_name}}（{{person.username}}）</option></select><small class="field-help">转让后，你将保留编辑成员权限。</small><small v-if="membersError" class="kb-error">{{membersError}}<button type="button" class="text-button" @click="loadMembers">重试</button></small></label>
        </div>
        <div v-if="modal==='settings'&&settingSpace?.kind==='shared'" class="kb-danger-zone"><div><strong>删除知识库</strong><p>移入回收站后所有成员将失去访问权限，可由你恢复。</p></div><button type="button" class="danger-button" :disabled="saving" @click="confirmDelete"><Trash2 :size="15"/>删除知识库</button></div>
        <div class="form-footer"><button type="button" class="secondary-button" :disabled="saving" @click="close">取消</button><button class="primary-button" :disabled="saving">{{saving?'保存中…':modal==='new-space'?'创建知识库':'保存设置'}}</button></div>
      </form>
      <div v-else-if="modal==='settings'&&tab==='members'" class="modal-body">
        <div class="kb-member-policy"><Users :size="21"/><div><strong>由负责人管理访问范围</strong><p>只读成员可以查看文档及版本；编辑成员还可创建文档、编辑本人文档。负责人可以维护全部文档。</p></div></div>
        <form class="kb-invite" @submit.prevent="mutateMember('add')"><h3>添加成员</h3><label class="field"><span>搜索成员</span><input v-model="candidateSearch" placeholder="搜索姓名、用户名或部门" :disabled="memberSaving"/></label><p v-if="candidatesError" class="kb-error" role="alert">{{candidatesError}}<button type="button" class="text-button" @click="loadCandidates">重试</button></p><div class="kb-invite-controls"><select v-model.number="candidateId" aria-label="选择要添加的成员" required :disabled="memberSaving||candidatesLoading"><option :value="null" disabled>{{candidatesLoading?'正在加载成员…':candidates.length?'请选择成员':'没有可添加的匹配成员'}}</option><option v-for="person in candidates" :key="person.id" :value="person.id">{{person.display_name}}（{{person.username}}） · {{person.department}}</option></select><select v-model="candidateRole" aria-label="新成员权限" :disabled="memberSaving"><option value="viewer">只读</option><option value="editor">编辑</option></select><button class="primary-button" :disabled="memberSaving||!candidateId">添加成员</button></div><small>最多显示 50 位候选成员，可输入关键词缩小范围。</small></form>
        <p v-if="membersError" class="kb-error" role="alert">{{membersError}}<button class="text-button" @click="loadMembers">重试</button></p>
        <div class="kb-member-heading"><h3>空间成员 <span>{{members.length+1}}</span></h3><input v-model="memberFilter" aria-label="筛选已加入成员" placeholder="筛选已加入成员"/></div>
        <div class="kb-member-row kb-owner-row"><div><strong>{{settingSpace?.owner_name}}</strong><small>空间负责人</small></div><span class="kb-owner-badge">负责人</span></div>
        <p v-if="membersLoading" role="status" class="kb-hint">正在加载空间成员…</p>
        <div v-for="person in filteredMembers" :key="person.user_id" class="kb-member-row"><div><strong>{{person.display_name}}<span v-if="!person.active" class="kb-disabled">已停用</span></strong><small>{{person.username}} · {{person.department}}</small></div><select :value="person.role" :aria-label="person.display_name+'的权限'" :disabled="memberSaving" @change="mutateMember('role',person,$event.target.value)"><option value="viewer">只读</option><option value="editor">编辑</option></select><button class="text-button kb-danger" :disabled="memberSaving" @click="removeTarget=person">移除</button></div>
        <p v-if="!membersLoading&&!filteredMembers.length" class="kb-hint">{{memberFilter?'没有匹配的成员':'暂未添加其他成员。'}}</p>
        <div v-if="removeTarget" class="kb-remove-confirm" role="alert"><span>将移除 {{removeTarget.display_name}} 的直接授权；若仍属于全员或部门开放范围，该成员仍可阅读。已有文档保留。</span><button class="secondary-button" :disabled="memberSaving" @click="removeTarget=null">取消</button><button class="primary-button" :disabled="memberSaving" @click="mutateMember('remove',removeTarget)">确认移除</button></div>
      </div>
      <form v-else-if="modal==='create'||modal==='edit'" class="modal-body form-body" @submit.prevent="saveArticle">
        <div class="kb-save-state" :class="{warning:editor.error.value||editor.fieldError.value||editor.localError.value}" role="status" aria-live="polite"><span class="kb-save-indicator" :class="{syncing:editor.syncing.value,warning:editor.error.value||editor.fieldError.value||editor.localError.value}"></span><div><strong>{{editor.status.value}}</strong><small>{{modal==='create'?'新文档先保留本地草稿，点击“保存文档”后创建。':'停止输入约 1 秒后自动保存内容，不增加版本；点击“保存文档”生成一个新版本。'}}<template v-if="editor.lastSaved.value"> · {{new Date(editor.lastSaved.value).toLocaleTimeString('zh-CN')}}</template></small></div><button v-if="editor.error.value&&!editor.conflict.value&&modal==='edit'" type="button" class="text-button" :disabled="saving||editor.syncing.value" @click="editor.retry">重试同步</button><button v-if="editor.dirty.value" type="button" class="text-button" @click="downloadDraft">下载草稿</button></div>
        <p v-if="editor.restored.value" class="kb-draft-restored">已恢复上次未同步的本地草稿。</p>
        <p v-if="formError||editor.error.value||editor.fieldError.value||editor.localError.value" class="kb-error" role="alert">{{formError||editor.localError.value||editor.fieldError.value||editor.error.value}}<button v-if="editor.conflict.value" type="button" class="text-button" :disabled="saving" @click="reloadEditor">读取最新版本</button></p><div class="form-grid"><label class="field full"><span id="kb-title-label">文档标题 *</span><input aria-labelledby="kb-title-label" v-model="articleForm.title" :disabled="saving" :maxlength="WIKI_TITLE_LIMIT" @input="limitArticleField('title',WIKI_TITLE_LIMIT,$event)" @compositionend="limitArticleField('title',WIKI_TITLE_LIMIT,$event)" aria-describedby="kb-title-limit" required/><small id="kb-title-limit" class="kb-field-counter" :class="{limit:wikiTextLength(articleForm.title)>=WIKI_TITLE_LIMIT}"><span>最多 {{WIKI_TITLE_LIMIT}} 个字符</span><span>{{wikiTextLength(articleForm.title)}} / {{WIKI_TITLE_LIMIT}}</span></small></label><label class="field"><span>所属空间 *</span><select v-model.number="articleForm.space_id" :disabled="modal==='edit'||saving" required><option v-for="space in writableSpaces" :key="space.id" :value="space.id">{{space.name}}</option></select><button v-if="modal==='create'" class="text-button" type="button" :disabled="saving" @click="newSpace">＋ 新建知识库</button><small v-else class="field-help">文档所属空间不可更换。</small></label><label class="field"><span>文档状态</span><select v-model="articleForm.status" :disabled="saving"><option value="draft">草稿</option><option value="published">已发布</option><option value="archived">已归档</option></select></label><label class="field full"><span id="kb-category-label">内容分类 *</span><input aria-labelledby="kb-category-label" v-model="articleForm.category" :disabled="saving" list="kb-article-categories" required :maxlength="WIKI_CATEGORY_LIMIT" @input="limitArticleField('category',WIKI_CATEGORY_LIMIT,$event)" @compositionend="limitArticleField('category',WIKI_CATEGORY_LIMIT,$event)" aria-describedby="kb-category-limit" placeholder="例如 技术方案，可自由填写"/><small id="kb-category-limit" class="kb-field-counter" :class="{limit:wikiTextLength(articleForm.category)>=WIKI_CATEGORY_LIMIT}"><span>最多 {{WIKI_CATEGORY_LIMIT}} 个字符</span><span>{{wikiTextLength(articleForm.category)}} / {{WIKI_CATEGORY_LIMIT}}</span></small><datalist id="kb-article-categories"><option v-for="category in articleCategories" :key="category" :value="category"/></datalist></label><div class="field full kb-body-field"><div class="kb-body-heading"><label id="kb-body-label" for="kb-body-input">正文内容 *</label><div class="kb-body-modes" role="group" aria-label="正文显示方式"><button type="button" :aria-pressed="bodyMode==='edit'" :class="{active:bodyMode==='edit'}" @click="bodyMode='edit'">编辑</button><button type="button" :aria-pressed="bodyMode==='preview'" :class="{active:bodyMode==='preview'}" @click="bodyMode='preview'">预览</button></div></div><textarea v-show="bodyMode==='edit'" id="kb-body-input" v-model="articleForm.content" :disabled="saving" :required="bodyMode==='edit'" rows="12" aria-describedby="kb-markdown-help" placeholder="使用 Markdown 编写正文，可切换预览检查排版。"/><div v-if="bodyMode==='preview'" class="kb-body-preview" role="region" aria-label="正文预览"><MarkdownContent v-if="articleForm.content" :content="articleForm.content"/><p v-else class="kb-preview-empty">输入正文后，在这里预览排版。</p></div><small id="kb-markdown-help" class="field-help">支持标题、加粗、列表、表格、引用、链接和代码块。切换预览不影响自动保存。</small></div></div><div class="form-footer"><button class="secondary-button" type="button" :disabled="saving" @click="close">关闭</button><button class="primary-button" :disabled="saving||editor.conflict.value||Boolean(editor.fieldError.value)">{{saving?'保存中…':'保存文档'}}</button></div>
      </form>
      <form v-else-if="modal==='delete-space'" class="modal-body" @submit.prevent="deleteSpace"><div class="kb-delete-warning"><Trash2 :size="26"/><h3>将「{{spaceForm.name}}」移入回收站？</h3><p>库内文档、历史版本和成员关系会保留，但所有成员将无法访问。只有你可以恢复此知识库。</p></div><p v-if="formError" class="kb-error" role="alert">{{formError}}</p><label class="field"><span>输入知识库名称确认删除</span><input v-model="deleteName" required autocomplete="off" :placeholder="spaceForm.name" :disabled="saving"/></label><div class="form-footer"><button type="button" class="secondary-button" :disabled="saving" @click="modal='settings'">取消</button><button class="danger-button" :disabled="saving||deleteName!==spaceForm.name">{{saving?'删除中…':'确认删除'}}</button></div></form>
      <div v-else-if="modal==='trash'" class="modal-body"><p class="kb-privacy-note">仅显示由你负责的已删除知识库。恢复后，原有文档、版本和成员权限同时恢复。</p><p v-if="trashError" class="kb-error" role="alert">{{trashError}}<button class="text-button" @click="openTrash">重试</button></p><p v-if="trashLoading" class="kb-hint" role="status">正在加载回收站…</p><div v-for="space in trash" :key="space.id" class="kb-trash-row"><div><strong>{{space.name}}</strong><small>{{space.category || '未分类'}} · 删除于 {{shortDate(space.deleted_at)}}</small></div><button class="secondary-button" :disabled="saving" :aria-label="'恢复'+space.name" @click="restoreSpace(space)"><RotateCcw :size="15"/>恢复</button></div><div v-if="!trashLoading&&!trash.length&&!trashError" class="kb-empty"><Trash2 :size="28"/><strong>回收站为空</strong><span>已删除的知识库会保留在这里。</span></div></div>
      <div v-else-if="modal==='detail'" class="modal-body article-body"><p v-if="detailLoading" role="status">正在验证权限并读取文档…</p><p v-if="detailError" class="kb-error" role="alert">{{detailError}}</p><template v-if="detail"><div class="article-meta"><span class="wiki-category">{{detail.category}}</span><span>{{labels[detail.status]}}</span><span>{{detail.author_name}} · v{{detail.version}} · {{shortDate(detail.updated_at)}}</span></div><MarkdownContent class="article-content" :content="detail.content"/><div v-if="versions.length" class="kb-history"><h3>历史版本</h3><details v-for="version in versions" :key="version.id"><summary>v{{version.version}} · {{version.author_name}} · {{shortDate(version.created_at)}} · {{labels[version.status]}}</summary><h4>{{version.title}}</h4><MarkdownContent :content="version.content"/></details></div><div class="form-footer"><button v-if="detail.can_edit" class="secondary-button" :disabled="detailLoading" @click="openArticle(detail,true)">编辑文档</button><button class="primary-button" @click="close">关闭</button></div></template></div>
    </section></div>
  </section>
</template>

<style scoped>
.kb-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:22px}.kb-heading h1{font-size:22px;margin:0 0 8px}.kb-heading p{color:#8491a6;font-size:12px;margin:0}.kb-scope-tabs{display:flex;gap:8px;border-bottom:1px solid #e4e9f1;margin-bottom:22px}.kb-scope-tabs button{display:flex;align-items:center;gap:8px;border:0;border-bottom:3px solid transparent;padding:14px 18px;background:transparent;color:#718198;font-weight:600}.kb-scope-tabs button.active{color:#4169df;border-bottom-color:#4169df}.kb-scope-tabs span{font-size:10px;font-weight:400;color:#8797ac}.kb-layout{display:grid;grid-template-columns:224px minmax(0,1fr);gap:22px}.kb-spaces{background:#fff;border:1px solid #e7ebf3;padding:14px;border-radius:12px;align-self:start}.kb-spaces-heading{font-size:12px;color:#52647d;padding:6px 6px 13px}.kb-space{display:flex;align-items:center;border-radius:8px;margin:4px 0}.kb-space.selected{background:#eef2ff}.kb-space-select{flex:1;min-width:0;padding:13px 10px;border:0;background:transparent;text-align:left;color:#4d607a}.kb-space-select strong,.kb-space-select small{display:block;overflow-wrap:anywhere}.kb-space-select strong{font-size:12px}.kb-space-select small{font-size:10px;color:#8998ad;margin-top:7px;line-height:1.6}.kb-space .icon-button{min-width:28px;width:28px;margin-right:5px}.kb-main{min-width:0}.kb-space-banner{display:flex;justify-content:space-between;gap:16px;align-items:center;border:1px solid #e2e9fa;background:linear-gradient(120deg,#edf2ff,#f7f9ff);padding:21px 24px;border-radius:12px;color:#6b86d0}.kb-eyebrow{font-size:10px;letter-spacing:2px;font-weight:700}.kb-space-banner h3{color:#2c4266;font-size:18px;margin:9px 0}.kb-space-banner p{font-size:12px;color:#7d8da4;margin:0;line-height:1.7;white-space:pre-wrap;overflow-wrap:anywhere}.kb-space-banner>svg{flex-shrink:0;opacity:.5}.kb-toolbar{display:flex;align-items:center;gap:14px;margin:22px 0 17px}.kb-toolbar .search-box{flex:1;max-width:360px;min-width:180px}.kb-toolbar-right{margin-left:auto;display:flex;align-items:center;justify-content:flex-end;gap:13px}.kb-toolbar-right>span{font-size:11px;color:#8a99ad;white-space:nowrap}.kb-toolbar select,.kb-invite select,.kb-member-row select{border:1px solid #e2e8f2;border-radius:7px;background:white;padding:8px 10px;color:#637691;font-size:12px}.kb-toolbar .primary-button{white-space:nowrap;font-size:12px}.kb-empty{min-height:260px;border:1px dashed #d9e1ee;border-radius:12px;background:#ffffffaa;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:13px;color:#8b9ab0;font-size:12px;padding:24px;text-align:center}.kb-empty strong{font-size:14px;color:#526781}.kb-empty>svg{color:#7b96de}.kb-hint,.kb-readonly,.kb-privacy-note{font-size:12px;color:#7c8da5;line-height:1.8}.kb-hint{padding:12px}.kb-error{color:#b04b58;border:1px solid #f1d4da;background:#fff6f7;border-radius:8px;padding:10px 13px;font-size:12px;line-height:1.8;display:flex;gap:8px;flex-wrap:wrap;align-items:center}.kb-article-card{width:100%;color:inherit}.kb-modal{max-width:700px}.kb-modal.kb-wide{max-width:860px}.kb-modal input,.kb-modal textarea{font-size:13px}.kb-settings-tabs{padding:0 26px;display:flex;gap:23px;border-bottom:1px solid #e7ecf5}.kb-settings-tabs button{border:0;border-bottom:2px solid transparent;background:none;padding:13px 4px;color:#7c8ba0}.kb-settings-tabs button.active{border-color:#4970e7;color:#4970e7}.kb-member-policy{display:flex;gap:11px;background:#f2f5ff;border:1px solid #e4eafb;padding:15px;border-radius:9px;color:#4d69a1}.kb-member-policy svg{flex-shrink:0}.kb-member-policy strong{font-size:13px}.kb-member-policy p{font-size:12px;color:#7a8da8;margin:6px 0 0;line-height:1.8}.kb-invite{border:1px solid #e4e9f2;padding:17px;border-radius:10px;margin:20px 0}.kb-invite h3,.kb-member-heading h3,.kb-history h3{font-size:13px;margin:0 0 14px;color:#455b79}.kb-invite-controls{display:flex;gap:9px;margin:10px 0}.kb-invite-controls>select:first-child{flex:1;min-width:0}.kb-invite small{color:#97a3b4;font-size:11px}.kb-member-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:24px 0 12px}.kb-member-heading h3{margin:0}.kb-member-heading span{font-weight:400;color:#8d9bad;margin-left:5px}.kb-member-heading input{padding:8px 10px;border:1px solid #e1e8f1;border-radius:7px;max-width:180px}.kb-member-row{display:flex;align-items:center;gap:12px;padding:14px 0;border-bottom:1px solid #edf0f6}.kb-member-row>div{flex:1;min-width:0}.kb-member-row strong{font-size:13px;color:#465b77}.kb-member-row small{display:block;font-size:11px;color:#8a99ad;margin-top:5px;overflow-wrap:anywhere}.kb-owner-badge{background:#eef2ff;color:#526ed3;font-size:11px;border-radius:5px;padding:6px 10px}.kb-disabled{font-size:10px;color:#a29094;margin-left:8px}.kb-danger{color:#be5c6b}.kb-remove-confirm{padding:14px;background:#fff5f4;border:1px solid #f1d4d0;display:flex;gap:10px;flex-wrap:wrap;font-size:12px;line-height:1.8;border-radius:9px;margin-top:16px}.kb-remove-confirm>span{width:100%;color:#a85853}.kb-history{margin-top:24px;border-top:1px solid #e9edf5;padding-top:18px}.kb-history details{border:1px solid #e7ebf4;border-radius:8px;margin:8px 0;padding:12px;font-size:12px;color:#74859e}.kb-history summary{cursor:pointer}.kb-history h4{color:#415571}.kb-readonly{background:#f5f7fc;border-radius:7px;padding:10px 13px}.wiki-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
.kb-editor{width:min(860px,100%)}.kb-save-state{position:sticky;top:-22px;z-index:2;box-shadow:0 -5px 0 5px #fff;display:flex;align-items:center;gap:10px;padding:13px 15px;margin-bottom:16px;border:1px solid #dce9e3;background:#f5faf7;border-radius:9px;color:#3d6c57}.kb-save-state>div{flex:1;min-width:0}.kb-save-state strong{display:block;font-size:12px;font-weight:600}.kb-save-state small{display:block;font-size:11px;color:#7c8f85;margin-top:5px;line-height:1.7}.kb-save-state.warning{background:#fff9ef;border-color:#f0dfbf;color:#926a2b}.kb-save-indicator{width:7px;height:7px;flex-shrink:0;border-radius:50%;background:#57a47d}.kb-save-indicator.syncing{background:#4b6ff1;animation:kb-saving 1s ease-in-out infinite}.kb-save-indicator.warning{background:#ce994b}.kb-draft-restored{font-size:12px;color:#5879bd;background:#f2f6ff;border-radius:7px;padding:10px 13px;line-height:1.7}@keyframes kb-saving{50%{opacity:.3}}@media(prefers-reduced-motion:reduce){.kb-save-indicator.syncing{animation:none}}@media(max-width:700px){.kb-save-state{top:-20px;flex-wrap:wrap}.kb-save-state>div{flex-basis:calc(100% - 20px)}.kb-save-state .text-button{margin-left:17px}}
@media(min-width:1500px){.wiki-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:900px){.kb-layout{grid-template-columns:180px minmax(0,1fr)}.kb-toolbar{flex-wrap:wrap}.kb-toolbar .search-box{max-width:none}.kb-toolbar-right{flex-wrap:wrap}.wiki-grid{grid-template-columns:1fr}}@media(max-width:700px){.kb-heading{align-items:flex-start;flex-direction:column}.kb-scope-tabs button{padding:12px 9px;font-size:12px;flex-wrap:wrap}.kb-scope-tabs span{display:none}.kb-layout{grid-template-columns:1fr}.kb-space-banner{padding:18px}.kb-toolbar .search-box{flex-basis:100%}.kb-toolbar-right{width:100%;margin-left:auto;gap:10px}.kb-toolbar-right>span{font-size:10px}.kb-toolbar-right .primary-button{padding:9px 10px}.kb-invite-controls{flex-wrap:wrap}.kb-invite-controls>select:first-child{flex-basis:100%}.kb-member-heading{align-items:flex-start;flex-direction:column}.kb-member-row{gap:8px}.kb-member-row select{padding:6px}.kb-modal .modal-head{padding:20px}.kb-modal .modal-body{padding:20px}.kb-space-select strong{font-size:13px}}

/* Bound each card independently so long words cannot paint over its neighbours. */
.kb-article-card{min-width:0;max-width:100%;overflow:hidden;display:flex;flex-direction:column;align-items:stretch;text-align:left}
.kb-article-card h3{min-width:0;max-width:100%;line-height:1.55;min-height:3.1em;overflow-wrap:anywhere;word-break:break-word;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}
.kb-article-card>p{min-width:0;max-width:100%;overflow-wrap:anywhere;word-break:break-word}
.kb-article-card .wiki-card-top{min-width:0;gap:10px}
.kb-article-card .wiki-card-top>span:last-child{flex-shrink:0}
.kb-article-card .wiki-category{min-width:0;max-width:calc(100% - 60px);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.kb-article-card .wiki-card-bottom{min-width:0;margin-top:auto;padding-top:17px}
.kb-article-card .wiki-card-bottom>span:first-child{flex:1;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.kb-article-card .wiki-date,.kb-article-card .wiki-card-bottom>svg{flex-shrink:0}
.kb-modal .modal-head>div{flex:1;min-width:0}
.kb-modal .modal-head h2,.kb-modal .article-content,.kb-history h4,.kb-history summary{overflow-wrap:anywhere;word-break:break-word}
.kb-modal .modal-head>.icon-button{flex-shrink:0}
.kb-modal .article-meta{flex-wrap:wrap;min-width:0}
.kb-modal .article-meta>span{min-width:0;overflow-wrap:anywhere}
.kb-field-counter{display:flex;justify-content:space-between;gap:12px;color:#8a98ac;font-size:11px;line-height:1.5}
.kb-field-counter>span{font-size:inherit;font-weight:400;color:inherit}
.kb-field-counter.limit{color:#b17d35}
.kb-body-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}
.kb-body-heading>label{font-size:11px;font-weight:700;color:#56647a}
.kb-body-modes{display:flex;padding:3px;border:1px solid #e1e7f1;background:#f5f7fc;border-radius:8px;gap:3px}
.kb-body-modes button{padding:5px 13px;border:0;border-radius:5px;background:transparent;color:#7d8ca3;font-size:12px;cursor:pointer}
.kb-body-modes button.active{background:#fff;color:#4267c5;box-shadow:0 1px 4px #20345814}
.kb-body-modes button:focus-visible{outline:2px solid #829bdd;outline-offset:1px}
.kb-body-field>textarea{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px;line-height:1.8}
.kb-body-preview{min-height:300px;max-width:100%;padding:18px;border:1px solid #e2e7ef;border-radius:8px;background:#fff}
.kb-preview-empty{font-size:13px;color:#94a1b5;margin:0}
.kb-history .markdown-content{margin-top:14px}
.kb-modal .article-content{white-space:normal;word-break:normal}
</style>
