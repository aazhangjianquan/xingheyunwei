<script setup>
import { ref, computed, onMounted } from 'vue'
import { get, put, del } from './api'
import DepartmentPicker from './DepartmentPicker.vue'
const props = defineProps({ member: Object, user: Object, departments: Array, departmentId: Number })
const emit = defineEmits(['close', 'saved', 'notice'])
const isAdmin = computed(() => props.user.canonical_roles?.includes('admin') || props.user.roles.includes('admin'))
const departmentId = ref(props.departmentId || (isAdmin.value ? props.member.department_id : props.departments.find(d=>(props.member.department_ids || [props.member.department_id]).includes(d.id))?.id))
const positionIds = ref([]), removeConfirm = ref(false)
const busy = ref(false), error = ref(''), impact = ref(null)
const selected = computed(() => props.departments.find(d => d.id === departmentId.value))
const positions = computed(() => selected.value?.positions || [])
const selectableDepartments = computed(() => props.departments.filter(d =>
  (d.active || d.id === props.member.department_id) &&
  (isAdmin.value || (props.member.department_ids || [props.member.department_id]).includes(d.id))))
const alreadyMember = computed(() => (props.member.department_ids || [props.member.department_id]).includes(departmentId.value))
function changeDepartment() {
  positionIds.value = (props.member.positions || []).filter(p=>p.department_id===departmentId.value).map(p=>p.id)
  removeConfirm.value=false;error.value=''
}
changeDepartment()
async function save() {
  busy.value = true; error.value = ''
  try {
    const result = await put(`/users/${props.member.id}/organization`, {department_id:departmentId.value,position_ids:positionIds.value,revision:props.member.organization_revision,set_primary:false})
    emit('saved', result); emit('notice','成员组织岗位已保存')
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function removeDepartment() {
  busy.value=true;error.value=''
  try {
    const result=await del(`/users/${props.member.id}/departments/${departmentId.value}?revision=${props.member.organization_revision}`)
    emit('saved',result);emit('notice','已移出该部门，其他部门角色保留')
  } catch(e) {error.value=e.message} finally {busy.value=false}
}
onMounted(async () => { try { impact.value = await get(`/users/${props.member.id}/organization-impact`) } catch(e) { error.value = e.message } })
</script>
<template>
  <div class="modal-backdrop" @click.self="!busy && emit('close')"><form class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="member-organization-title" @submit.prevent="save">
    <div class="modal-head"><h2 id="member-organization-title">调整组织岗位 · {{member.display_name}}</h2><button type="button" class="text-button" :disabled="busy" @click="emit('close')">关闭</button></div>
    <div class="modal-body">
      <p class="field-help">已加入：{{member.departments?.map(d=>d.path+(d.is_manager?'（负责人）':'')).join('；') || member.department_path || '待分配'}}。加入其他部门会保留原部门岗位、角色与任命。</p>
      <div class="field"><span>所属部门</span><DepartmentPicker v-model="departmentId" :departments="selectableDepartments" required :disabled="busy || (!isAdmin && departments.length<2)" @change="changeDepartment" /></div>
      <fieldset class="org-position-choices" :disabled="busy"><legend>当前部门岗位 · 可多选</legend><label v-for="p in positions" :key="p.id"><input v-model="positionIds" type="checkbox" :value="p.id" :disabled="!p.active || p.source_department_active===false || (!isAdmin && !p.can_assign)" />{{p.name}}<small v-if="p.inherited">来自{{p.source_department_name}}</small><small>{{!p.active || p.source_department_active===false?'已停用':!isAdmin && !p.can_assign?'由管理员维护':''}}</small></label><p v-if="!positions.length" class="field-help">本部门尚无岗位，可先加入部门，由管理员建立岗位。</p></fieldset>
      <p v-if="impact" class="field-help">当前关联 {{impact.project_assignments}} 项项目岗位、{{impact.pending_tasks}} 项待审批任务。只修改当前部门提交的岗位；其他部门岗位、任命和直接角色保留。</p>
      <div v-if="isAdmin && alreadyMember" class="org-removal"><button v-if="!removeConfirm" type="button" class="text-button" :disabled="busy" @click="removeConfirm=true">移出该部门</button><template v-else><p>将撤销「{{selected?.name}}」的部门关系、岗位及负责人任命。其他部门和直接角色保留。</p><button type="button" class="secondary-button" :disabled="busy" @click="removeConfirm=false">保留</button><button type="button" class="secondary-button" :disabled="busy" @click="removeDepartment">确认移出该部门</button></template></div>
      <p v-if="error" class="inline-error" role="alert">{{error}}</p><div class="form-footer"><button type="button" class="secondary-button" :disabled="busy" @click="emit('close')">取消</button><button class="primary-button" :disabled="busy || !departmentId">{{busy?'保存中…':'保存组织岗位'}}</button></div>
    </div>
  </form></div>
</template>
<style scoped>
.org-position-choices{border:1px solid #e4e9f1;border-radius:10px;margin:20px 0;padding:16px;display:flex;gap:14px;flex-wrap:wrap}.org-position-choices legend{font-size:12px;color:#526580;padding:0 6px}.org-position-choices label{display:flex;align-items:center;gap:7px;font-size:13px}.org-position-choices small{color:#8b99ac;font-size:11px}.org-removal{margin:16px 0;color:#8c642c;font-size:12px;line-height:1.7}.org-removal button{margin-right:10px}
</style>
