<script setup>
import { computed, ref, onMounted } from 'vue'
import { Search, X } from 'lucide-vue-next'
import { get, post, put } from './api'
const props=defineProps({user:Object})
const emit=defineEmits(['notice'])
const projects=ref([]), people=ref([]), roles=ref([]), editing=ref(null), busy=ref(false)
const loadError=ref('')
const loaded=ref(false), search=ref(''), departmentFilter=ref(''), statusFilter=ref('all')
const projectCounts=computed(()=>({
  total:projects.value.length,
  active:projects.value.filter(p=>p.active).length,
  inactive:projects.value.filter(p=>!p.active).length,
}))
const departments=computed(()=>[...new Set(projects.value.map(p=>p.department).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh-CN')))
const hasFilters=computed(()=>Boolean(search.value.trim() || departmentFilter.value || statusFilter.value!=='all'))
const filteredProjects=computed(()=>{
  const keyword=search.value.trim().toLocaleLowerCase()
  return projects.value.filter(p=>{
    if(departmentFilter.value && p.department!==departmentFilter.value) return false
    if(statusFilter.value==='active' && !p.active) return false
    if(statusFilter.value==='inactive' && p.active) return false
    return !keyword || [p.name,p.department,...p.members.map(m=>m.name)].filter(Boolean).join(' ').toLocaleLowerCase().includes(keyword)
  })
})
function resetFilters(){search.value='';departmentFilter.value='';statusFilter.value='all'}
const isAdmin=()=>props.user.roles.includes('admin')
const roleAlias={leader:'manager',project_owner:'project_manager',project_director:'project_manager_head',sales_head:'commercial_head',sales_director:'commercial_head',development_head:'technical_head'}
const effectiveRoles=(person)=>person.canonical_roles || (person.roles||[]).map(r=>roleAlias[r]||r)
const canEditRole=(role)=>isAdmin() || Boolean(editing.value?.manageable_roles?.includes(role))
async function act(fn){busy.value=true;try{await fn()}catch(e){emit('notice',e.message)}finally{busy.value=false}}
async function load(){
  loadError.value=''
  try {[projects.value,roles.value]=await Promise.all([get('/projects'),get('/roles')]);loaded.value=true}
  catch(e){loadError.value='项目加载失败：'+e.message;throw e}
}
async function edit(p){await act(async()=>{
  // The member directory is only needed when editing, never on a page refresh.
  people.value=await get('/users')
  editing.value=p?JSON.parse(JSON.stringify(p)):{name:'',department:'',active:true,members:[]}
})}
function candidates(role){return people.value.filter(p=>p.active&&effectiveRoles(p).includes(role)&& (isAdmin() || !roles.value.some(r=>effectiveRoles(props.user).includes(r.id)&&r.managed_roles?.includes(role)) || p.department===props.user.department))}
async function save(){await act(async()=>{const p=editing.value; if(!p.id) await post('/projects',p); else if(isAdmin()) await put(`/projects/${p.id}`,p); else await put(`/projects/${p.id}/members`,{members:p.members}); editing.value=null;await load();emit('notice','项目及岗位成员已保存')})}
onMounted(()=>act(load))
</script>
<template>
  <section class="panel settings-card project-management">
    <div class="flow-toolbar project-heading"><div><h3>项目与岗位成员</h3><p class="field-help">先授予成员角色，再分配项目岗位。项目经理维护项目成员；本部门负责人仅维护各自负责的岗位。</p></div><button v-if="isAdmin()" class="primary-button" :disabled="busy" @click="edit()">＋ 创建项目</button></div>
    <div class="project-stats" aria-label="项目统计" :aria-busy="!loaded || Boolean(loadError)">
      <div class="project-stat total"><span>项目总数</span><strong>{{loaded && !loadError ? projectCounts.total : '—'}}</strong><small>{{isAdmin()?'全部项目':'我可查看的项目'}}</small></div>
      <div class="project-stat"><span><i class="active-dot"></i>启用项目</span><strong>{{loaded && !loadError ? projectCounts.active : '—'}}</strong><small>可用于提交业务申请</small></div>
      <div class="project-stat"><span><i class="inactive-dot"></i>停用项目</span><strong>{{loaded && !loadError ? projectCounts.inactive : '—'}}</strong><small>保留项目与岗位记录</small></div>
    </div>
    <div class="project-filters" role="search" aria-label="筛选项目">
      <label class="project-search"><span class="project-filter-label">关键词</span><div class="search-box"><Search :size="16" /><input v-model="search" type="search" placeholder="搜索项目名称、部门或成员" aria-label="搜索项目名称、部门或成员" /><button v-if="search" type="button" class="project-search-clear" aria-label="清除项目搜索" @click="search=''"><X :size="14" /></button></div></label>
      <label class="project-filter"><span class="project-filter-label">所属部门</span><select v-model="departmentFilter"><option value="">全部部门</option><option v-for="department in departments" :key="department" :value="department">{{department}}</option></select></label>
      <label class="project-filter"><span class="project-filter-label">项目状态</span><select v-model="statusFilter"><option value="all">全部状态</option><option value="active">启用</option><option value="inactive">停用</option></select></label>
      <button type="button" class="project-filter-reset text-button" :disabled="!hasFilters" @click="resetFilters">重置筛选</button>
    </div>
    <p v-if="busy && !editing" class="empty-mini" role="status">正在加载项目数据…</p>
    <p v-if="loadError" class="inline-error" role="alert">{{loadError}}<button class="text-button" :disabled="busy" @click="act(load)">重试</button></p>
    <template v-else-if="loaded">
      <p class="project-result-count" role="status">共 <strong>{{projectCounts.total}}</strong> 个项目<span v-if="hasFilters">，筛选结果 <strong>{{filteredProjects.length}}</strong> 个</span></p>
      <div v-if="filteredProjects.length" class="project-cards"><article v-for="p in filteredProjects" :key="p.id" class="project-card"><div class="flow-toolbar"><strong>{{ p.name }}</strong><span class="status-tag" :class="p.active?'green':'gray'">{{p.active?'启用':'停用'}}</span></div><p class="muted">{{p.department}}</p><div v-for="r in roles.filter(r=>p.members.some(m=>m.role===r.id))" :key="r.id" class="project-member-line"><span>{{r.name}}</span><b>{{p.members.filter(m=>m.role===r.id).map(m=>m.name+(m.eligible?'':'（需调整）')).join('、')}}</b></div><button v-if="p.can_manage" class="secondary-button" :disabled="busy" @click="edit(p)">维护项目</button></article></div>
      <div v-else-if="projects.length" class="project-empty"><Search :size="24" /><strong>没有符合条件的项目</strong><p>调整关键词、所属部门或项目状态后再试。</p><button type="button" class="secondary-button" @click="resetFilters">清空筛选</button></div>
      <p v-else-if="!busy" class="empty-mini">{{isAdmin()?'暂无项目，点击「创建项目」开始添加。':'暂无参与的项目，请联系管理员创建并分配岗位。'}}</p>
    </template>
  </section>
  <div v-if="editing" class="modal-backdrop" @click.self="editing=null"><form class="modal-panel" @submit.prevent="save"><div class="modal-head"><h3>{{editing.id?'维护项目':'创建项目'}}</h3><button type="button" class="text-button" @click="editing=null">关闭</button></div><div class="modal-body"><div class="form-grid"><label class="field"><span>项目名称</span><input v-model="editing.name" required :disabled="!isAdmin()" /></label><label class="field"><span>所属部门</span><input v-model="editing.department" required :disabled="!isAdmin()" /></label><label v-if="isAdmin()" class="field"><span>项目状态</span><select v-model="editing.active"><option :value="true">启用</option><option :value="false">停用</option></select></label></div>
    <div v-for="m in editing.members.filter(m=>!people.some(p=>p.id===m.user_id && p.active && effectiveRoles(p).includes(roleAlias[m.role]||m.role)))" :key="m.user_id+'-'+m.role" class="settings-note"><span>岗位失效：{{people.find(p=>p.id===m.user_id)?.display_name || m.user_id}} / {{roles.find(r=>r.id===(roleAlias[m.role]||m.role))?.name || m.role}}</span><button type="button" class="text-button danger" :disabled="!canEditRole(roleAlias[m.role]||m.role)" @click="editing.members=editing.members.filter(x=>x!==m)">移除失效岗位</button></div>
    <div v-for="r in roles.filter(r=>r.project_member_role)" :key="r.id" class="project-role-select"><label class="field"><span>{{r.name}} {{r.id==='project_manager'?'（项目必须至少一名）':''}}</span></label><div class="role-options"><label v-for="p in candidates(r.id)" :key="p.id"><input type="checkbox" :checked="editing.members.some(m=>m.user_id===p.id&&((roleAlias[m.role]||m.role)===r.id))" :disabled="!canEditRole(r.id)" @change="$event.target.checked?editing.members.push({user_id:p.id,role:r.id}):editing.members=editing.members.filter(m=>!(m.user_id===p.id&&((roleAlias[m.role]||m.role)===r.id)))" />{{p.display_name}}</label><small v-if="!candidates(r.id).length" class="muted">暂无获授此角色的成员</small></div></div>
    <div class="form-footer"><button class="primary-button" :disabled="busy">保存项目</button></div></div></form></div>
</template>
<style scoped>
.project-heading{justify-content:space-between;align-items:flex-start}.project-heading h3{margin:0 0 8px;font-size:15px}.project-heading p{margin:0;max-width:600px}.project-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:23px 0}.project-stat{padding:18px 20px;border:1px solid #e6ebf3;border-radius:10px;background:#fcfdff}.project-stat.total{background:#f2f5ff;border-color:#e0e7fc}.project-stat>span{display:flex;align-items:center;gap:6px;font-size:11px;color:#7d8da6}.project-stat strong{display:block;font-size:27px;line-height:1.3;font-weight:650;letter-spacing:-.7px;color:#35465f;margin:9px 0 5px}.project-stat.total strong{color:#526fd3}.project-stat small{display:block;font-size:10px;line-height:1.6;color:#99a6ba}.project-stat i{width:6px;height:6px;flex:0 0 6px;border-radius:50%}.active-dot{background:#61ad8a}.inactive-dot{background:#a9b3c5}.project-filters{display:flex;gap:14px;align-items:flex-end;flex-wrap:wrap;background:#fafbfd;border:1px solid #eaf0f6;border-radius:10px;padding:16px}.project-filter-label{display:block;font-size:10px;color:#7b8ba4;font-weight:600;margin-bottom:8px}.project-search{flex:1;min-width:230px}.project-search .search-box{min-width:0;height:36px;background:white}.project-search input{min-width:0}.project-search input::-webkit-search-cancel-button{-webkit-appearance:none}.project-search svg{flex-shrink:0}.project-search-clear{display:grid;place-items:center;padding:3px;border:0;background:transparent;color:#95a3ba;cursor:pointer}.project-filter{min-width:140px;flex:0 1 180px}.project-filter select{width:100%;height:36px;min-width:0;padding:0 10px;border:1px solid #e2e8f2;border-radius:8px;background:white;color:#556780;font-size:11px}.project-filter-reset{height:36px;font-size:11px;white-space:nowrap}.project-filter-reset:disabled{color:#aab5c5;cursor:default}.project-result-count{font-size:11px;color:#8d9cb0;margin:21px 0 0}.project-result-count strong{color:#546783;font-weight:600}.project-cards{margin-top:14px;grid-template-columns:repeat(auto-fill,minmax(min(100%,300px),1fr))}.project-card{min-width:0}.project-card .flow-toolbar{justify-content:space-between;align-items:flex-start}.project-card .flow-toolbar>strong{overflow-wrap:anywhere;flex:1;min-width:0}.project-member-line b{overflow-wrap:anywhere;min-width:0}.project-empty{display:flex;align-items:center;flex-direction:column;text-align:center;padding:44px 18px;color:#9aabc2;gap:12px}.project-empty strong{font-size:13px;color:#687d9b}.project-empty p{font-size:11px;line-height:1.7;margin:0 0 4px}.project-search .search-box:focus-within{border-color:#9fb3f0;box-shadow:0 0 0 3px #5678e714}.project-filter select:focus-visible,.project-search-clear:focus-visible{outline:2px solid #8fa8ef;outline-offset:2px}@media(max-width:600px){.project-stats{gap:8px}.project-stat{padding:14px 10px}.project-stat strong{font-size:23px}.project-stat small{font-size:9px}.project-filters{gap:12px}.project-search{flex-basis:100%;min-width:0}.project-filter{flex:1;min-width:0}.project-filter-reset{flex-basis:100%;height:28px;text-align:right}.project-heading .primary-button{margin-left:auto}}
</style>
