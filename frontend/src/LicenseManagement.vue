<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Copy, FileKey2, KeyRound, Plus, RefreshCw, Search, X } from 'lucide-vue-next'
import { get, post, del } from './api'
import ExpiryReminder from './ExpiryReminder.vue'
import ApplicationDepartment from './ApplicationDepartment.vue'

const props = defineProps({ user: { type: Object, required: true } })
const emit = defineEmits(['notice'])
const items = ref([]), counts = ref({ all: 0, pending: 0, in_progress: 0, generated: 0, rejected: 0 }), canManage = ref(false)
const page = ref(1), pageSize = ref(10), total = ref(0), totalPages = ref(1)
const search = ref(''), status = ref('all'), reminderFilter = ref('all')
const loading = ref(false), saving = ref(false), listError = ref(''), modalError = ref('')
const modal = ref(''), detail = ref(null), form = ref({}), deleteConfirm = ref(false)
const projects = ref([]), projectsLoading = ref(false), projectsError = ref(''), legacyProjectId = ref(null), legacyDepartmentId = ref(null)
const canSubmitLegacy = computed(() => detail.value?.stage === 'awaiting_workflow' && (detail.value.applicant_id === props.user.id || props.user.roles.some(r => ['admin','license_manager'].includes(r))))
const selectedProject = computed(() => projects.value.find(p => p.id === form.value.project_id))
const statusLabels = {pending:'待审批',in_progress:'审批中',generated:'已生成',rejected:'已驳回'}
const tabs = [{key:'all',label:'全部'},...Object.entries(statusLabels).map(([key,label])=>({key,label}))]
const actionComment = ref(''), resubmitId = ref(null), people = ref([])
const myTask = computed(() => detail.value?.my_tasks?.find(t => t.eligible))
const isAdmin = computed(() => props.user?.roles?.includes('admin'))
const rangeStart = computed(() => total.value ? (page.value - 1) * pageSize.value + 1 : 0)
const rangeEnd = computed(() => Math.min(page.value * pageSize.value, total.value))
let revision = 0, detailRevision = 0, projectRevision = 0, searchTimer, disposed = false, skipPageLoad = false
const dateTime = value => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '—'
const limit = value => value === -1 ? '不限' : value

async function load() {
  const current = ++revision
  loading.value = true
  listError.value = ''
  try {
    const params = new URLSearchParams({ page: String(page.value), page_size: String(pageSize.value), status: status.value, q: search.value.trim(), reminder: reminderFilter.value })
    const result = await get(`/licenses?${params}`)
    if (disposed || current !== revision) return
    items.value = result.items
    counts.value = result.counts
    total.value = result.total
    totalPages.value = result.total_pages
    canManage.value = result.can_manage
    skipPageLoad = true
    page.value = result.page
    skipPageLoad = false
  } catch (error) {
    if (!disposed && current === revision) {
      items.value = []
      canManage.value = false
      counts.value = { all: 0, pending: 0, in_progress: 0, generated: 0, rejected: 0 }
      total.value = 0
      listError.value = error.message
    }
  } finally { if (!disposed && current === revision) loading.value = false }
}
function scheduleLoad(delay = 0) {
  ++revision
  clearTimeout(searchTimer)
  loading.value = true
  searchTimer = window.setTimeout(load, delay)
}
watch([status, pageSize, reminderFilter], () => {
  skipPageLoad = true; page.value = 1; skipPageLoad = false
  scheduleLoad()
}, { flush: 'sync' })
watch(search, () => {
  skipPageLoad = true; page.value = 1; skipPageLoad = false
  scheduleLoad(300)
}, { flush: 'sync' })
watch(page, () => { if (!skipPageLoad) scheduleLoad() }, { flush: 'sync' })

