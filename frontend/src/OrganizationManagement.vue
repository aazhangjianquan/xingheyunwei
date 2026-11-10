<script setup>
import { ref, computed, onMounted } from 'vue'
import { get, post, patch, put, del } from './api'
import MemberOrganization from './MemberOrganization.vue'
const props = defineProps({ user:Object, roles:Array })
const emit = defineEmits(['notice','changed','create-member'])
const directory = ref([]), selectedId = ref(null), tab = ref('members'), loading = ref(false), error = ref('')
const includeDescendants = ref(true)
const members = ref([]), memberPage = ref(1), memberTotal = ref(0), memberPages = ref(1), editedMember = ref(null)
const deletionImpact = ref(null), deletionLoading = ref(false)
const deleting = computed(() => editor.value==='delete-department' || editor.value==='delete-position')
const deletionLabel = computed(() => editor.value==='delete-department'?'部门':'岗位')
const editor = ref(''), draft = ref({}), saving = ref(false), saveError = ref(''), managerPeople = ref([])
const existingPeople = ref([]), existingId = ref(null), memberSearch = ref(''), transferImpact = ref(null), impactLoading = ref(false)
const isAdmin = computed(() => (props.user.canonical_roles || props.user.roles).includes('admin'))
const selected = computed(() => directory.value.find(d=>d.id===selectedId.value))
const templateRoles = computed(() => props.roles.filter(r=>!['admin','license_manager','knowledge_admin','department_head'].includes(r.id)))
const existingMember = computed(() => existingPeople.value.find(p=>p.id===existingId.value))
const candidateMembers = computed(() => {
  const query = memberSearch.value.trim().toLocaleLowerCase()
  return existingPeople.value.filter(p=>!query || [p.display_name,p.username,p.department_path,p.department,...(p.departments || []).map(d=>d.path)].join(' ').toLocaleLowerCase().includes(query))
})
const tree = computed(() => {
  const output=[], seen=new Set()
  function walk(parent,depth){for(const d of directory.value.filter(d=>d.parent_id===parent)){if(seen.has(d.id))continue;seen.add(d.id);output.push({...d,depth});walk(d.id,depth+1)}}
  walk(null,0)
  for(const d of directory.value) if(!seen.has(d.id)) output.push({...d,depth:0})
  return output
})
let memberRequest = 0
async function loadMembers() {
  const request = ++memberRequest
  if(!selected.value){members.value=[];memberTotal.value=0;return}
  const query=new URLSearchParams({page:String(memberPage.value),page_size:'20',department_id:String(selectedId.value),include_descendants:String(isAdmin.value && includeDescendants.value)})
  const result=await get(`/users?${query}`)
  if(request!==memberRequest)return
  members.value=result.items;memberTotal.value=result.total;memberPages.value=result.total_pages;memberPage.value=result.page
}
async function reload() {
  loading.value=true;error.value=''
  try{directory.value=await get('/departments');if(!selected.value)selectedId.value=directory.value[0]?.id || null;await loadMembers()}
  catch(e){error.value=e.message}finally{loading.value=false}
}
async function choose(d){selectedId.value=d.id;memberPage.value=1;loading.value=true;error.value='';try{await loadMembers()}catch(e){error.value=e.message}finally{loading.value=false}}
async function pageMembers(delta){memberPage.value+=delta;await choosePage()}
async function choosePage(){loading.value=true;try{await loadMembers()}catch(e){error.value=e.message}finally{loading.value=false}}
function editDepartment(d=null){saveError.value='';draft.value=d?{id:d.id,name:d.name,parent_id:d.parent_id,active:d.active,sort_order:d.sort_order,description:d.description,revision:d.revision}:{name:'',parent_id:null,active:true,sort_order:0,description:''};editor.value='department'}
async function openPositionSource(p){const source=directory.value.find(d=>d.id===p.source_department_id);if(source){await choose(source);tab.value='positions'}}
function editPosition(p=null){saveError.value='';draft.value=p?{id:p.id,name:p.name,active:p.active,manager_assignable:p.manager_assignable,roles:[],revision:p.revision,impact:p}:{name:'',active:true,manager_assignable:true,roles:[]};editor.value='position'}
async function editManagers(){
  loading.value=true;error.value=''
  try{managerPeople.value=(await get('/users')).filter(p=>(p.department_ids || [p.department_id]).includes(selectedId.value) && p.active);draft.value={user_ids:selected.value.head_ids.filter(id=>managerPeople.value.some(p=>p.id===id)),revision:selected.value.revision};saveError.value='';editor.value='managers'}
  catch(e){error.value=e.message}finally{loading.value=false}
}
async function addExistingMember(){
  loading.value=true;error.value=''
  try{existingPeople.value=await get('/users');existingId.value=null;memberSearch.value='';transferImpact.value=null;draft.value={position_ids:[]};saveError.value='';editor.value='existing'}
  catch(e){error.value=e.message}finally{loading.value=false}
}
async function chooseExistingMember(){
  const id=existingId.value
  transferImpact.value=null;saveError.value='';impactLoading.value=true
  try{const result=await get(`/users/${id}/organization-impact`);if(id===existingId.value)transferImpact.value=result}
  catch(e){if(id===existingId.value)saveError.value=e.message}finally{if(id===existingId.value)impactLoading.value=false}
}
let deletionRequest = 0
async function inspectDeletion(kind, item){
  const request = ++deletionRequest
  editor.value='delete-'+kind;draft.value={id:item.id,name:item.name,revision:item.revision}
  deletionImpact.value=null;deletionLoading.value=true;saveError.value=''
  const path=kind==='department'?'/departments/':'/department-positions/'
  try {
    const result=await get(`${path}${item.id}/deletion-impact`)
    if(request!==deletionRequest || editor.value!=='delete-'+kind || draft.value.id!==item.id)return
    deletionImpact.value=result;draft.value={id:result.id,name:result.name,revision:result.revision}
  } catch(e) {if(request===deletionRequest)saveError.value=e.message}
  finally {if(request===deletionRequest)deletionLoading.value=false}
}
async function save(){
  saving.value=true;saveError.value=''
  try{
    const d=draft.value
    if(deleting.value){
      if(!deletionImpact.value?.can_delete || deletionLoading.value)return
      const path=editor.value==='delete-department'?'/departments/':'/department-positions/'
      await del(`${path}${d.id}?revision=${d.revision}`)
      const label=deletionLabel.value
      editor.value='';await reload();emit('changed');emit('notice',`${label}已删除`);return
    }else if(editor.value==='department'){
      const {id,...body}=d;const saved=id?await patch(`/departments/${id}`,body):await post('/departments',body);selectedId.value=saved.id
    }else if(editor.value==='position'){
      const {id,impact,...body}=d;if(id)await patch(`/department-positions/${id}`,body);else await post(`/departments/${selectedId.value}/positions`,body)
    }else if(editor.value==='existing'){
      const person=existingMember.value
      if(!person || !transferImpact.value)return
      await put(`/users/${person.id}/organization`,{department_id:selectedId.value,position_ids:d.position_ids,revision:person.organization_revision,set_primary:false})
    }else await put(`/departments/${selectedId.value}/managers`,d)
    editor.value='';await reload();emit('changed');emit('notice','组织配置已保存')
  }catch(e){saveError.value=e.message}finally{saving.value=false}
}
function displayPositions(person) {
  const ids = new Set([selectedId.value])
  if(isAdmin.value && includeDescendants.value) {
    let changed=true
    while(changed) {changed=false;for(const d of directory.value) if(ids.has(d.parent_id) && !ids.has(d.id)){ids.add(d.id);changed=true}}
  }
  return person.positions.filter(p=>ids.has(p.department_id)).map(p=>(isAdmin.value && includeDescendants.value?p.department_name+' / ':'')+p.name+(p.inherited?'（来自'+p.source_department_name+'）':'')+(p.active?'':'（已停用）')).join('、')
}
async function memberSaved(){editedMember.value=null;await reload();emit('changed')}
onMounted(reload)
</script>
<template>
  <section class="panel org-management">
    <div class="org-heading"><div><h3>部门与岗位</h3><p>维护组织架构，指定负责人，按部门分配岗位成员。</p></div><button v-if="isAdmin" class="primary-button" :disabled="loading" @click="editDepartment()">＋ 新建部门</button></div>
    <p v-if="error" class="inline-error" role="alert">{{error}} <button class="text-button" @click="reload">重试</button></p>
    <p v-if="loading" class="field-help" role="status">正在加载组织信息…</p>
    <div v-if="directory.length" class="org-layout">
      <nav class="org-tree" aria-label="部门组织架构"><button v-for="d in tree" :key="d.id" :class="{selected:d.id===selectedId}" :style="{paddingLeft:(14+Math.min(d.depth,6)*15)+'px'}" :aria-current="d.id===selectedId?'true':undefined" :disabled="loading" @click="choose(d)"><span>{{d.name}}<small v-if="!d.active"> 已停用</small></span><span class="org-count" :title="'含下级部门，人员去重统计；直属 '+d.direct_member_count+' 人'">{{d.member_count}}</span></button></nav>
      <div v-if="selected" class="org-detail">
        <div class="org-heading"><div><div class="org-title"><h3>{{selected.name}}</h3><span class="status-tag" :class="selected.active?'green':'gray'">{{selected.active?'启用':'停用'}}</span></div><p>{{selected.path}}</p><p class="org-member-summary">直属 {{selected.direct_member_count}} 人 · 含下级共 {{selected.member_count}} 人（去重）</p></div><div v-if="isAdmin" class="org-member-actions"><button class="secondary-button" :disabled="loading" @click="editDepartment(selected)">编辑部门</button><button class="text-button org-delete-button" :disabled="loading" @click="inspectDeletion('department',selected)">删除部门</button></div></div>
        <p v-if="selected.description" class="org-description">{{selected.description}}</p>
        <div class="org-heads"><span>部门负责人</span><strong>{{selected.heads.map(p=>p.name).join('、') || '待设置'}}</strong><button v-if="isAdmin && selected.active" class="text-button" :disabled="loading" @click="editManagers">设置负责人</button></div>
        <p v-if="selected.approval_mode==='legacy_roles'" class="org-legacy">历史部门 · 请确认负责人。新申请按明确任命匹配；在途任务保留原快照。{{selected.head_candidates.length?'原负责人候选：'+selected.head_candidates.map(p=>p.name).join('、'):''}}</p>
        <div class="org-local-tabs" role="tablist" aria-label="部门详情"><button id="org-members-tab" role="tab" aria-controls="org-members-panel" :aria-selected="tab==='members'" :class="{selected:tab==='members'}" @click="tab='members'">成员 {{isAdmin && includeDescendants?selected.member_count:selected.direct_member_count}}</button><button id="org-positions-tab" role="tab" aria-controls="org-positions-panel" :aria-selected="tab==='positions'" :class="{selected:tab==='positions'}" @click="tab='positions'">岗位 {{selected.positions.length}}</button></div>
        <div v-if="tab==='members'" id="org-members-panel" role="tabpanel" aria-labelledby="org-members-tab">
          <div class="org-section-heading"><div><p class="field-help">加入其他部门会保留原部门岗位和任命；角色由管理员明确维护。</p><label v-if="isAdmin" class="org-check"><input v-model="includeDescendants" type="checkbox" @change="memberPage=1;choosePage()" />包含下级部门成员</label></div><div v-if="isAdmin && selected.active" class="org-member-actions"><button class="secondary-button" @click="addExistingMember">＋ 添加已有成员</button><button class="secondary-button" @click="emit('create-member',selected.id)">＋ 新建成员</button></div></div>
          <div class="table-wrap"><table class="data-table"><thead><tr><th>成员</th><th>部门岗位</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="person in members" :key="person.id"><td><strong>{{person.display_name}}</strong><small class="org-username">{{person.username}}</small><small class="org-username">{{(person.departments || []).map(d=>d.name).join('、')}}</small></td><td>{{displayPositions(person) || '未分配岗位'}}</td><td><span class="status-tag" :class="person.active?'green':'gray'">{{person.active?'启用':'停用'}}</span></td><td><button v-if="person.can_manage_organization" class="text-button" @click="editedMember=person">调整组织岗位</button><span v-else class="muted">由管理员维护</span></td></tr></tbody></table></div><div class="org-mobile-members"><article v-for="person in members" :key="person.id"><div><strong>{{person.display_name}}</strong><span class="status-tag" :class="person.active?'green':'gray'">{{person.active?'启用':'停用'}}</span></div><p>岗位：{{displayPositions(person) || '未分配岗位'}}</p><button v-if="person.can_manage_organization" class="text-button" @click="editedMember=person">调整组织岗位</button><small v-else>由管理员维护</small></article></div><p v-if="!members.length" class="empty-mini">本部门暂无成员，可由管理员添加。</p>
          <div class="org-pagination"><span>共 {{memberTotal}} 人 · {{memberPage}} / {{memberPages}} 页</span><button class="secondary-button" :disabled="loading || memberPage<=1" @click="pageMembers(-1)">上一页</button><button class="secondary-button" :disabled="loading || memberPage>=memberPages" @click="pageMembers(1)">下一页</button></div>
        </div>
        <div v-else id="org-positions-panel" role="tabpanel" aria-labelledby="org-positions-tab">
          <div class="org-section-heading"><p class="field-help">上级岗位可直接分配给本部门成员；同一上下级链路不能重复创建同名岗位。</p><button v-if="isAdmin && selected.active" class="secondary-button" @click="editPosition()">＋ 添加岗位</button></div>
          <div class="org-position-list"><article v-for="p in selected.positions" :key="p.id"><div><strong>{{p.name}}</strong><small class="org-position-origin">{{p.inherited?'来自'+p.source_department_name:'本部门'}}</small><span class="status-tag" :class="p.active?'green':'gray'">{{p.active?'启用':'停用'}}</span><p>部门岗位用于组织任职，项目职责在项目中指定</p><small>{{p.has_descendants?'直属 '+p.direct_member_count+' 人 · 含下级共 '+p.member_count+' 人（去重）':p.member_count+' 人（本部门）'}} · {{p.manager_assignable?'允许负责人分配':'由管理员分配'}}</small></div><div v-if="isAdmin && p.inherited" class="org-member-actions"><button class="text-button" @click="openPositionSource(p)">前往来源部门</button></div><div v-else-if="isAdmin" class="org-member-actions"><button class="text-button" @click="editPosition(p)">编辑岗位</button><button class="text-button org-delete-button" @click="inspectDeletion('position',p)">删除岗位</button></div></article></div><p v-if="!selected.positions.length" class="empty-mini">暂无岗位，可添加「销售」「售前」等岗位。</p>
        </div>
      </div>
    </div>
    <div v-else-if="!loading && !error" class="empty-state">{{isAdmin?'尚无部门，点击「新建部门」建立组织架构。':'暂无可管理的部门，请联系管理员确认负责人任命。'}}</div>
  </section>
  <MemberOrganization v-if="editedMember" :member="editedMember" :department-id="(editedMember.department_ids || [editedMember.department_id]).includes(selectedId)?selectedId:null" :user="user" :departments="directory" @close="editedMember=null" @saved="memberSaved" @notice="emit('notice',$event)" />
  <div v-if="editor" class="modal-backdrop" @click.self="!saving && (editor='')"><form class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="org-editor-title" @submit.prevent="save"><div class="modal-head"><h2 id="org-editor-title">{{deleting?'删除'+deletionLabel:editor==='department'?(draft.id?'编辑部门':'新建部门'):editor==='position'?(draft.id?'编辑岗位':'添加岗位'):editor==='existing'?'添加已有成员':'设置部门负责人'}}</h2><button type="button" class="text-button" :disabled="saving" @click="editor=''">关闭</button></div><div class="modal-body">
    <section v-if="deleting" class="org-delete-preview">
      <p>确认删除{{deletionLabel}}「{{draft.name}}」？</p>
      <p v-if="deletionLoading" class="field-help" role="status">正在检查关联内容…</p>
      <template v-else-if="deletionImpact">
        <template v-if="deletionImpact.can_delete"><p class="field-help">此{{deletionLabel}}没有阻止删除的关联。确认后将删除{{deletionLabel}}配置，操作记录会保留。</p></template>
        <template v-else><p class="inline-error">暂时无法删除，请先处理以下关联：</p><ul class="org-delete-blockers"><li v-for="b in deletionImpact.blockers" :key="b.label">{{b.label}}：{{b.count}} 项</li></ul><p class="field-help">{{deletionLabel==='部门'?'请先移出成员、迁移下级部门与项目、删除空岗位，并解除负责人和审批引用。':'请先在成员的组织岗位中解除该岗位分配，再删除。'}}人员账号和其他部门角色不会自动移除。</p></template>
      </template>
      <button v-if="!deletionLoading" type="button" class="text-button" :disabled="saving" @click="inspectDeletion(editor==='delete-department'?'department':'position',draft)">重新检查</button>
    </section>
    <fieldset v-else class="org-editor-fields" :disabled="saving">
      <template v-if="editor==='department'"><label class="field"><span>部门名称 *</span><input v-model.trim="draft.name" required maxlength="100" /></label><label class="field"><span>上级部门</span><select v-model="draft.parent_id"><option :value="null">无（顶级部门）</option><option v-for="d in directory.filter(d=>d.id!==draft.id && d.active)" :key="d.id" :value="d.id">{{d.path}}</option></select></label><label class="field"><span>说明</span><textarea v-model="draft.description" maxlength="500" rows="2" /></label><label class="field"><span>排序</span><input v-model.number="draft.sort_order" type="number" /></label><label class="org-check"><input v-model="draft.active" type="checkbox" />启用部门</label><p class="field-help">停用前须先处理启用成员、项目、子部门和未完成部门审批。</p></template>
      <template v-else-if="editor==='position'"><label class="field"><span>岗位名称 *</span><input v-model.trim="draft.name" required maxlength="100" placeholder="例如：销售、售前" /></label><label class="org-check"><input v-model="draft.manager_assignable" type="checkbox" />允许部门负责人分配</label><p class="field-help">负责人可分配允许委派的部门岗位；项目审批和回填按项目中的岗位人员执行。</p><label class="org-check"><input v-model="draft.active" type="checkbox" />启用岗位</label><p v-if="draft.impact" class="org-impact">影响范围：{{draft.impact.referenced_department_count}} 个引用部门、{{draft.impact.member_count}} 名去重岗位成员、{{draft.impact.project_assignments}} 项项目岗位、{{draft.impact.pending_tasks}} 项待审批。调整组织任职不会改变项目岗位；项目任务人员请在关联项目中维护。</p></template>
      <template v-else-if="editor==='managers'"><p class="field-help">从本部门启用成员中选择，可指定多人。其他部门人员可先通过「添加已有成员」调入。保存后负责人获得本部门人员管理能力及部门负责人审批身份。</p><div class="org-manager-choices"><label v-for="p in managerPeople" :key="p.id"><input v-model="draft.user_ids" type="checkbox" :value="p.id" />{{p.display_name}}</label></div><p v-if="!managerPeople.length" class="empty-mini">请先向该部门添加启用成员。</p><p class="org-impact">本部门当前有 {{selected.pending_tasks}} 项待审批。保存后，新申请按指定负责人匹配；失效待办须由管理员转交，审批人数不足时会阻止新申请提交。</p></template>
      <template v-else-if="editor==='existing'">
        <label class="field"><span>搜索已有成员</span><input v-model="memberSearch" placeholder="姓名、用户名或原部门" /></label>
        <label class="field"><span>选择已有成员 *</span><select v-model="existingId" required @change="chooseExistingMember"><option :value="null" disabled>选择全平台已有成员</option><option v-for="person in candidateMembers" :key="person.id" :value="person.id" :disabled="(person.department_ids || [person.department_id]).includes(selectedId)">{{person.display_name}} · {{person.username}} · {{person.department_path || person.department || '待分配'}}{{(person.department_ids || [person.department_id]).includes(selectedId)?'（已在本部门）':!person.active?'（已停用）':''}}</option></select></label>
        <fieldset class="org-template-choices"><legend>本部门岗位 · 可多选</legend><label v-for="p in selected.positions.filter(p=>p.active && p.source_department_active!==false)" :key="p.id"><input v-model="draft.position_ids" type="checkbox" :value="p.id" />{{p.name}}<small v-if="p.inherited">（来自{{p.source_department_name}}）</small></label><p v-if="!selected.positions.some(p=>p.active && p.source_department_active!==false)" class="field-help">本部门暂无启用岗位，可先加入后再分配。</p></fieldset>
        <p v-if="existingMember" class="org-impact">将 {{existingMember.display_name}} 加入「{{selected.path}}」。原部门关系、岗位、负责人任命、直接角色和项目关系全部保留；一个人可以同时在多个部门。</p>
        <p v-if="transferImpact" class="field-help">当前关联 {{transferImpact.project_assignments}} 项项目岗位、{{transferImpact.pending_tasks}} 项待审批；失效任务由管理员转交。</p><p v-if="impactLoading" class="field-help" role="status">正在核对调动影响…</p>
      </template>
    </fieldset>
    <p v-if="saveError" class="inline-error" role="alert">{{saveError}}</p><div class="form-footer"><button type="button" class="secondary-button" :disabled="saving" @click="editor=''">取消</button><button class="primary-button" :disabled="saving || (deleting && (deletionLoading || !deletionImpact?.can_delete)) || (editor==='existing' && (!existingMember || !transferImpact || impactLoading))">{{saving?(deleting?'删除中…':'保存中…'):deleting?'确认删除'+deletionLabel:editor==='existing'?'加入并保存':'保存'}}</button></div>
  </div></form></div>
