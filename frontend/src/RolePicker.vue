<script setup>
import { computed, ref } from 'vue'
import { Check, Search, ShieldCheck, X } from 'lucide-vue-next'
const props = defineProps({ modelValue: { type: Array, default: () => [] }, roles: { type: Array, default: () => [] }, disabled: Boolean, editableIds: { type: Array, default: null } })
const emit = defineEmits(['update:modelValue'])
const query = ref('')
const descriptions = {
    applicant: '提交申请，跟踪自己的业务进度', admin: '管理成员、平台配置和审批流程', license_manager: '查看并管理全部 License 申请', knowledge_admin: '参与知识库协作；内容访问仍由空间负责人授权',
  department_head: '按所属部门审核申请', ops_head: '统筹运维审核与资源安排', manager: '参与业务决策与审批', operator: '执行运维任务与交付回填', tester: '验证功能、变更结果与交付质量',
  commercial_head: '管理本部门销售/售前成员', project_manager_head: '管理本部门项目经理成员', technical_head: '管理本部门产品开发成员', agent_development_head: '管理本部门智能体开发成员',
  sales_presales: '负责客户需求、方案与商务协同', project_manager: '协调项目交付与成员分工', agent_developer: '负责智能体方案与开发交付', product_developer: '负责产品功能与技术实现',
}
const aliasMap = { leader: 'manager', project_owner: 'project_manager', project_director: 'project_manager_head', sales_head: 'commercial_head', sales_director: 'commercial_head', development_head: 'technical_head' }
const groups = computed(() => {
  const names = ['基础与系统', '审批与执行', '项目与业务']
  return names.map(name => ({ name, items: props.roles.filter(r => r.group === name && (!query.value || (r.name + (descriptions[r.id] || '')).includes(query.value.trim()))) }))
})
const selected = computed(() => props.roles.filter(role => isSelected(role.id)))
function locked(id) { return props.disabled || (props.editableIds !== null && !props.editableIds.includes(id)) }
function toggle(id) {
  if (locked(id)) return
  emit('update:modelValue', isSelected(id) ? props.modelValue.filter(r => r !== id && aliasMap[r] !== id) : [...props.modelValue, id])
}
function isSelected(id) { return props.modelValue.includes(id) || props.modelValue.some(r => aliasMap[r] === id) }
</script>
<template>
  <div class="role-picker">
    <div class="role-picker-head"><div><ShieldCheck :size="17" /><strong>角色与权限</strong><span>可多选</span></div><span class="role-selected-count">已选 {{ selected.length }} 项</span></div>
    <p class="role-picker-help">按实际职责分配多个角色。负责人按所属部门关联，可参与部门审批并管理对应岗位。<template v-if="editableIds !== null">灰色角色由系统管理员维护。</template></p>
    <div class="role-selected" aria-label="已选角色"><span v-for="role in selected" :key="role.id" class="role-token">{{ role.name }}<button type="button" :disabled="locked(role.id)" :aria-label="'移除' + role.name" @click="toggle(role.id)"><X :size="12" /></button></span><span v-if="!selected.length" class="role-none">至少选择一个角色</span></div>
    <label class="role-search"><Search :size="15" /><input v-model="query" :disabled="disabled" placeholder="搜索角色名称或职责" aria-label="搜索可选角色" /></label>
    <div class="role-groups"><fieldset v-for="group in groups.filter(g => g.items.length)" :key="group.name" :disabled="disabled"><legend>{{ group.name }}</legend><div class="role-card-grid"><label v-for="role in group.items" :key="role.id" class="role-card" :class="{ checked: isSelected(role.id), privileged: role.id === 'admin', locked: locked(role.id) }"><input type="checkbox" :disabled="locked(role.id)" :checked="isSelected(role.id)" @change="toggle(role.id)" /><span class="role-card-check"><Check v-if="isSelected(role.id)" :size="12" /></span><span class="role-card-text"><strong>{{ role.name }}</strong><small>{{ descriptions[role.id] || '参与对应岗位的业务协作' }}</small><span v-if="role.department_scoped" class="role-scope">本部门 · 负责人</span></span></label></div></fieldset><p v-if="!groups.some(g => g.items.length)" class="role-none">{{ roles.length ? '没有匹配的角色' : '正在加载角色目录…' }}</p></div>
  </div>
</template>
<style scoped>
.role-card.locked{opacity:.55;cursor:default}.role-token button:disabled{opacity:.35;cursor:default}.role-scope{display:inline-block;margin-top:6px;font-size:9px;color:#617ac0;background:#eaf0ff;border-radius:4px;padding:2px 5px}
.role-picker{border:1px solid #e1e7f0;border-radius:12px;background:#fff;padding:18px}.role-picker-head{display:flex;justify-content:space-between;align-items:center;gap:12px}.role-picker-head>div{display:flex;align-items:center;gap:8px;color:#43547a}.role-picker-head strong{font-size:13px;color:#253654}.role-picker-head>div>span{font-size:10px;border-radius:4px;background:#f0f3fa;padding:3px 5px;color:#7786a0}.role-selected-count{font-size:11px;color:#677ed2;white-space:nowrap}.role-picker-help{font-size:11px;color:#8190a5;line-height:1.6;margin:8px 0 12px}.role-selected{display:flex;flex-wrap:wrap;gap:7px;min-height:28px;margin-bottom:13px}.role-token{display:inline-flex;align-items:center;gap:7px;background:#edf2ff;color:#4c68c4;border:1px solid #dce5fd;border-radius:6px;padding:5px 8px;font-size:11px}.role-token button{display:grid;place-items:center;padding:0;border:0;background:transparent;color:#7890c8}.role-search{display:flex;align-items:center;gap:8px;border:1px solid #e7ebf2;border-radius:7px;padding:9px 10px;background:#fafbfd;color:#9ba8bb}.role-search input{width:100%;border:0;background:transparent;outline:none;color:#42536f;font-size:11px;padding:0}.role-groups{max-height:310px;overflow:auto;padding:0 3px 0 0}.role-groups fieldset{margin:15px 0 0;border:0;padding:0;min-width:0}.role-groups legend{padding:0;margin-bottom:9px;font-size:10px;font-weight:700;color:#8592a6;letter-spacing:.5px}.role-card-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.role-card{position:relative;display:flex;gap:9px;align-items:flex-start;border:1px solid #e5eaf1;padding:11px;border-radius:8px;cursor:pointer;transition:background .15s,border-color .15s}.role-card:hover{border-color:#acbced;background:#fcfcff}.role-card.checked{border-color:#93aaf0;background:#f5f7ff}.role-card:focus-within{outline:2px solid #8aa4ef;outline-offset:2px}.role-card input{position:absolute;opacity:0;width:1px;height:1px}.role-card-check{height:15px;width:15px;flex:0 0 15px;display:grid;place-items:center;border:1px solid #cdd6e4;border-radius:4px;margin-top:1px;background:white;color:white;box-sizing:border-box}.checked .role-card-check{border-color:#5978dc;background:#5978dc}.role-card-text strong,.role-card-text small{display:block}.role-card-text strong{font-size:11px;color:#3b4c68;font-weight:600;line-height:1.5}.role-card-text small{font-size:10px;color:#94a0b2;line-height:1.6;margin-top:3px}.privileged .role-card-text strong{color:#8a6840}.role-none{font-size:11px;color:#97a3b6;padding:4px 0}@media(max-width:520px){.role-card-grid{grid-template-columns:1fr}.role-groups{max-height:330px}.role-picker{padding:14px}}
</style>