function closeModal(push = true) {
  if (saving.value) return
  ++detailRevision
  ++projectRevision
  modal.value = ''; detail.value = null; modalError.value = ''
  deleteConfirm.value = false
  if (push && /^\/licenses\/\d+\/?$/.test(window.location.pathname)) window.history.pushState({}, '', '/licenses')
}
async function loadProjects(path = '/projects') {
  const current = ++projectRevision
  projectsLoading.value = true; projectsError.value = ''; projects.value = []
  try {
    const result = await get(path)
    if (!disposed && current === projectRevision) projects.value = result.filter(p => p.active && p.can_apply !== false)
  } catch (error) {
    if (!disposed && current === projectRevision) projectsError.value = error.message
  } finally { if (!disposed && current === projectRevision) projectsLoading.value = false }
}
async function newApplication() {
  closeModal()
  resubmitId.value = null
  form.value = { project_id: null, application_department_id: null, customer: '', product: '', tenant_id: 1, expire_at: '', account_number: -1, agent_number: -1, purpose: '', reminder_enabled: true }
  modal.value = 'apply'
  await loadProjects()
}
async function openDetail(id, push = true) {
  const current = ++detailRevision
  ++projectRevision
  legacyProjectId.value = null; legacyDepartmentId.value = null; projects.value = []; projectsError.value = ''; projectsLoading.value = false
  modal.value = 'detail'; detail.value = null; modalError.value = ''; actionComment.value = ''
  if (push) window.history.pushState({}, '', `/licenses/${id}`)
  try {
    const result = await get(`/licenses/${id}`)
    if (!disposed && current === detailRevision) {
      detail.value = result
      if (canSubmitLegacy.value) {
        await loadProjects(`/licenses/${id}/projects`)
        if (!disposed && current === detailRevision && projects.value.some(p => p.id === result.project_id)) legacyProjectId.value = result.project_id
      }
      if (!disposed && current === detailRevision && isAdmin.value) people.value = await get('/users')
    }
  } catch (error) { if (!disposed && current === detailRevision) modalError.value = error.message }
}
async function submitApplication() {
  saving.value = true
  modalError.value = ''
  try {
    if (projectsLoading.value || projectsError.value || !selectedProject.value) throw new Error('请选择有权限的启用项目；没有可选项目时请联系管理员分配项目岗位')
    const { expire_at, project_name, ...values } = form.value
    // The form explicitly uses Beijing time, independent of the browser timezone.
    const expire_time = new Date(`${expire_at}+08:00`).getTime()
    if (!Number.isFinite(expire_time) || expire_time <= Date.now()) throw new Error('请选择晚于当前时间的授权到期时间')
    const result = await post(resubmitId.value ? `/licenses/${resubmitId.value}/resubmit` : '/licenses', { ...values, expire_time })
    if (disposed) return
    emit('notice', 'License 申请已提交，等待审批')
    await load()
    await openDetail(result.id)
  } catch (error) { if (!disposed) modalError.value = error.message }
  finally { saving.value = false }
}
async function submitApproval(action) {
  if (!myTask.value) return
  const id = detail.value.id
  saving.value = true
  modalError.value = ''
  try {
    const result = await post(`/license/${id}/action`, {task_id:myTask.value.id,action,comment:actionComment.value})
    if (disposed) return
    detail.value = result
    emit('notice', detail.value.status === 'generated' ? '最终审批通过，License Key 已生成' : action === 'reject' ? '申请已驳回，可由申请者修改重提' : '审批通过，已流转至下一节点')
    actionComment.value = ''
    await load()
  } catch (error) {
    if (disposed) return
    if ([403,409].includes(error.status)) await openDetail(id, false)
    modalError.value = error.message
  } finally { saving.value = false }
}
async function submitReview() {
  saving.value = true
  modalError.value = ''
  try {
    if (projectsLoading.value || projectsError.value || !projects.value.some(p => p.id === legacyProjectId.value)) throw new Error('请选择申请者有权限的启用项目')
    const result = await post(`/licenses/${detail.value.id}/submit-review`, {project_id: legacyProjectId.value, application_department_id: legacyDepartmentId.value})
    if (disposed) return
    detail.value = result
    await load()
    emit('notice', '历史申请已提交审批，最终审批通过后开放密钥')
  } catch (error) { modalError.value = error.message }
  finally { saving.value = false }
}
async function editRejected() {
  const item = detail.value
  ++detailRevision
  form.value = Object.fromEntries(['project_id','application_department_id','customer','product','tenant_id','account_number','agent_number','purpose','reminder_enabled'].map(k => [k,item[k]]))
  form.value.expire_at = new Date(item.expire_time + 8*3600000).toISOString().slice(0,16)
  resubmitId.value = item.id
  modal.value = 'apply'
  await loadProjects()
}
async function transferTask(task) {
  saving.value = true
  modalError.value = ''
  try {
    await post(`/admin/tasks/${task.id}/reassign`, {assignee_id:task.new_assignee_id,reason:task.transfer_reason || ''})
    await openDetail(detail.value.id, false)
    emit('notice','审批任务已转交')
  } catch (error) { modalError.value = error.message }
  finally { saving.value = false }
}
function requestDelete() {
  if (detail.value?.can_delete && !saving.value) deleteConfirm.value = true
}
async function deleteLicense() {
  if (!detail.value?.can_delete || saving.value) return
  saving.value = true; modalError.value = ''
  try {
    await del(`/licenses/${detail.value.id}`)
    emit('notice', 'License 申请已删除，审批历史与操作日志已保留')
    deleteConfirm.value = false
    closeModal()
    await load()
  } catch (error) { modalError.value = error.message }
  finally { saving.value = false }
}
async function copyKey() {
  try {
    await navigator.clipboard.writeText(detail.value.license_key)
    emit('notice', 'License Key 已复制')
  } catch { modalError.value = '自动复制不可用，请选中下方完整 License Key 手动复制' }
}
function syncPath() {
  const match = window.location.pathname.match(/^\/licenses\/(\d+)\/?$/)
  if (match) void openDetail(Number(match[1]), false)
  else closeModal(false)
}
onMounted(() => {
  void load()
  syncPath()
  window.addEventListener('popstate', syncPath)
})
onBeforeUnmount(() => {
  disposed = true
  ++revision; ++detailRevision; ++projectRevision
  clearTimeout(searchTimer)
  window.removeEventListener('popstate', syncPath)
  detail.value = null
})
</script>

