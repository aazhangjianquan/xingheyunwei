<script setup>
import { createClientId as uid } from './client-id'
const props = defineProps({ steps: Array, roles: Array, positions: {type:Array,default:()=>[]}, users: Array, fields: Array, root: Boolean, kind: String, departments: {type:Array,default:()=>[]} })
function add(type = 'approval') {
  const node = type === 'branch'
    ? { id: uid(), name: '条件分流', type, branches: [{ id: uid(), name: '条件分支', match: 'all', conditions: [{ field: props.fields[0], op: 'eq', value: '' }], steps: [] }, { id: uid(), name: '默认分支', default: true, steps: [] }] }
    : { id: uid(), name: '审批节点', type, mode: 'all', required_count: null, assignee: { source: 'project_role', role: 'project_manager' } }
  props.steps.splice(props.root ? props.steps.length - 1 : props.steps.length, 0, node)
}
function move(index, delta) {
  const to = index + delta
  if (props.steps[index]?.assignee?.source === 'organization_manager' || props.steps[to]?.assignee?.source === 'organization_manager') return
  if (to < 0 || to >= props.steps.length || ['execution', 'license_approval'].includes(props.steps[to].type)) return
  props.steps.splice(to, 0, props.steps.splice(index, 1)[0])
}
function sourceChanged(node) {
  node.assignee = node.assignee.source === 'fixed' ? { source: 'fixed', user_ids: [] } : { source: node.assignee.source, role: node.type === 'execution' ? 'operator' : node.assignee.source === 'department_role' ? 'department_head' : 'project_manager', ...(node.assignee.source==='department_role' && node.type!=='execution'?{department_scope:'auto'}:{}) }
}
function addArm(node) { node.branches.splice(node.branches.length - 1, 0, { id: uid(), name: '条件分支', match: 'all', conditions: [{ field: props.fields[0], op: 'eq', value: '' }], steps: [] }) }
function moveArm(node, i, d) { const to = i+d; if (to >= 0 && to < node.branches.length-1) node.branches.splice(to,0,node.branches.splice(i,1)[0]) }
const labels = { environment: '环境', resource_type: '资源类型', change_type: '变更类型', department: '审批归属部门', cloud_type: '云环境', deployment_mode: '部署方式', compute_type: '计算资源', accelerator: '加速卡', product: '授权产品', customer: '客户名称' }
function changeOp(c) { c.value = c.op === 'eq' ? '' : [] }
</script>
<template>
  <div class="flow-steps">
    <article v-for="(node, i) in steps" :key="node.id" class="flow-node" :class="{ execution: node.type === 'execution', branch: node.type === 'branch' }">
      <div class="flow-node-top"><span class="flow-index">{{ i + 1 }}</span><input v-model="node.name" :disabled="node.assignee?.source === 'organization_manager'" aria-label="节点名称" maxlength="150" /><span class="flow-kind">{{ node.type === 'execution' ? '末节点 · 运维回填' : node.type === 'branch' ? '条件分支' : node.type === 'license_approval' ? '末节点 · 审批并生成密钥' : '审批节点' }}</span><template v-if="!['execution','license_approval'].includes(node.type) && node.assignee?.source !== 'organization_manager'"><button type="button" class="text-button" :disabled="i === 0 || steps[i-1]?.assignee?.source === 'organization_manager'" @click="move(i,-1)">上移</button><button type="button" class="text-button" :disabled="i === steps.length-1 || ['execution','license_approval'].includes(steps[i+1]?.type)" @click="move(i,1)">下移</button><button type="button" class="text-button danger" @click="steps.splice(i,1)">删除</button></template></div>
      <p v-if="node.assignee?.source === 'organization_manager'" class="field-help">固定首审：本部门负责人；申请人是负责人则从上级开始；逐级无可用负责人时由其他启用的系统管理员审批。排除申请人本人，任意一人同意。后端在发布和提单时统一执行此规则。</p>
      <div v-else-if="node.type !== 'branch'" class="form-grid flow-node-fields">
        <label class="field"><span>处理人来源</span><select v-model="node.assignee.source" :disabled="node.type === 'license_approval'" @change="sourceChanged(node)"><option value="fixed">固定成员</option><option value="project_role">项目岗位</option></select><small v-if="node.assignee.source==='department_role'" class="field-help">按提单时选择的本次申请部门匹配，须来自申请人已加入的部门。</small></label>
        <label v-if="node.assignee.source==='department_role' && node.assignee.role==='department_head'" class="field"><span>负责人部门范围</span><select :value="node.assignee.department_scope || 'auto'" @change="node.assignee.department_scope=$event.target.value"><option value="auto">申请部门本级</option><option value="parent">直属上级部门（顶级使用本部门）</option><option value="current">申请归属部门本级</option></select></label>
        <label class="field"><span>审批方式</span><select v-model="node.mode"><option value="all">全部同意（会签）</option><option value="any">任意一人同意（或签）</option><option value="quorum">任意 N 人同意</option></select></label>
        <label v-if="node.mode === 'quorum'" class="field"><span>同意人数 N</span><input v-model.number="node.required_count" type="number" min="1" :max="node.assignee.source === 'fixed' ? node.assignee.user_ids.length || 1 : 99" required /><small class="field-help">达到 N 人同意后自动通过；其余待办会关闭。</small></label>
        <label v-if="node.assignee.source !== 'fixed'" class="field full"><span>项目岗位 · 按关联项目任命匹配</span><select v-model="node.assignee.role"><option v-for="r in positions.filter(r => node.type !== 'execution' || r.id === 'operator')" :key="r.id" :value="r.id">{{ r.name }}</option></select><small v-if="node.assignee.source==='department_role'" class="field-help">负责人仅从部门管理中的明确任命匹配，不使用旧职能角色兜底。</small></label>
        <label v-else-if="node.type === 'license_approval'" class="field full"><span>最终审批人 · 可多选</span><select v-model="node.assignee.user_ids" multiple size="5"><option v-for="p in users.filter(p => p.active)" :key="p.id" :value="p.id">{{p.display_name}} · {{p.department}}</option></select><small class="field-help">可配置多人，并在上方选择全部同意、任意一人或任意 N 人同意。达到通过条件后生成密钥，申请者与实际完成最终审批的人员可以查看。</small></label>
        <label v-else class="field full"><span>固定成员 · 可多选</span><select v-model="node.assignee.user_ids" multiple size="4"><option v-for="p in users.filter(p => p.active)" :key="p.id" :value="p.id">{{ p.display_name }} · {{ p.department }}</option></select></label>
      </div>
      <template v-else>
        <p class="field-help">从上到下首次匹配；未命中时进入默认分支，结束后继续后续节点。</p>
        <div v-for="(arm, ai) in node.branches" :key="arm.id" class="flow-arm"><div class="flow-arm-top"><input v-model="arm.name" aria-label="分支名称" /><strong v-if="arm.default">默认分支</strong><template v-else><button class="text-button" @click="moveArm(node,ai,-1)">上移</button><button class="text-button" @click="moveArm(node,ai,1)">下移</button><button class="text-button danger" @click="node.branches.splice(ai,1)">删除分支</button></template></div>
          <template v-if="!arm.default"><label class="field"><span>条件组合</span><select v-model="arm.match"><option value="all">全部满足</option><option value="any">任一满足</option></select></label>
            <div v-for="(c, ci) in arm.conditions" :key="ci" class="flow-condition"><select v-model="c.field" aria-label="条件字段" @change="changeOp(c)"><option v-for="f in fields" :key="f" :value="f">{{ labels[f] }}</option></select><select v-model="c.op" aria-label="条件操作" @change="changeOp(c)"><option value="eq">等于</option><option value="in">属于</option><option value="not_in">不属于</option></select><select v-if="c.field==='department'" v-model="c.value" :multiple="c.op!=='eq'" aria-label="选择审批归属部门"><option v-if="c.op==='eq'" value="" disabled>选择已有部门</option><option v-if="c.op==='eq' && c.value && !c.value.startsWith('department:')" :value="c.value">{{c.value}}（历史条件）</option><template v-if="c.op!=='eq'"><option v-for="value in c.value.filter(v=>!v.startsWith('department:'))" :key="value" :value="value">{{value}}（历史条件）</option></template><option v-for="d in departments" :key="d.id" :value="'department:'+d.id">{{d.path || d.name}}</option></select><input v-else-if="c.op === 'eq'" v-model="c.value" aria-label="条件值" placeholder="例如：生产" /><input v-else :value="c.value.join(',')" aria-label="条件值列表" placeholder="多个值用英文逗号分隔" @input="c.value = $event.target.value.split(',').map(v => v.trim()).filter(Boolean)" /><button class="text-button danger" @click="arm.conditions.splice(ci,1)">移除</button></div>
            <button class="text-button" @click="arm.conditions.push({ field: fields[0], op: 'eq', value: '' })">＋ 添加条件</button></template>
          <FlowSteps :steps="arm.steps" :roles="roles" :positions="positions" :users="users" :fields="fields" :kind="kind" :departments="departments" />
        </div><button class="secondary-button" @click="addArm(node)">＋ 添加条件分支</button>
      </template>
    </article>
    <div class="flow-add"><button class="secondary-button" @click="add()">＋ 审批节点</button><button class="secondary-button" @click="add('branch')">＋ 条件分支</button></div>
  </div>
</template>