</template>
<style scoped>
.org-delete-button{color:#b34b55}.org-delete-preview{font-size:14px;line-height:1.8}.org-delete-blockers{padding-left:22px;color:#8b6242;font-size:12px}.org-member-actions{display:flex;gap:8px;flex-wrap:wrap}
.org-management{padding:24px}.org-mobile-members{display:none}.org-heading,.org-section-heading,.org-heads,.org-title,.org-pagination{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.org-heading h3{font-size:15px;margin:0}.org-heading p,.org-description{font-size:12px;color:#8493a8;line-height:1.7;margin:7px 0 0}.org-layout{display:grid;grid-template-columns:220px minmax(0,1fr);gap:24px;margin-top:24px}.org-detail,.org-tree{min-width:0}.org-tree{border-right:1px solid #e8edf5;padding-right:14px}.org-tree button{width:100%;display:flex;gap:8px;align-items:center;justify-content:space-between;text-align:left;background:none;border:0;padding:12px 10px;border-radius:8px;color:#596b85;font-size:12px;overflow-wrap:anywhere}.org-tree button.selected{background:#edf2ff;color:#526fce}.org-count{font-size:11px;color:#8b9bb2}.org-heads{justify-content:flex-start;font-size:12px;background:#f7f9fd;padding:14px;border-radius:9px;margin-top:18px}.org-heads>span{color:#8898ad}.org-heads>strong{font-weight:500}.org-heads .text-button{margin-left:auto}.org-title{justify-content:flex-start}.org-legacy,.org-impact{background:#fff8e9;color:#8a6b36;padding:12px;border-radius:8px;font-size:12px;line-height:1.8}.org-local-tabs{display:flex;gap:22px;border-bottom:1px solid #e6ebf3;margin:20px 0}.org-local-tabs button{background:none;border:0;border-bottom:2px solid transparent;padding:12px 0;font-size:13px;color:#8190a6}.org-local-tabs button.selected{color:#5472d4;border-color:#5472d4}.org-section-heading{margin-bottom:15px}.org-username{display:block;color:#93a1b4;font-size:11px;margin-top:5px}.org-pagination{justify-content:flex-end;font-size:12px;color:#8190a6;margin-top:16px}.org-pagination>span{margin-right:auto}.org-position-list{display:grid;gap:12px}.org-position-list article{display:flex;align-items:center;justify-content:space-between;gap:14px;border:1px solid #e5eaf3;border-radius:10px;padding:16px;font-size:13px}.org-position-list .status-tag{margin-left:12px}.org-position-list p{font-size:12px;color:#8392aa;margin:9px 0}.org-position-origin{margin-left:10px}.org-position-list small{color:#8c9ab0;font-size:11px}.org-editor-fields{padding:0;border:0;display:grid;gap:16px;min-width:0}.org-template-choices,.org-manager-choices{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.org-template-choices label,.org-manager-choices label,.org-check{display:flex;align-items:center;gap:8px;font-size:12px;color:#536681}.org-tree small{font-size:11px}@media(max-width:800px){.org-layout{grid-template-columns:minmax(0,1fr)}.org-tree{border-right:0;border-bottom:1px solid #e8edf5;padding:0 0 14px;display:flex;flex-wrap:wrap;gap:6px}.org-tree button{width:auto;flex:1 1 130px}.org-management{padding:16px}}@media(max-width:600px){.org-detail .table-wrap{display:none}.org-mobile-members{display:grid;gap:12px}.org-mobile-members article{border:1px solid #e5eaf3;border-radius:9px;padding:14px;min-width:0;overflow-wrap:anywhere}.org-mobile-members article>div{display:flex;align-items:center;gap:10px;justify-content:space-between;font-size:13px}.org-mobile-members p,.org-mobile-members small{font-size:12px;color:#8190a6;line-height:1.7;margin:10px 0}.org-mobile-members .text-button{padding:8px 0}}@media(max-width:420px){.org-template-choices,.org-manager-choices{grid-template-columns:1fr}.org-pagination{justify-content:flex-start}}
</style>