<template>
  <div class="license-management">
    <div class="section-summary">
      <div class="summary-icon"><KeyRound :size="23" /></div>
      <div><strong>{{ canManage ? '全部 License 申请' : '我的 License 申请' }}</strong><span>{{ canManage ? '查看全部申请与审批进度，按流程核对授权参数。' : '提交授权申请，查看自己的申请进度与 License Key。' }}</span></div>
      <button class="primary-button" :disabled="saving" @click="newApplication"><Plus :size="17" /> 申请 License</button>
    </div>
    <section class="panel list-panel" :aria-busy="loading">
      <div class="list-toolbar">
        <div class="tab-strip" aria-label="按 License 状态筛选"><button v-for="tab in tabs" :key="tab.key" :class="{ selected: status === tab.key }" :aria-pressed="status === tab.key" @click="status = tab.key">{{ tab.label }} <span>{{ counts[tab.key] }}</span></button></div>
        <div class="license-list-actions"><select v-model="reminderFilter" aria-label="License 提醒筛选"><option value="all">全部提醒状态</option><option value="enabled">提醒已开启</option><option value="disabled">提醒已关闭</option></select><div class="search-box"><Search :size="17" /><input v-model="search" aria-label="搜索 License 申请" placeholder="搜索项目、客户、产品、申请人…" maxlength="100" /></div><button class="icon-button" :disabled="loading" title="刷新申请" @click="load"><RefreshCw :size="17" /></button></div>
      </div>
      <div v-if="listError" class="license-error" role="alert">{{ listError }}<button class="text-button" @click="load">重试</button></div>
      <div class="table-wrap">
        <table class="data-table">
          <thead><tr><th>编号 / 项目</th><th>客户 / 产品</th><th>租户 ID</th><th>申请人</th><th>到期时间（北京时间）</th><th>状态</th><th>到期提醒</th><th>License Key</th></tr></thead>
          <tbody v-if="loading"><tr><td colspan="8" class="list-loading" role="status">正在查询 License 申请…</td></tr></tbody>
          <tbody v-else><tr v-for="item in items" :key="item.id" @click="openDetail(item.id)"><td><div class="table-title"><FileKey2 :size="20" /><div><strong>{{ item.project_name }}</strong><small>{{ item.code }}</small></div></div></td><td><div class="table-title"><div><strong>{{ item.customer }}</strong><small>{{ item.product }}</small></div></div></td><td>{{ item.tenant_id }}</td><td>{{ item.applicant_name }}</td><td class="table-date">{{ dateTime(item.expire_time) }}</td><td><span class="status-tag" :class="{generated:'green',rejected:'red',in_progress:'blue'}[item.status] || 'amber'"><i></i>{{ statusLabels[item.status] }}</span></td><td>{{ item.reminder_enabled ? '已开启' : '已关闭' }}</td><td><button class="text-button" @click.stop="openDetail(item.id)">{{ item.can_view_key ? '查看 Key' : '查看申请' }}</button></td></tr></tbody>
        </table>
        <div v-if="!loading && !listError && !items.length" class="empty-state"><div class="empty-icon"><KeyRound :size="25" /></div><strong>暂无符合条件的 License 申请</strong><p>调整筛选条件，或提交新的授权申请。</p></div>
      </div>
      <div class="pagination-bar"><div class="pagination-summary">共 <strong>{{ total }}</strong> 条<span v-if="total"> · 第 {{ rangeStart }}–{{ rangeEnd }} 条</span></div><label class="page-size-control">每页<select v-model.number="pageSize" aria-label="License 每页条数"><option v-for="size in [10, 20, 50]" :key="size" :value="size">{{ size }}</option></select>条</label><nav class="pagination-controls" aria-label="License 分页"><button class="page-button" :disabled="loading || page <= 1" @click="page--">上一页</button><span>{{ page }} / {{ totalPages }}</span><button class="page-button" :disabled="loading || page >= totalPages" @click="page++">下一页</button></nav></div>
    </section>

    <div v-if="modal" class="modal-backdrop" @click.self="closeModal()" @keydown.esc="closeModal()">
      <section class="modal-panel detail-panel" role="dialog" aria-modal="true" aria-labelledby="license-modal-title">
        <div class="modal-head"><div><h2 id="license-modal-title">{{ modal === 'apply' ? '申请 License 授权' : 'License 申请详情' }}</h2><p>{{ modal === 'apply' ? '填写授权参数，提交后进入审批流程。' : '申请参数与生成结果' }}</p></div><button class="icon-button" :disabled="saving" aria-label="关闭 License 弹窗" @click="closeModal()"><X :size="20" /></button></div>
        <div class="modal-body">
          <p v-if="modalError" class="license-error" role="alert">{{ modalError }}</p>
          <form v-if="modal === 'apply'" @submit.prevent="submitApplication">
            <div class="form-grid">
              <ApplicationDepartment v-model="form.application_department_id" :user="user" />
              <label class="field full"><span>关联项目 <b>*</b></span><select v-model.number="form.project_id" required :disabled="saving || projectsLoading || Boolean(projectsError)"><option :value="null" disabled>选择项目管理中的项目</option><option v-for="p in projects" :key="p.id" :value="p.id">{{p.name}} · {{p.department}}</option></select><small class="field-help">与资源申请使用相同的项目目录，仅可选择有权限的启用项目；审批可按项目岗位匹配处理人。</small><small v-if="isAdmin" class="field-help">系统管理员可申请全部启用项目；审批按本次选择的申请部门匹配。</small><small v-if="projectsLoading" class="field-help" role="status">正在加载项目…</small><small v-else-if="projectsError" class="license-error" role="alert">项目加载失败：{{projectsError}} <button type="button" class="text-button" @click="loadProjects()">重试</button></small><small v-else-if="!projects.length" class="field-help">暂无可选项目，请联系管理员在「项目管理」创建项目并分配岗位。</small><small v-else-if="form.project_id && !selectedProject" class="field-help">原关联项目已停用或已无权限，请重新选择。</small></label>
              <label class="field"><span>客户名称 <b>*</b></span><input v-model.trim="form.customer" required maxlength="180" placeholder="请输入客户或业务单位" /></label>
              <label class="field"><span>授权产品 <b>*</b></span><input v-model.trim="form.product" required maxlength="150" placeholder="请输入产品名称" /></label>
              <label class="field"><span>租户 ID <b>*</b></span><input v-model.number="form.tenant_id" type="number" required min="1" max="2147483647" step="1" /></label>
              <label class="field full"><span>到期时间（北京时间）<b>*</b></span><input v-model="form.expire_at" type="datetime-local" required /><small class="field-help">按北京时间生成授权到期时间。</small></label>
              <div class="field full"><label class="reminder-create"><input v-model="form.reminder_enabled" type="checkbox" /> 开启 License 到期飞书提醒</label><small class="field-help">到期前 1 个自然月、15 天、7 天、1 天提醒申请人和最后审批人，双方可在详情修改。</small></div>
              <label class="field"><span>账号数 <b>*</b></span><input v-model.number="form.account_number" type="number" required min="-1" max="2147483647" step="1" /><small class="field-help">-1 表示不限数量。</small></label>
              <label class="field"><span>Agent 数 <b>*</b></span><input v-model.number="form.agent_number" type="number" required min="-1" max="2147483647" step="1" /><small class="field-help">-1 表示不限数量。</small></label>
              <label class="field full"><span>申请用途 <b>*</b></span><textarea v-model.trim="form.purpose" required rows="4" maxlength="10000" placeholder="说明使用场景与授权需求" /></label>
            </div>
            <div class="form-footer"><button type="button" class="secondary-button" :disabled="saving" @click="closeModal()">取消</button><button class="primary-button" :disabled="saving || projectsLoading || Boolean(projectsError) || !selectedProject">{{ saving ? '提交中…' : '提交申请' }}</button></div>
          </form>
          <template v-else-if="detail">
            <div class="detail-topline"><span class="code-label">{{ detail.code }}</span><span class="status-tag" :class="{generated:'green',rejected:'red',in_progress:'blue'}[detail.status] || 'amber'">{{ statusLabels[detail.status] }}</span><button class="text-button license-refresh" :disabled="saving" @click="openDetail(detail.id, false)"><RefreshCw :size="14" /> 刷新详情</button></div>
            <div class="detail-section"><h3>申请信息</h3><div class="detail-grid"><div><span>关联项目</span><strong>{{ detail.project_name }}</strong><small v-if="!detail.project_id" class="field-help">历史申请 · 尚未关联项目</small></div><div><span>客户名称</span><strong>{{ detail.customer }}</strong></div><div><span>授权产品</span><strong>{{ detail.product }}</strong></div><div><span>申请人</span><strong>{{ detail.applicant_name }}</strong></div><div v-if="detail.approval_department"><span>审批归属部门</span><strong>{{detail.approval_department}}</strong><small v-if="detail.approval_department_source==='project'" class="field-help">历史流程 · 按项目所属部门匹配审批</small></div><div><span>租户 ID</span><strong>{{ detail.tenant_id }}</strong></div><div><span>到期时间（北京时间）</span><strong>{{ dateTime(detail.expire_time) }}</strong></div><div><span>账号数</span><strong>{{ limit(detail.account_number) }}</strong></div><div><span>Agent 数</span><strong>{{ limit(detail.agent_number) }}</strong></div><div class="full"><span>申请用途</span><strong>{{ detail.purpose }}</strong></div><div><span>申请时间（北京时间）</span><strong>{{ dateTime(detail.created_at) }}</strong></div></div></div>
            <ExpiryReminder :key="detail.id" kind="license" :item-id="detail.id" :value="detail.reminder" @updated="value => { detail.reminder = value; detail.reminder_enabled = value.enabled; load() }" />
            <div class="detail-section"><div class="detail-section-heading"><h3>License Key</h3><button v-if="detail.license_key" class="secondary-button" @click="copyKey"><Copy :size="15" /> 复制完整 Key</button></div>
              <template v-if="detail.license_key"><p class="field-help">完整授权结果包含 body 与 key，可复制用于产品授权。</p><textarea class="license-key" :value="detail.license_key" readonly rows="9" aria-label="生成的 License Key" spellcheck="false" /><p class="field-help">最终审批人：{{ detail.final_approver_name }} · 生成时间：{{ dateTime(detail.generated_at) }}（北京时间）</p></template>
              <p v-else-if="detail.status === 'generated'" class="license-key-restricted">密钥已生成，仅申请者与最终审批人可以查看。</p>
              <p v-else class="empty-mini">最终审批通过后自动生成密钥，仅申请者与最终审批人可以查看。</p>
            </div>
            <div class="detail-section"><h3>审批流程与历史</h3>
              <p v-if="!detail.rounds?.length" class="field-help">此历史申请尚未进入审批。请关联申请者有权限的启用项目，并在系统配置中发布 License 流程，再提交审核。</p>
              <article v-for="round in detail.rounds" :key="round.id" class="license-round"><div class="license-round-head"><strong>第 {{round.round}} 轮</strong><span>{{round.version_id ? '流程版本 #'+round.version_id : '历史流程'}} · {{statusLabels[round.status] || (round.status==='completed'?'已完成':round.status)}}</span></div><div v-for="node in round.nodes" :key="node.id" class="license-approval-node" :class="node.status"><div class="license-node-dot"></div><div class="license-node-content"><strong>{{node.name}}</strong><small>{{ {waiting:'待流转',pending:'待审批',completed:'已通过',rejected:'已驳回',closed:'已关闭'}[node.status] }} · {{node.node_type==='license_approval'?'最终审批 · ':''}}{{node.mode==='all'?'全部同意（会签）':node.mode==='any'?'任意一人同意（或签）':`任意 ${node.config?.required_count || 0} 人同意`}}</small><span v-if="!node.tasks.length">{{node.config.assignees?.map(a=>a.name).join('、')}}</span><div v-for="task in node.tasks" :key="task.id" class="license-task"><span>{{task.assignee_name}} · {{ {pending:'待处理',approve:'已通过',reject:'已驳回',closed:'已关闭'}[task.action] }}</span><p v-if="task.comment">{{task.comment}}</p><small v-if="task.acted_at">{{dateTime(task.acted_at)}}</small><p v-if="task.action==='pending' && !task.eligible" class="license-error">处理人账号或角色已变化，请管理员转交。</p><div v-if="isAdmin && task.action==='pending'" class="license-transfer"><select v-model.number="task.new_assignee_id" aria-label="转交审批人"><option :value="undefined" disabled>选择新审批人</option><option v-for="p in people.filter(p=>p.active && p.id!==detail.applicant_id)" :key="p.id" :value="p.id">{{p.display_name}}</option></select><input v-model="task.transfer_reason" placeholder="转交原因" aria-label="转交原因" /><button class="text-button" :disabled="saving || !task.new_assignee_id || !task.transfer_reason" @click="transferTask(task)">转交</button></div></div></div></div></article>
              <p v-for="transfer in detail.transfers" :key="transfer.id" class="field-help">任务 #{{transfer.task_id}} 已转交 · {{transfer.reason}} · {{dateTime(transfer.created_at)}}</p>
            </div>
            <div v-if="canSubmitLegacy" class="action-box"><h3>关联项目并补充审批</h3><ApplicationDepartment v-model="legacyDepartmentId" :user="{departments:detail.application_departments || []}" /><p>选择申请者有权限的启用项目。本次申请部门只能选择申请者已加入的部门。完成审批后才可查看密钥。</p><label class="field"><span>关联项目 <b>*</b></span><select v-model.number="legacyProjectId" :disabled="saving || projectsLoading"><option :value="null" disabled>请选择关联项目</option><option v-for="p in projects" :key="p.id" :value="p.id">{{p.name}} · {{p.department}}</option></select></label><p v-if="projectsLoading" class="field-help" role="status">正在加载申请者可用的项目…</p><p v-else-if="projectsError" class="license-error" role="alert">{{projectsError}} <button type="button" class="text-button" @click="loadProjects(`/licenses/${detail.id}/projects`)">重试</button></p><p v-else-if="!projects.length" class="field-help">申请者暂无可用项目，请先在项目管理中分配岗位。</p><button class="primary-button" :disabled="saving || projectsLoading || Boolean(projectsError) || !legacyProjectId" @click="submitReview">提交审批</button></div>
            <div v-if="detail.status==='rejected' && detail.applicant_id===user.id" class="action-box"><h3>申请已驳回</h3><p>修改授权参数后，按最新流程重新审批。</p><button class="primary-button" :disabled="saving" @click="editRejected">修改并重新提交</button></div>
            <form v-if="myTask" class="action-box license-approve-form" @submit.prevent="submitApproval('approve')"><h3>{{detail.current_node?.name}}</h3><p>{{detail.current_node?.node_type==='license_approval'?'通过后自动生成密钥，仅你与申请者可查看。':'请核对申请参数，再提交审批意见。'}}</p><label class="field"><span>审批意见</span><textarea v-model="actionComment" rows="3" placeholder="填写审核意见或驳回原因" /></label><div class="action-buttons"><button type="button" class="danger-button" :disabled="saving" @click="submitApproval('reject')">驳回修改</button><button class="primary-button" :disabled="saving">{{saving?'处理中…':detail.current_node?.node_type==='license_approval'?'通过并生成密钥':'同意'}}</button></div></form>
            <div v-if="detail.can_delete" class="record-danger-zone"><div><strong>删除这张 License 申请</strong><p>删除后列表和详情将隐藏，审批历史和操作日志仍会保留。</p></div><template v-if="deleteConfirm"><span class="record-danger-question">确认删除 {{detail.code}}？</span><button class="secondary-button" :disabled="saving" @click="deleteConfirm=false">取消</button><button class="danger-button" :disabled="saving" @click="deleteLicense">{{saving?'删除中…':'确认删除'}}</button></template><button v-else class="danger-button" :disabled="saving" @click="requestDelete">删除申请</button></div>
          </template>
          <p v-else-if="!modalError" class="empty-mini" role="status">正在加载申请详情…</p>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.license-key-restricted{font-size:12px;color:#8795ad;background:#f5f7fb;padding:16px;border-radius:8px;line-height:1.7}.license-round{border:1px solid #e7ebf3;border-radius:10px;padding:16px;margin:12px 0}.license-round-head{display:flex;justify-content:space-between;gap:12px;font-size:11px;margin-bottom:16px;color:#8b99b0}.license-round-head strong{color:#485a78}.license-approval-node{position:relative;display:flex;gap:12px;padding-bottom:18px}.license-approval-node:before{content:'';position:absolute;left:5px;top:14px;bottom:0;width:1px;background:#e3e9f3}.license-approval-node:last-child:before{display:none}.license-node-dot{height:11px;width:11px;margin-top:3px;flex:0 0 11px;background:#dce3ed;border-radius:50%;box-shadow:0 0 0 3px #f5f7fb}.pending .license-node-dot{background:#7796e8}.completed .license-node-dot{background:#62b595}.rejected .license-node-dot{background:#d78086}.license-node-content{min-width:0;flex:1}.license-node-content>strong{font-size:12px;color:#425471}.license-node-content>small{float:right;font-size:10px;color:#9ba8bb}.license-node-content>span{display:block;font-size:11px;color:#95a1b4;margin-top:8px}.license-task{font-size:11px;margin-top:8px;background:#f8fafd;padding:10px;border-radius:7px;color:#7f8fa6}.license-task p{white-space:pre-wrap;margin:6px 0;line-height:1.6}.license-task small{font-size:10px;color:#a2aec0}.license-approve-form{margin-top:16px}.license-transfer{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.license-transfer input,.license-transfer select{border:1px solid #dde4ee;border-radius:5px;font-size:11px;min-width:0;padding:6px;color:#5d6e88}

.license-list-actions{display:flex;align-items:center;gap:10px}.license-error{color:#b94e54;background:#fff3f3;border:1px solid #f5d5d5;border-radius:8px;padding:12px 14px;font-size:12px;line-height:1.7;overflow-wrap:anywhere}.license-error .text-button{margin-left:12px}.license-refresh{margin-left:auto}.license-key{box-sizing:border-box;width:100%;resize:vertical;border:1px solid #dce5f4;background:#f7f9fe;border-radius:8px;padding:14px;color:#344867;font:12px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere;white-space:pre-wrap}.detail-section-heading{gap:12px;margin-bottom:12px}.detail-section-heading h3{margin:0}.detail-topline{flex-wrap:wrap}.field-help{line-height:1.7}.table-title>svg{color:#6885d9;flex-shrink:0}.section-summary>.primary-button{flex-shrink:0}.pagination-controls>span{white-space:nowrap;padding:0 8px}.full{grid-column:1/-1}@media(max-width:700px){.license-list-actions{width:100%}.license-list-actions .search-box{min-width:0;flex:1}.section-summary>.primary-button{margin-left:59px}.detail-section-heading{align-items:flex-start}.detail-section-heading .secondary-button{font-size:11px}.license-key{font-size:11px}}
</style>
