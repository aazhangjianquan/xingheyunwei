<script setup>
import { ref, onMounted } from 'vue'
import { get, post, put } from './api'
import FlowSteps from './FlowSteps.vue'
const emit = defineEmits(['notice'])
const kind = ref('resource'), state = ref(null), roles = ref([]), users = ref([]), projects = ref([]), busy = ref(false), preview = ref(null), viewing = ref(null)
const sample = ref({ applicant_id: null, project_id: null, data: {} })
const labels = { resource: '资源申请', change: '变更管理', delivery: '私有化交付', license: 'License 授权' }
async function act(fn) { busy.value=true; try { await fn() } catch(e) { emit('notice', e.message) } finally { busy.value=false } }
async function load() { state.value = await get(`/admin/flows/${kind.value}`); preview.value=null; viewing.value=null }
async function save() { const r = await put(`/admin/flows/${kind.value}/draft`, { definition: state.value.draft, revision: state.value.revision }); state.value.revision=r.revision }
function publish() { act(async () => { await save(); await post(`/admin/flows/${kind.value}/publish`, { revision: state.value.revision }); await load(); emit('notice', '流程已发布；在途单据继续原版本') }) }
onMounted(() => act(async () => { [roles.value, users.value, projects.value] = await Promise.all([get('/roles'),get('/users'),get('/projects')]); await load() }))
</script>
<template>
  <section class="panel settings-card workflow-editor">
    <div class="settings-heading"><div><h3>审批流程设计</h3><p>分别配置各类审批流程，支持审批节点、条件分支和多人审批阈值。License 达到最终审批通过条件后自动生成密钥。系统管理员申请按关联项目所属部门匹配部门岗位及部门条件。</p></div></div>
    <div class="flow-toolbar"><select v-model="kind" :disabled="busy" @change="act(load)"><option v-for="(v,k) in labels" :key="k" :value="k">{{ v }}</option></select><span v-if="state" class="muted">{{ state.versions.length ? '已发布 v'+state.versions[0].version : '尚未发布' }} · 草稿修订 {{ state.revision }}</span><button class="secondary-button" :disabled="busy || !state" @click="act(async()=>{ await save(); emit('notice','草稿已保存') })">保存草稿</button><button class="secondary-button" :disabled="busy || !state" @click="act(async()=>{ await post(`/admin/flows/${kind}/validate`,{definition:state.draft}); emit('notice','流程结构校验通过') })">校验</button><button class="primary-button" :disabled="busy || !state" @click="publish">发布新版本</button></div>
    <template v-if="state"><FlowSteps :steps="state.draft.steps" :roles="roles" :users="users" :fields="state.fields" :kind="kind" root />
      <div class="flow-lifecycle"><span>提交</span><b>→</b><span>条件分支</span><b>→</b><span>审批 / 会签 / 或签 / N 人阈值</span><b>→</b><span>{{ kind === 'license' ? '生成 License' : '执行回填' }}</span><b>→</b><span>完成</span></div>
      <div class="flow-preview"><h4>路径预览</h4><p class="field-help">使用当前草稿计算路径与所有处理人，不创建待办或发送飞书通知。普通成员须有对应项目岗位权限；系统管理员可选择全部启用项目。{{kind === 'license' ? '末节点可配置多人，并选择通过阈值。' : ''}}</p><div class="form-grid"><label class="field"><span>申请人</span><select v-model.number="sample.applicant_id"><option :value="null">请选择</option><option v-for="u in users.filter(u=>u.active)" :value="u.id" :key="u.id">{{ u.display_name }} · {{ u.department }}</option></select></label><label class="field"><span>关联项目</span><select v-model.number="sample.project_id"><option :value="null">请选择</option><option v-for="p in projects.filter(p=>p.active)" :value="p.id" :key="p.id">{{ p.name }}</option></select></label><label v-for="f in state.fields.filter(f=>f!=='department')" :key="f" class="field"><span>{{ {environment:'环境',resource_type:'资源类型',change_type:'变更类型',cloud_type:'云环境',deployment_mode:'部署方式',compute_type:'计算资源',accelerator:'加速卡',product:'授权产品',customer:'客户名称'}[f] }}</span><input v-model="sample.data[f]" /></label></div><button class="secondary-button" :disabled="busy" @click="act(async()=>preview=await post(`/admin/flows/${kind}/preview`,{...sample,definition:state.draft}))">计算预计路径</button>
      <div v-if="preview" class="preview-result"><p v-if="preview.department_source==='project'" class="field-help">管理员申请 · 审批归属部门：{{preview.context.department}}（来自关联项目）</p><p v-for="b in preview.branches" :key="b.id">{{ b.name }}：<span v-for="a in b.branches" :key="a.id" :class="{muted:!a.selected}">{{ a.name }}（{{ a.selected?'命中':'跳过' }}） </span></p><div v-for="(n,i) in preview.path" :key="n.id" class="preview-step"><b>{{ i+1 }}. {{ n.name }}</b><span>{{ n.mode==='all'?'全部同意（会签）':n.mode==='any'?'任意一人同意（或签）':`任意 ${n.required_count} 人同意` }} · {{ n.assignees.map(a=>a.name).join('、') }}</span></div></div>
      </div>
      <div class="flow-versions"><h4>已发布版本 · 只读</h4><div class="flow-toolbar"><button v-for="v in state.versions" :key="v.id" class="secondary-button" @click="viewing=v">v{{ v.version }} · {{ v.created_at.slice(0,10) }}</button></div><template v-if="viewing"><p>v{{ viewing.version }} 已发布内容</p><pre>{{ JSON.stringify(viewing.definition, null, 2) }}</pre><button class="secondary-button" @click="state.draft=JSON.parse(JSON.stringify(viewing.definition)); emit('notice','已复制到编辑区，请保存草稿并发布新版本')">复制此版本到草稿</button></template></div>
    </template><div v-else class="empty-mini">加载流程…</div>
  </section>
</template>
