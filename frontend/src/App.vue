<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { get, post, put, patch, del } from './api'
import { readBootstrapSettings } from './bootstrap'
import ProjectManagement from './ProjectManagement.vue'
import SystemSettings from './SystemSettings.vue'
import LicenseManagement from './LicenseManagement.vue'
import ExpiryReminder from './ExpiryReminder.vue'
import PersonalReminders from './PersonalReminders.vue'
import AuditLog from './AuditLog.vue'
import RolePicker from './RolePicker.vue'
import ApplicationDepartment from './ApplicationDepartment.vue'
import OrganizationManagement from './OrganizationManagement.vue'
import MemberOrganization from './MemberOrganization.vue'
import DepartmentPicker from './DepartmentPicker.vue'
import PasswordField from './PasswordField.vue'
import AccountSettings from './AccountSettings.vue'
import DeliveryAttachments from './DeliveryAttachments.vue'
import KnowledgeManagement from './KnowledgeManagement.vue'
import { validPassword } from './password-policy'
import { LayoutDashboard, Server, RefreshCw, Boxes, CheckSquare, BookOpen, ShieldCheck, Users, Bell, Search, Plus, ChevronDown, ChevronRight, Menu, X, LogOut, ArrowUpRight, ArrowRight, Clock3, CircleCheck, AlertCircle, Database, HardDrive, Cloud, Cpu, KeyRound, FileText, CalendarDays, SlidersHorizontal, MoreHorizontal, Activity, LockKeyhole } from 'lucide-vue-next'

const nav = [
  { key: 'dashboard', label: '工作台', icon: LayoutDashboard, group: 'overview' },
  { key: 'resources', label: '资源申请', icon: Server, group: 'work' },
  { key: 'changes', label: '变更管理', icon: RefreshCw, group: 'work' },
  { key: 'deliveries', label: '私有化交付', icon: Boxes, group: 'work' },
  { key: 'licenses', label: 'License 管理', icon: KeyRound, group: 'work' },
  { key: 'projects', label: '项目管理', icon: Boxes, group: 'work' },
  { key: 'personal-reminders', label: '个人提醒', icon: Bell, group: 'work' },
  { key: 'tasks', label: '我的审批', icon: CheckSquare, group: 'work' },
  { key: 'wiki', label: '知识库', icon: BookOpen, group: 'knowledge' },
  { key: 'audit', label: '操作日志', icon: ShieldCheck, group: 'admin' },
  { key: 'users', label: '用户与权限', icon: Users, group: 'admin' },
  { key: 'notifications', label: '通知记录', icon: Bell, group: 'admin' },
  { key: 'settings', label: '系统配置', icon: SlidersHorizontal, group: 'admin' },
]
const titles = { 'personal-reminders': '个人提醒', licenses: 'License 管理', projects: '项目管理', dashboard: '工作台', resources: '资源申请', changes: '变更管理', deliveries: '私有化交付', tasks: '我的审批', wiki: '知识库', audit: '操作日志', users: '用户与权限', notifications: '通知记录', settings: '系统配置' }
const subtitles = { 'personal-reminders': '安排提醒时间，通过飞书提醒自己或指定成员', licenses: '申请授权、跟踪生成进度，安全查看 License Key', projects: '维护项目岗位，按实际职责分配审批', dashboard: '掌握资源、变更、交付与授权的每一个关键节点', resources: '统一提交、审批与追踪云资源需求', changes: '让每一次变更都有计划、有记录、可回溯', deliveries: '汇集交付信息与项目文档，跟踪每一次协作', tasks: '需要你处理的审批和回填事项', wiki: '沉淀运维规范、交付经验和实施方案', audit: '追溯谁在什么时候，对哪张单据做了什么操作', users: '管理成员角色和通知收件人', notifications: '查看飞书通知的发送状态', settings: '管理飞书通知、审批流程和资源目录' }
const kindLabel = { resource: '资源申请', change: '变更申请', delivery: '交付项目', license: 'License 授权', security: '访问安全' }
const stageLabel = { department_head: '部门负责人', ops_head: '运维负责人', manager: '管理者审批', leader: '管理者审批', operator: '运维回填' }
const statusLabel = { pending: '待审批', in_progress: '处理中', completed: '已完成', generated: '已生成', rejected: '已驳回' }
const roleLabel = ref({})
const roles = ref([])
const projects = ref([])
const resubmitId = ref(null)
const roleAlias = { leader: 'manager', project_owner: 'project_manager', project_director: 'project_manager_head', sales_head: 'commercial_head', sales_director: 'commercial_head', development_head: 'technical_head' }
const hasRole = (...values) => values.some(r => (user.value?.roles || []).some(current => current === r || roleAlias[current] === r))
const canManagePeople = () => user.value?.can_manage_people ?? hasRole('admin')
function roleOptionsFor(item) {
  if (hasRole('admin') || !item?.managed_roles?.length) return roles.value
  const allowed = new Set([...(item.canonical_roles || item.roles || []).map(r => roleAlias[r] || r), ...item.managed_roles])
  return roles.value.filter(role => allowed.has(role.id))
}
async function loadRoles() {
  roles.value = await get('/roles')
  roleLabel.value = Object.fromEntries(roles.value.flatMap(r=>[[r.id,r.name], ...Object.entries(roleAlias).filter(([,value]) => value === r.id).map(([key]) => [key,r.name])]))
}
const departmentDirectory = ref([])
async function loadDepartments() { departmentDirectory.value = await get('/departments') }

const typeIcons = { ECS: Server, MySQL: Database, Redis: Database, MongoDB: Database, PostgreSQL: Database, K8s: Boxes, OSS: HardDrive, GPU: Cpu, APIKEY: KeyRound }
const resourceCatalog = ref([])
const resourceCatalogError = ref('')
const activeResourceTypes = computed(() => resourceCatalog.value.filter(item => item.active))
const selectedResourceType = computed(() => activeResourceTypes.value.find(item => item.key === form.value.resource_type) || activeResourceTypes.value[0])

const user = ref(null)
const initialOptions = readBootstrapSettings()
const authOptions = ref(initialOptions || { site_name: '', feishu_login_enabled: false, local_login_enabled: true })
const restoringSession = ref(true)
const startupError = ref('')
const page = ref('dashboard')
const personalSharedId = ref(null)
const data = ref([])
const dashboard = ref(null)
const tasks = ref([])
const taskPage = ref(1)
const taskPageSize = ref(10)
const taskTotal = ref(0)
const taskTotalPages = ref(1)
const taskDeleteConfirm = ref(null)
const taskPaginationNumbers = computed(() => [...new Set([1, taskPage.value - 1, taskPage.value, taskPage.value + 1, taskTotalPages.value])].filter(n => n >= 1 && n <= taskTotalPages.value).sort((a, b) => a - b))
const users = ref([])
const editingFeishu = ref({})
const feishuDraft = ref({})
const loading = ref(false)
const sidebarOpen = ref(false)
const search = ref('')
const filterStatus = ref('all')
const reminderFilter = ref('all')
const listPages = ['resources', 'changes', 'deliveries']
const listPage = ref(1)
const listPageSize = ref(10)
const listTotal = ref(0)
const listTotalPages = ref(1)
const statusCounts = ref({ all: 0, pending: 0, in_progress: 0, completed: 0, rejected: 0 })
const adminPage = ref(1)
const adminPageSize = ref(10)
const adminTotal = ref(0)
const adminTotalPages = ref(1)
const adminSearch = ref('')
const adminStatus = ref('all')
const adminRole = ref('all')
const adminDepartment = ref('')
const currentDepartment = computed(() => departmentDirectory.value.find(item => item.name === (adminDepartment.value || (!hasRole('admin') ? user.value?.department : ''))))
const adminPaginationNumbers = computed(() => {
  const values = [...new Set([1, adminTotalPages.value, adminPage.value - 1, adminPage.value, adminPage.value + 1])]
    .filter(value => value >= 1 && value <= adminTotalPages.value).sort((a, b) => a - b)
  const result = []
  for (const value of values) { if (result.length && value - result.at(-1) > 1) result.push('…'); result.push(value) }
  return result
})
const listTabs = [{key:'all',label:'全部'}, {key:'pending',label:'待审批'}, {key:'in_progress',label:'处理中'}, {key:'completed',label:'已完成'}, {key:'rejected',label:'已驳回'}]
let loadRevision = 0
let listReloadTimer
const listRangeStart = computed(() => listTotal.value ? (listPage.value - 1) * listPageSize.value + 1 : 0)
const listRangeEnd = computed(() => Math.min(listPage.value * listPageSize.value, listTotal.value))
const paginationNumbers = computed(() => {
  const candidates = [...new Set([1, listTotalPages.value, listPage.value-1, listPage.value, listPage.value+1])]
    .filter(n => n >= 1 && n <= listTotalPages.value).sort((a,b)=>a-b)
  const result = []
  for (const n of candidates) {
    if (result.length && n - result.at(-1) > 1) result.push('…')
    result.push(n)
  }
  return result
})
const toast = ref('')
const modal = ref('')
const profileOpen = ref(false)
const detail = ref(null)
const detailKind = ref('')
const deleteConfirm = ref(false)
const saving = ref(false)
const deliveryAttachmentQueue = ref([])
const changeAttachmentQueue = ref([])
const changeUploadId = ref(null)
const changeAttachmentErrors = computed(() => changeAttachmentQueue.value.some(entry => entry.status === 'invalid'))
const deliveryUploadId = ref(null)
const deliveryAttachmentErrors = computed(() => deliveryAttachmentQueue.value.some(entry => entry.status === 'invalid'))
const loginForm = ref({ username: '', password: '' })
const loginError = ref('')
const feishuTaskRetry = ref(false)
const feishuRedirecting = ref(false)
watch([() => loginForm.value.username, () => loginForm.value.password], () => { loginError.value = '' })
const form = ref({})
const memberPasswordValid = computed(() => validPassword(form.value.password || ''))
const roleMember = ref(null)
const roleDraft = ref([])
const usersTab = ref('members'), organizationVersion = ref(0), organizationMember = ref(null)
const selectedCreationDepartment = computed(() => departmentDirectory.value.find(d=>d.id===form.value.department_id))
const selectedProjectOperations = computed(() => {
  const members=projects.value.find(p=>p.id===form.value.project_id)?.members || []
  return [...new Map(members.filter(m=>['ops_head','operator'].includes(m.role) && m.eligible).map(m=>[m.user_id,{id:m.user_id,display_name:m.name}])).values()]
})
watch(() => form.value.project_id, () => { if(modal.value==='change') form.value.ops_owner_id=null })

watch(() => form.value.department_id, () => { if(modal.value==='user') form.value.position_ids=[] })
async function organizationChanged() { user.value=await get('/me'); await loadPage() }
async function organizationSaved() { organizationMember.value=null; await organizationChanged(); organizationVersion.value++ }
function createDepartmentMember(id) { newForm('user'); form.value.department_id=id }
const actionForm = ref({ action: 'approve', comment: '', fulfillment: '' })

function notify(message) {
  toast.value = message
  window.setTimeout(() => { if (toast.value === message) toast.value = '' }, 3600)
}
function routeFromPath() {
  const [, section, id, action] = window.location.pathname.split('/')
  personalSharedId.value = section === 'personal-reminders' && action === 'files' && /^\d+$/.test(id) ? Number(id) : null
  page.value = ({ resource: 'resources', change: 'changes', delivery: 'deliveries' })[section] || (titles[section] ? section : 'dashboard')
  if (id && ['resource', 'change', 'delivery'].includes(section)) openDetail(section, Number(id), false)
}
function navigate(key) {
  if ((saving.value && modal.value === 'delivery')) return
  personalSharedId.value = null
  page.value = key
  modal.value = ''
  detail.value = null
  search.value = ''
  filterStatus.value = 'all'
  reminderFilter.value = 'all'
  sidebarOpen.value = false
  window.history.pushState({}, '', `/${key}`)
  loadPage()
}
async function loadPage() {
  clearTimeout(listReloadTimer)
  const revision = ++loadRevision
  const target = page.value
  if (!user.value) return
  loading.value = true
  try {
    if (listPages.includes(target)) {
      const params = new URLSearchParams({page: String(listPage.value), page_size: String(listPageSize.value), status: filterStatus.value, q: search.value.trim(), reminder: reminderFilter.value})
      if (target === 'resources') void loadResourceCatalog()
      const result = await get('/' + target + '?' + params)
      if (revision !== loadRevision || target !== page.value) return
      data.value = result.items
      listTotal.value = result.total
      listTotalPages.value = result.total_pages
      statusCounts.value = result.counts
      if (listPage.value !== result.page) {
        skipPageReload = true
        listPage.value = result.page
        skipPageReload = false
      }
    } else if (target === 'dashboard') {
      const result = await Promise.all([get('/dashboard'), get(`/tasks?page=1&page_size=${taskPageSize.value}`)])
      if (revision === loadRevision) { dashboard.value = result[0]; tasks.value = result[1].items; taskTotal.value = result[1].total; taskTotalPages.value = result[1].total_pages }
    } else if (target === 'tasks') {
      const result = await get(`/tasks?page=${taskPage.value}&page_size=${taskPageSize.value}`)
      if (revision === loadRevision) { tasks.value = result.items; taskTotal.value = result.total; taskTotalPages.value = result.total_pages; taskPage.value = result.page }
    } else if (target === 'users') {
      const query = new URLSearchParams({page:String(adminPage.value), page_size:String(adminPageSize.value), q:adminSearch.value.trim(), status:adminStatus.value, role:adminRole.value, department:adminDepartment.value})
      const [result] = await Promise.all([get(`/users?${query}`), loadRoles(), loadDepartments()])
      if (revision === loadRevision) { users.value = result.items; adminTotal.value = result.total; adminTotalPages.value = result.total_pages; adminPage.value = result.page }
    } else {
      const paths = { notifications: '/notifications' }
      if (paths[target]) {
        const query = new URLSearchParams({page:String(adminPage.value), page_size:String(adminPageSize.value), q:adminSearch.value.trim(), status:adminStatus.value})
        const result = await get(`${paths[target]}?${query}`)
        if (revision === loadRevision) { data.value = result.items; adminTotal.value = result.total; adminTotalPages.value = result.total_pages; adminPage.value = result.page }
      }
    }
  } catch (error) {
    if (revision === loadRevision) {
      notify(error.message)
    }
  }
  finally { if (revision === loadRevision) loading.value = false }
}
let skipPageReload = false
function scheduleListLoad(delay = 0) {
  if (!user.value || (!listPages.includes(page.value) && !['users', 'notifications', 'tasks'].includes(page.value))) return
  ++loadRevision
  clearTimeout(listReloadTimer)
  loading.value = true
  listReloadTimer = window.setTimeout(loadPage, delay)
}
watch(page, () => {
  clearTimeout(listReloadTimer)
  ++loadRevision
  search.value = ''
  filterStatus.value = 'all'
  reminderFilter.value = 'all'
  listPage.value = 1
  listTotal.value = 0
  listTotalPages.value = 1
  statusCounts.value = {all:0,pending:0,in_progress:0,completed:0,rejected:0}
  data.value = []
  adminPage.value = 1
  adminTotal.value = 0
  adminTotalPages.value = 1
  adminSearch.value = ''
  adminStatus.value = 'all'
  adminRole.value = 'all'
  adminDepartment.value = ''
  taskPage.value = 1; taskTotal.value = 0; taskTotalPages.value = 1; taskDeleteConfirm.value = null
}, {flush:'sync'})
watch([filterStatus, listPageSize, reminderFilter], () => {
  listPage.value = 1
  scheduleListLoad()
}, {flush:'sync'})
watch(search, () => {
  listPage.value = 1
  scheduleListLoad(300)
}, {flush:'sync'})
watch(listPage, () => { if (!skipPageReload) scheduleListLoad() }, {flush:'sync'})
watch([adminPageSize, adminStatus, adminRole, adminDepartment], () => {
  if (!['users', 'notifications'].includes(page.value)) return
  adminPage.value = 1; scheduleListLoad()
}, {flush:'sync'})
watch(adminSearch, () => {
  if (!['users', 'notifications'].includes(page.value)) return
  adminPage.value = 1; scheduleListLoad(300)
}, {flush:'sync'})
watch(adminPage, () => {
  if (!['users', 'notifications'].includes(page.value)) return
  scheduleListLoad()
}, {flush:'sync'})
watch([taskPage, taskPageSize], () => { if (page.value === 'tasks') scheduleListLoad() }, {flush:'sync'})
async function refreshAuthOptions() {
  authOptions.value = await get('/auth/options')
}
function loadSessionData() {
  routeFromPath()
  if (user.value) return loadPage()
}
function feishuTaskPath() {
  return /^\/(resource|change|delivery|licenses)\/[1-9]\d{0,18}$/.test(window.location.pathname)
}
function inFeishuClient() {
  return /(?:Feishu|Lark)\//i.test(window.navigator?.userAgent || '')
}
function startFeishuTaskLogin() {
  if (!feishuTaskPath() || !inFeishuClient()) return
  feishuRedirecting.value = true
  restoringSession.value = true
  window.location.replace(`/api/auth/feishu/task/start?${new URLSearchParams({next: window.location.pathname})}`)
}
async function handleFeishuTaskEntry() {
  const fragment = new URLSearchParams((window.location.hash || '').slice(1))
  const ticket = fragment.get('feishu_ticket')
  const error = fragment.get('feishu_error')
  if (ticket || error) {
    // 清除一次性参数和旧身份，防止其他账号的历史会话盖过本次飞书认证结果。
    window.history.replaceState({}, '', window.location.pathname)
    localStorage.removeItem('yunwei_token')
    user.value = null
    feishuTaskRetry.value = feishuTaskPath() && inFeishuClient()
    if (ticket && inFeishuClient() && feishuTaskPath()) {
      try {
        const result = await post('/auth/ticket', {ticket})
        if (result.next !== window.location.pathname) throw new Error('登录任务地址不匹配，请从原消息重试')
        localStorage.setItem('yunwei_token', result.token)
        user.value = result.user
        feishuTaskRetry.value = false
        void loadSessionData()
      } catch (failure) { loginError.value = failure.message }
    } else {
      const messages = {
        unavailable: '飞书任务免密尚未启用或配置不完整，请使用账号密码登录。',
        expired: '飞书登录已过期或浏览器不匹配，请从任务消息重试。',
        denied: '飞书授权未完成，请重试或使用账号密码登录。',
        verification: '飞书身份验证失败，请重试；仍失败时请联系管理员检查应用配置。',
        unbound: '此飞书身份尚未绑定平台账号，请联系管理员配置，或使用账号密码登录。',
        disabled: '此账号已停用，请联系管理员启用后再登录',
      }
      loginError.value = ticket ? '请在飞书客户端内重新打开任务消息，或使用账号密码登录。' :
        messages[error] || '飞书登录未完成，请重试或使用账号密码登录。'
    }
    return true
  }
  if (!feishuTaskPath() || !inFeishuClient() || feishuTaskRetry.value) return false
  // 旧卡片的普通任务链接也支持免密；每次入站重新读取开关，关闭后立即回到密码登录。
  await refreshAuthOptions()
  if (!authOptions.value.feishu_task_login_enabled) return false
  startFeishuTaskLogin()
  return true
}
async function restoreSession() {
  restoringSession.value = true
  startupError.value = ''
  try {
    const fragment = new URLSearchParams((window.location.hash || '').slice(1))
    const taskEntry = fragment.has('feishu_ticket') || fragment.has('feishu_error') || (feishuTaskPath() && inFeishuClient())
    if (taskEntry && await handleFeishuTaskEntry()) {
      if (!initialOptions && !feishuRedirecting.value) await refreshAuthOptions()
      return
    }
    // The standalone frontend reads public settings from the API alongside identity verification.
    await Promise.all([
      initialOptions ? Promise.resolve() : refreshAuthOptions(),
      localStorage.getItem('yunwei_token') ? get('/me').then(result => { user.value = result }).catch(error => {
        if (error.status === 401) { expireSession(error.message); return }
        throw error
      }) : Promise.resolve(),
    ])
    // Identity is the only protected-data gate. Page requests render locally
    // and must not keep the entire application behind the startup screen.
    if (user.value) void loadSessionData()
  } catch (error) {
    // A network/5xx failure is recoverable; retain the token and requested route.
    startupError.value = error.status === 429 ? `${error.message} 登录状态已保留。` : '暂时无法连接平台，请重试。登录状态已保留。'
  } finally { restoringSession.value = feishuRedirecting.value }
}
function expireSession(reason) {
  ++loadRevision
  clearTimeout(listReloadTimer)
  localStorage.removeItem('yunwei_token')
  user.value = null
  profileOpen.value = false
  modal.value = ''
  detail.value = null
  deliveryAttachmentQueue.value = []
  deliveryUploadId.value = null
  const message = typeof reason === 'string' ? reason : reason?.detail?.message
  loginError.value = message || '登录已失效，请重新登录'
  // Keep the requested URL so signing in returns to the same page.
}
async function login() {
  loginError.value = ''
  saving.value = true
  try {
    const result = await post('/login', loginForm.value)
    localStorage.setItem('yunwei_token', result.token)
    user.value = result.user
    loginForm.value.password = ''
    void loadSessionData()
  } catch (error) { loginError.value = error.message }
  finally { saving.value = false }
}
function logout() {
  expireSession()
  loginError.value = ''
  window.history.pushState({}, '', '/')
}
function openProfile() {
  if (!user.value || saving.value) return
  sidebarOpen.value = false
  profileOpen.value = true
}
function applyProfile(result) {
  if (user.value?.id !== result.id) return
  user.value = result
  users.value = users.value.map(item => item.id === result.id ? result : item)
}
async function openDetail(kind, id, push = true) {
  if (kind === 'license') { navigate('licenses'); window.history.pushState({}, '', `/licenses/${id}`); window.dispatchEvent(new Event('popstate')); return }
  detailKind.value = kind
  deleteConfirm.value = false
  modal.value = 'detail'
  detail.value = null
  actionForm.value = { action: 'approve', comment: '', fulfillment: '', implementation_plan: '', rollback_plan: '' }
  if (push) window.history.pushState({}, '', `/${kind}/${id}`)
  try {
    const path = { resource: 'resources', change: 'changes', delivery: 'deliveries' }[kind]
    detail.value = await get(`/${path}/${id}`)
    if (hasRole('admin')) {
      try { users.value = await get('/users') }
      catch (error) { notify(error.message) }
    }
  } catch (error) { notify(error.message); modal.value = '' }
}
function requestDelete() {
  if (detail.value?.can_delete && !saving.value) deleteConfirm.value = true
}
async function deleteCurrentRequest() {
  if (!detail.value?.can_delete || saving.value) return
  saving.value = true
  try {
    const path = { resource: 'resources', change: 'changes', delivery: 'deliveries' }[detailKind.value]
    await del(`/${path}/${detail.value.id}`)
    notify('单据已删除，审批历史与操作日志已保留')
    deleteConfirm.value = false
    closeModal(true)
    await loadPage()
  } catch (error) { notify(error.message) }
  finally { saving.value = false }
}
async function deleteTaskRecord(task) {
  if (!task.can_delete || saving.value) return
  saving.value = true
  try {
    const path = {resource:'resources', change:'changes', delivery:'deliveries', license:'licenses'}[task.kind]
    await del(`/${path}/${task.object_id}`)
    taskDeleteConfirm.value = null
    notify('单据已删除，审批历史与操作日志已保留')
    await loadPage()
  } catch (error) { notify(error.message) }
  finally { saving.value = false }
}
async function reassignApproval(approval) {
  try {
    await post(`/admin/approvals/${approval.id}/reassign`, { assignee_id: approval.assignee_id, reason: approval.transfer_reason || '' })
    await openDetail(detailKind.value, detail.value.id, false)
    await loadPage()
    notify('当前审批已转交，通知记录已生成')
  } catch (error) { notify(error.message) }
}
function closeModal(force = false) {
  if (saving.value && modal.value === 'delivery' && force !== true) return
  if (modal.value === 'delivery' && force !== true) { deliveryAttachmentQueue.value = []; deliveryUploadId.value = null }
  if (modal.value === 'change' && force !== true) { changeAttachmentQueue.value = []; changeUploadId.value = null }
  resubmitId.value = null
  modal.value = ''
  detail.value = null
  deleteConfirm.value = false
  if (window.location.pathname.split('/').length > 2) window.history.pushState({}, '', `/${page.value}`)
}
function resetSpecificationValues(type = form.value.resource_type) {
  const item = activeResourceTypes.value.find(entry => entry.key === type)
  const values = {}
  for (const field of item?.fields || []) values[field.key] = ''
  form.value.specification_values = values
  form.value.specification = ''
}
function selectResourceType(type) {
  form.value.resource_type = type
  resetSpecificationValues(type)
}
async function loadResourceCatalog() {
  try {
    const items = (await get('/resource-catalog')).filter(item => item.active)
    resourceCatalog.value = items
    resourceCatalogError.value = ''
    if (!items.some(item => item.key === form.value.resource_type)) {
      form.value.resource_type = items[0]?.key || ''
      resetSpecificationValues(form.value.resource_type)
    } else if (!Object.keys(form.value.specification_values || {}).length) resetSpecificationValues()
  } catch (error) { resourceCatalogError.value = error.message }
}
function newForm(kind) {
  if ((saving.value && modal.value === 'delivery')) return
  resubmitId.value = null
  if (kind === 'delivery') { deliveryAttachmentQueue.value = []; deliveryUploadId.value = null }
  if (kind === 'change') { changeAttachmentQueue.value = []; changeUploadId.value = null }
  const today = new Date().toISOString().slice(0, 10)
  if (kind === 'resource') {
    form.value = { project_name: '', environment: '测试', resource_type: activeResourceTypes.value[0]?.key || 'ECS', specification: '', specification_values: {}, start_date: today, end_date: '', purpose: '', capacity_basis: '', ports: '', whitelist: '', reminder_enabled: true }
    void loadResourceCatalog()
  }
  if (kind === 'change') form.value = { name: '', environment: '生产', change_type: '配置变更', window_start: '', window_end: '', ops_owner_id: null, problem_description: '', plan: '', rollback_plan: '' }
  if (kind === 'delivery') form.value = { name: '', customer: '', cloud_type: '专有云', deployment_mode: '云平台', environment: 'POC', compute_type: '虚拟机', accelerator: '无', network_notes: '', db_whitelist: '', security_group: '', notes: '' }
  if (kind === 'user') { form.value = { username:'',display_name:'',department_id:null,position_ids:[],roles:['applicant'],password:'',feishu_open_id:'' }; void Promise.all([loadRoles(), loadDepartments()]).catch(error=>notify(error.message)) }
  if (['resource','change','delivery'].includes(kind)) { form.value.project_id=null; form.value.application_department_id=null; get('/projects').then(p=>projects.value=p.filter(project => project.can_apply !== false)).catch(e=>notify(e.message)) }
  modal.value = kind
  if (kind === 'change') get('/users').then(result => users.value = result).catch(() => {})
}
async function submitForm() {
  if (saving.value) return
  if (modal.value === 'change' && changeAttachmentErrors.value) { notify('请移除不支持、为空或超过大小限制的附件后再提交'); return }
  if (modal.value === 'delivery' && deliveryAttachmentErrors.value) { notify('请移除不支持、为空或超过大小限制的附件后再提交'); return }
  if (modal.value === 'user' && (!form.value.roles?.length || !memberPasswordValid.value)) { notify('请选择至少一个角色，并设置至少 12 位、包含至少三类字符的密码'); return }
  saving.value = true
  const kind = modal.value
  const identity = user.value?.id
  try {
    const paths = { resource: '/resources', change: '/changes', delivery: '/deliveries', user: '/users' }
    const result = resubmitId.value ? await post(`/${modal.value}/${resubmitId.value}/resubmit`, form.value) : await post(paths[modal.value], form.value)
    if (user.value?.id !== identity) return
    if (kind === 'delivery') deliveryUploadId.value = result.id
    if (kind === 'change') changeUploadId.value = result.id
    if (kind === 'user') organizationVersion.value++
    notify(kind === 'user' ? '成员已创建' : kind === 'delivery' && deliveryAttachmentQueue.value.length ? '交付单已创建，正在上传所选附件' : kind === 'change' && changeAttachmentQueue.value.length ? '变更单已创建，正在上传所选附件' : '申请已提交，审批通知已生成')
    closeModal(true)
    if (['resource', 'change', 'delivery'].includes(kind)) await openDetail(kind, result.id)
    await loadPage()
  } catch (error) { notify(error.message) }
  finally { saving.value = false }
}
async function submitAction() {
  saving.value = true
  try {
    detail.value = await post(`/${detailKind.value}/${detail.value.id}/action`, {...actionForm.value, task_id: detail.value.approvals.find(a=>a.action==='pending' && a.assignee_id===user.value.id)?.id})
    notify(actionForm.value.action === 'approve' ? '处理成功，已流转至下一环节' : '申请已驳回')
    actionForm.value = { action: 'approve', comment: '', fulfillment: '', implementation_plan: '', rollback_plan: '' }
    await loadPage()
  } catch (error) { notify(error.message) }
  finally { saving.value = false }
}
async function updateUser(item, changes) {
  try {
    await patch(`/users/${item.id}`, changes)
    notify('成员信息已更新')
    await loadPage()
    return true
  } catch (error) { notify(error.message) }
  return false
}
function editFeishu(item) {
  feishuDraft.value[item.id] = ''
  editingFeishu.value[item.id] = true
}
function cancelFeishu(item) {
  delete feishuDraft.value[item.id]
  delete editingFeishu.value[item.id]
}
async function saveFeishu(item) {
  const saved = await updateUser(item, { feishu_open_id: (feishuDraft.value[item.id] || '').trim() })
  if (saved) cancelFeishu(item)
}
function editUserRoles(item) {
  roleMember.value = item
  roleDraft.value = [...(item.direct_roles || item.roles)]
  modal.value = 'user-roles'
}
async function saveUserRoles() {
  if (!roleDraft.value.length) { notify('至少选择一个角色'); return }
  saving.value = true
  try {
    const result = await patch(`/users/${roleMember.value.id}`, { roles: roleDraft.value })
    if (result.id === user.value.id) user.value = result
    closeModal()
    await loadPage()
    notify('成员角色已保存')
  } catch (error) { notify(error.message) }
  finally { saving.value = false }
}
async function retryNotification(item) {
  try {
    await post(`/admin/notifications/${item.id}/retry`, {})
    await loadPage()
    notify('通知已重新安排发送')
  } catch (error) { notify(error.message) }
}
function canAct(item) {
  return item && ['pending','in_progress'].includes(item.status) && item.approvals?.some(a=>a.action==='pending' && a.assignee_id===user.value.id && a.eligible)
}
function editRejected() {
  const item = {...detail.value}
  const kind = detailKind.value
  newForm(kind)
  Object.keys(form.value).forEach(key => { if (key in item) form.value[key] = item[key] })
  if (kind === 'resource') {
    const snapshot = item.specification_values
    if (snapshot && Array.isArray(snapshot.fields)) {
      form.value.specification_values = Object.fromEntries(snapshot.fields.map(field => [field.key, field.value ?? '']))
    } else if (!snapshot || typeof snapshot !== 'object') {
      form.value.specification_values = {}
    }
  }
  resubmitId.value = item.id
}
const branchNames = steps => steps.flatMap(n=>n.type==='branch'?[n.name,...n.branches.flatMap(a=>branchNames(a.steps))]:[n.name]).join(' → ')
const currentNode = computed(()=>detail.value?.rounds?.at(-1)?.nodes.find(n=>n.status==='pending'))
const filteredData = computed(() => listPages.includes(page.value) ? data.value : data.value.filter(item => {
  const matchSearch = !search.value || JSON.stringify(item).toLowerCase().includes(search.value.toLowerCase())
  const matchStatus = filterStatus.value === 'all' || item.status === filterStatus.value
  return matchSearch && matchStatus
}))
const dateTime = value => value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'
const shortDate = value => value ? String(value).slice(0, 10) : '—'
const statusClass = status => ({ pending: 'amber', in_progress: 'blue', completed: 'green', rejected: 'red' })[status] || 'gray'
const kindPath = kind => ({ resource: 'resources', change: 'changes', delivery: 'deliveries' })[kind]

onMounted(async () => {
  window.addEventListener('yunwei-unauthorized', expireSession)
  window.addEventListener('popstate', () => { if (user.value) { routeFromPath(); loadPage() } })
  const params = new URLSearchParams(window.location.search)
  if (params.has('login_ticket') || params.has('login_error') || params.has('feishu_bound')) {
    loginError.value = '平台已改用账号密码登录，请输入平台账号和密码。'
    window.history.replaceState({}, '', window.location.pathname)
  }
  await restoreSession()
})
watch([page, authOptions], () => {
  if (authOptions.value.site_name) document.title = `${titles[page.value]} · ${authOptions.value.site_name}`
}, {immediate:true})
</script>

<template>
  <div v-if="restoringSession" class="session-pending" aria-busy="true" aria-label="正在恢复会话"></div>
  <div v-else-if="startupError" class="startup-shell">
    <div class="startup-card"><p role="alert">{{startupError}}</p><button class="primary-button" @click="restoreSession">重新连接</button></div>
  </div>
  <div v-else-if="!user" class="login-shell">
    <div class="login-art">
      <div class="login-art-grid"></div>
      <div class="login-brand"><div class="brand-mark"><Activity :size="20" :stroke-width="2.6" /></div><span>{{ authOptions.site_name }}</span></div>
      <div class="login-art-content">
        <div class="eyebrow light">OPERATIONS, IN CONTROL</div>
        <h1>让每一次项目协作<br>都有迹可循。</h1>
        <p>资源申请、变更管理、私有化交付和知识沉淀，<br>在一个清晰的工作空间里有序完成。</p>
        <div class="login-feature"><span><CircleCheck :size="17" /> 流程清晰</span><span><LockKeyhole :size="17" /> 凭据加密</span><span><ShieldCheck :size="17" /> 全程留痕</span></div>
      </div>
      <div class="login-art-bottom">YUNWEISHENJI <span>·</span> OPERATIONS PLATFORM</div>
    </div>
    <div class="login-panel">
      <div class="login-card">
        <div class="mobile-login-brand"><div class="brand-mark"><Activity :size="19" /></div> {{ authOptions.site_name }}</div>
        <div class="login-icon"><LockKeyhole :size="26" /></div>
        <div class="eyebrow">WELCOME BACK</div>
        <h2>登录工作空间</h2>
        <p class="muted">输入账号信息，继续你的项目协作</p>
        <form @submit.prevent="login" class="login-form">
          <label>用户名<input v-model="loginForm.username" :disabled="saving" :aria-describedby="loginError ? 'login-error' : undefined" autocomplete="username" required placeholder="请输入用户名" /></label>
          <label>密码<input v-model="loginForm.password" :disabled="saving" :aria-describedby="loginError ? 'login-error' : undefined" type="password" autocomplete="current-password" required placeholder="请输入密码" /></label>
          <div v-if="loginError" id="login-error" class="login-alert" role="alert" aria-atomic="true"><AlertCircle :size="17" /><p>{{ loginError }}</p></div>
          <button class="primary-button wide" :disabled="saving">{{ saving ? '登录中…' : '登录平台' }} <ArrowRight :size="18" /></button>
        </form>
        <button v-if="feishuTaskRetry" type="button" class="secondary-button feishu-login-button" :disabled="saving" @click="startFeishuTaskLogin">重新通过飞书进入任务</button>
      </div>
      <div class="login-footer">© 2026 {{ authOptions.site_name }} · 项目协作平台</div>
    </div>
  </div>

  <div v-else class="app-shell">
    <div v-if="sidebarOpen" class="mobile-shade" @click="sidebarOpen = false"></div>
    <aside class="sidebar" :class="{ 'sidebar-open': sidebarOpen }">
      <div class="sidebar-top">
        <div class="brand"><div class="brand-mark"><Activity :size="19" :stroke-width="2.6" /></div><div><strong>{{ authOptions.site_name }}</strong><small>PROJECT WORKSPACE</small></div></div>
        <button class="icon-button mobile-close" @click="sidebarOpen = false"><X :size="20" /></button>
      </div>
      <div class="workspace-pill"><div class="workspace-icon"><Cloud :size="18" /></div><div><strong>统一工作台</strong></div><ChevronDown :size="16" /></div>
      <nav class="nav-list">
        <div class="nav-group-label">总览</div>
        <button v-for="item in nav.filter(x => x.group === 'overview')" :key="item.key" class="nav-item" :class="{ active: page === item.key }" @click="navigate(item.key)"><component :is="item.icon" :size="18" /><span>{{ item.label }}</span></button>
        <div class="nav-group-label">工作管理</div>
        <button v-for="item in nav.filter(x => x.group === 'work')" :key="item.key" class="nav-item" :class="{ active: page === item.key }" @click="navigate(item.key)"><component :is="item.icon" :size="18" /><span>{{ item.label }}</span><b v-if="item.key === 'tasks' && taskTotal" class="nav-count">{{ taskTotal }}</b></button>
        <div class="nav-group-label">知识与规范</div>
        <button v-for="item in nav.filter(x => x.group === 'knowledge')" :key="item.key" class="nav-item" :class="{ active: page === item.key }" @click="navigate(item.key)"><component :is="item.icon" :size="18" /><span>{{ item.label }}</span></button>
        <template v-if="canManagePeople()"><div class="nav-group-label">系统管理</div><button v-for="item in nav.filter(x => x.group === 'admin' && (x.key === 'users' || hasRole('admin')))" :key="item.key" class="nav-item" :class="{ active: page === item.key }" @click="navigate(item.key)"><component :is="item.icon" :size="18" /><span>{{ item.label }}</span></button></template>
      </nav>
      <div class="sidebar-bottom"><div class="support-card"><div class="support-icon"><ShieldCheck :size="18" /></div><strong>安全合规，安心协作</strong><p>所有操作均有审计记录，重要凭据加密保存。</p></div><div class="profile-row"><button class="profile" title="个人资料与安全" @click="openProfile"><div class="avatar">{{ user.display_name?.slice(0, 1) }}</div><div><strong>{{ user.display_name }}</strong><small>个人资料与安全</small></div><ChevronRight :size="15" /></button><button class="icon-button profile-logout" title="退出登录" aria-label="退出登录" @click="logout"><LogOut :size="17" /></button></div></div>
    </aside>

    <div class="main-area">
      <header class="topbar"><div class="topbar-left"><button class="icon-button mobile-menu" @click="sidebarOpen = true"><Menu :size="22" /></button><span>工作空间</span><ChevronRight :size="15" /><strong>{{ titles[page] }}</strong></div><div class="topbar-right"><span class="system-live"><i></i> 系统运行中</span><button class="topbar-icon" title="通知记录" @click="navigate('notifications')"><Bell :size="19" /><span class="unread-dot"></span></button><button class="top-avatar profile-avatar" title="个人资料与安全" aria-label="打开个人资料与安全" @click="openProfile">{{ user.display_name?.slice(0, 1) }}</button></div></header>
      <main class="content">
        <div v-if="page !== 'wiki'" class="page-intro"><div><div class="eyebrow">{{ page === 'dashboard' ? 'OVERVIEW' : 'WORKSPACE / ' + page.toUpperCase() }}</div><h1>{{ titles[page] }}</h1><p>{{ subtitles[page] }}</p></div><div class="page-actions"><button v-if="page === 'resources'" class="primary-button" @click="newForm('resource')"><Plus :size="18" /> 新建资源申请</button><button v-if="page === 'changes'" class="primary-button" @click="newForm('change')"><Plus :size="18" /> 新建变更单</button><button v-if="page === 'deliveries'" class="primary-button" @click="newForm('delivery')"><Plus :size="18" /> 创建交付项目</button><button v-if="page === 'users' && hasRole('admin')" class="primary-button" @click="newForm('user')"><Plus :size="18" /> 添加成员</button></div></div>

        <template v-if="page === 'dashboard' && dashboard">
          <div class="welcome-banner"><div><span class="banner-kicker"><span class="banner-spark">✦</span> 工作空间概览</span><h2>你好，{{ user.display_name }} <span>👋</span></h2><p>把工作安排的井井有条</p><button @click="navigate('tasks')">查看待办事项 <ArrowRight :size="17" /></button></div><div class="banner-visual"><div class="visual-ring outer"></div><div class="visual-ring middle"></div><div class="visual-ring inner"></div><div class="visual-center"><Activity :size="35" /></div><div class="visual-float one"><CircleCheck :size="15" /> 流程清晰</div><div class="visual-float two"><ShieldCheck :size="15" /> 安全可控</div></div></div>
          <div class="metric-grid"><div class="metric-card"><div class="metric-icon blue"><Server :size="21" /></div><div class="metric-number">{{ dashboard.resource_count }}</div><div class="metric-label">资源申请</div><div class="metric-foot">云资源需求总数 <ArrowUpRight :size="15" /></div></div><div class="metric-card"><div class="metric-icon violet"><RefreshCw :size="21" /></div><div class="metric-number">{{ dashboard.change_count }}</div><div class="metric-label">变更工单</div><div class="metric-foot">计划内变更事项 <ArrowUpRight :size="15" /></div></div><div class="metric-card"><div class="metric-icon orange"><Boxes :size="21" /></div><div class="metric-number">{{ dashboard.delivery_count }}</div><div class="metric-label">交付项目</div><div class="metric-foot">私有化交付进度 <ArrowUpRight :size="15" /></div></div><div class="metric-card"><div class="metric-icon teal"><KeyRound :size="21" /></div><div class="metric-number">{{ dashboard.license_count }}</div><div class="metric-label">License 申请</div><div class="metric-foot">授权申请总数 <ArrowUpRight :size="15" /></div></div><div class="metric-card"><div class="metric-icon green"><CheckSquare :size="21" /></div><div class="metric-number">{{ dashboard.pending_count }}</div><div class="metric-label">进行中事项</div><div class="metric-foot">需要持续关注 <ArrowUpRight :size="15" /></div></div></div>
          <div class="dashboard-grid"><section class="panel activity-panel"><div class="panel-heading"><div><h3>最近动态</h3><p>近期提交的申请与项目</p></div><button class="text-button" @click="navigate('resources')">查看全部 <ArrowRight :size="15" /></button></div><div v-if="!dashboard.recent.length" class="empty-mini">还没有动态，开始提交第一条申请吧。</div><button v-for="item in dashboard.recent" :key="item.kind + item.id" class="activity-row" @click="openDetail(item.kind, item.id)"><div class="activity-icon" :class="item.kind"><component :is="item.kind === 'resource' ? Server : item.kind === 'change' ? RefreshCw : item.kind === 'license' ? KeyRound : Boxes" :size="18" /></div><div class="activity-main"><strong>{{ item.title }}</strong><span>{{ item.code }} · {{ kindLabel[item.kind] }}</span></div><span class="status-tag" :class="statusClass(item.status)"><i></i>{{ statusLabel[item.status] }}</span><span class="activity-date">{{ shortDate(item.created_at) }}</span><ChevronRight :size="16" class="faint" /></button></section><div class="right-panels"><section class="panel todo-panel"><div class="panel-heading"><div><h3>我的待办</h3><p>等待你处理的事项</p></div><span class="count-badge">{{ taskTotal }}</span></div><div v-if="!tasks.length" class="todo-empty"><CircleCheck :size="25" /><strong>暂时没有待办</strong><span>所有事项都已处理完成</span></div><button v-for="task in tasks.slice(0, 3)" :key="task.id" class="todo-row" @click="openDetail(task.kind, task.object_id)"><div class="todo-indicator"></div><div><strong>{{ task.title }}</strong><span>{{ task.code }} · {{ task.kind==='delivery' && task.stage==='operator' ? '交付执行' : stageLabel[task.stage] || task.stage }}</span></div><ArrowRight :size="16" /></button><button class="panel-bottom-link" @click="navigate('tasks')">进入审批中心 <ArrowRight :size="15" /></button></section><section class="panel resource-panel"><div class="panel-heading"><div><h3>资源类型分布</h3><p>当前申请的资源类别</p></div><SlidersHorizontal :size="17" class="muted-icon" /></div><div class="resource-bars"><div v-for="[type, count] in Object.entries(dashboard.resource_types).filter(([, count]) => count).slice(0, 5)" :key="type" class="resource-bar-row"><span>{{ type }}</span><div class="bar-track"><div class="bar-fill" :style="{ width: `${Math.max(12, count / Math.max(1, dashboard.resource_count) * 100)}%` }"></div></div><strong>{{ count }}</strong></div><div v-if="!dashboard.resource_count" class="empty-mini">暂无资源申请数据</div></div></section></div></div>
        </template>

        <template v-else-if="['resources', 'changes', 'deliveries'].includes(page)">
          <div class="section-summary"><div class="summary-icon" :class="page"><component :is="page === 'resources' ? Server : page === 'changes' ? RefreshCw : Boxes" :size="23" /></div><div><strong>{{ page === 'resources' ? '云资源全生命周期管理' : page === 'changes' ? '标准化变更流程' : '私有化交付全景' }}</strong><span>{{ page === 'resources' ? '从需求提出到资源开通，每一步都有负责人。' : page === 'changes' ? '提交方案、审批窗口、记录执行结果。' : '项目环境、网络配置与交付文档一处管理。' }}</span></div><div class="summary-stats"><span>共 <strong>{{ statusCounts.all }}</strong> 条记录</span><span>进行中 <strong>{{ statusCounts.pending + statusCounts.in_progress }}</strong></span></div></div>
          <section class="panel list-panel"><div class="list-toolbar"><div class="tab-strip" aria-label="按单据状态筛选"><button v-for="tab in listTabs" :key="tab.key" :class="{selected:filterStatus===tab.key}" :aria-pressed="filterStatus===tab.key" @click="filterStatus=tab.key">{{tab.label}} <span>{{statusCounts[tab.key]}}</span></button></div><select v-if="page === 'resources'" v-model="reminderFilter" aria-label="资源提醒筛选"><option value="all">全部提醒状态</option><option value="enabled">提醒已开启</option><option value="disabled">提醒已关闭</option></select><div class="search-box"><Search :size="17" /><input v-model="search" placeholder="搜索名称、编号、类型、发起人..." /></div></div><div class="table-wrap"><table class="data-table"><thead><tr><th>编号 / 名称</th><th>{{ page === 'resources' ? '资源类型' : page === 'changes' ? '变更类型' : '云环境' }}</th><th>{{ page === 'resources' ? '环境' : page === 'changes' ? '维护窗口' : '部署方式' }}</th><th>发起人</th><th>状态</th><th v-if="page === 'resources'">结束提醒</th><th>创建时间</th><th></th></tr></thead><tbody v-if="!loading"><tr v-for="item in filteredData" :key="item.id" @click="openDetail(page === 'resources' ? 'resource' : page === 'changes' ? 'change' : 'delivery', item.id)"><td><div class="table-title"><div class="table-type-icon" :class="page"><component :is="page === 'resources' ? typeIcons[item.resource_type] || Server : page === 'changes' ? RefreshCw : Boxes" :size="19" /></div><div><strong>{{ item.project_name || item.name }}</strong><small>{{ item.code }}</small></div></div></td><td>{{ page === 'resources' ? (resourceCatalog.find(type => type.key === item.resource_type)?.name || item.resource_type) : item.change_type || item.cloud_type }}</td><td>{{ page === 'resources' ? item.environment : page === 'changes' ? item.window_start?.replace('T', ' ') + ' -- ' + item.window_end?.replace('T', ' ') : item.deployment_mode }}</td><td>{{ item.applicant_name || item.owner_name }}</td><td><span class="status-tag" :class="statusClass(item.status)"><i></i>{{ statusLabel[item.status] }}</span></td><td v-if="page === 'resources'">{{ item.reminder_enabled ? '已开启' : '已关闭' }}</td><td class="table-date">{{ shortDate(item.created_at) }}</td><td><ChevronRight :size="17" class="faint" /></td></tr></tbody><tbody v-else><tr><td :colspan="page === 'resources' ? 8 : 7" class="list-loading" role="status">正在查询单据…</td></tr></tbody></table><div v-if="!loading && !filteredData.length" class="empty-state"><div class="empty-icon"><FileText :size="25" /></div><strong>暂无符合条件的记录</strong><p>调整筛选条件，或创建一条新申请。</p></div></div>
            <div class="pagination-bar" :aria-busy="loading"><div class="pagination-summary">共 <strong>{{listTotal}}</strong> 条<span v-if="listTotal"> · 第 {{listRangeStart}}–{{listRangeEnd}} 条</span></div><label class="page-size-control">每页<select v-model.number="listPageSize" aria-label="每页条数"><option v-for="size in [10,20,50]" :key="size" :value="size">{{size}}</option></select>条</label><nav class="pagination-controls" aria-label="单据分页"><button class="page-button" :disabled="loading || listPage<=1" @click="listPage--">上一页</button><template v-for="(n,index) in paginationNumbers" :key="index"><span v-if="n==='…'" class="page-ellipsis">…</span><button v-else class="page-button page-number" :class="{active:n===listPage}" :aria-current="n===listPage?'page':undefined" :disabled="loading" @click="listPage=n">{{n}}</button></template><button class="page-button" :disabled="loading || listPage>=listTotalPages" @click="listPage++">下一页</button></nav><span class="pagination-current">{{listPage}} / {{listTotalPages}} 页</span></div>
          </section>
        </template>

        <PersonalReminders v-if="page === 'personal-reminders'" :key="'personal-'+user.id+'-'+personalSharedId" :user="user" :shared-id="personalSharedId" @notice="notify" />
        <LicenseManagement v-else-if="page === 'licenses'" :key="'licenses-'+user.id" :user="user" @notice="notify" />
        <ProjectManagement v-else-if="page === 'projects'" :user="user" @notice="notify" /><SystemSettings v-else-if="page === 'settings'" @notice="notify" @saved="refreshAuthOptions().catch(e=>notify(e.message))" />
        <template v-else-if="page === 'tasks'"><div class="section-summary"><div class="summary-icon tasks"><CheckSquare :size="23" /></div><div><strong>审批中心</strong><span>仅显示分配给你的审批和回填任务，便于追踪处理责任。</span></div><div class="summary-stats"><span>分配给我 <strong>{{ taskTotal }}</strong> 项</span></div></div><section class="panel list-panel"><div class="panel-heading list-heading"><div><h3>分配给我的待办</h3><p>处理人、申请人和流程节点清晰可追溯</p></div></div><div v-if="!tasks.length && !loading" class="empty-state"><div class="empty-icon success"><CircleCheck :size="27" /></div><strong>待办已清空</strong><p>当前没有分配给你的审批或回填事项。</p></div><article v-for="task in tasks" :key="task.id" class="task-card"><button type="button" class="task-open" @click="openDetail(task.kind, task.object_id)"><div class="task-leading"><component :is="task.kind === 'resource' ? Server : task.kind === 'change' ? RefreshCw : task.kind === 'license' ? KeyRound : Boxes" :size="22" /></div><div class="task-main"><span class="task-kind">{{ kindLabel[task.kind] }} <span>·</span> {{ task.code }}</span><strong>{{ task.title }}</strong><small>申请人 {{ task.owner_name }} · 当前处理人 {{ task.assignee_name }} · {{ dateTime(task.created_at) }}</small></div><span class="stage-chip">{{ task.kind==='delivery' && task.stage==='operator' ? '交付执行' : stageLabel[task.stage] || task.stage }}</span></button><span v-if="taskDeleteConfirm===task.id" class="task-delete-confirm" @click.stop><small>确认删除？</small><button type="button" class="text-button muted" @click.stop="taskDeleteConfirm=null">取消</button><button type="button" class="danger-button task-danger-button" @click.stop="deleteTaskRecord(task)">确认</button></span><button v-else-if="task.can_delete" type="button" class="text-button task-delete-button" :disabled="saving" @click.stop="taskDeleteConfirm=task.id">删除</button><ArrowRight v-else :size="18" class="faint" /></article><div class="pagination-bar" :aria-busy="loading"><div class="pagination-summary">共 <strong>{{taskTotal}}</strong> 项待办<span v-if="taskTotal"> · 第 {{(taskPage-1)*taskPageSize+1}}–{{Math.min(taskPage*taskPageSize,taskTotal)}} 项</span></div><label class="page-size-control">每页<select v-model.number="taskPageSize" aria-label="审批每页条数"><option v-for="size in [10,20,50]" :key="size" :value="size">{{size}}</option></select>项</label><nav class="pagination-controls" aria-label="审批分页"><button class="page-button" :disabled="loading||taskPage<=1" @click="taskPage--">上一页</button><button v-for="n in taskPaginationNumbers" :key="n" class="page-button page-number" :class="{active:n===taskPage}" :aria-current="n===taskPage?'page':undefined" :disabled="loading" @click="taskPage=n">{{n}}</button><button class="page-button" :disabled="loading||taskPage>=taskTotalPages" @click="taskPage++">下一页</button></nav><span class="pagination-current">{{taskPage}} / {{taskTotalPages}} 页</span></div></section></template>

        <KnowledgeManagement v-else-if="page === 'wiki'" :key="'knowledge-'+user.id" :user="user" @notice="notify" />

        <AuditLog v-else-if="page === 'audit'" :key="'audit-'+user.id" />

        <template v-else-if="page === 'users'"><div class="org-main-tabs"><button class="secondary-button" :class="{active:usersTab==='members'}" @click="usersTab='members'">成员管理</button><button class="secondary-button" :class="{active:usersTab==='departments'}" @click="usersTab='departments'">部门管理</button></div><OrganizationManagement v-if="usersTab==='departments'" :key="organizationVersion" :user="user" :roles="roles" @notice="notify" @changed="organizationChanged" @create-member="createDepartmentMember" /><template v-else><div class="section-summary"><div class="summary-icon users"><Users :size="23" /></div><div><strong>按角色分配操作权限</strong><span>{{ hasRole('admin') ? '基础角色管理系统权限；审批与回填按组织负责人及项目岗位任命执行。' : '仅管理本部门职责范围内的成员岗位；其他角色由系统管理员维护。' }}</span></div><div class="summary-stats"><span>团队成员 <strong>{{ adminTotal }}</strong></span></div></div><section class="panel list-panel"><div class="panel-heading list-heading"><div><h3>成员列表</h3><p>部门负责人在部门管理中维护成员岗位；平台角色由管理员维护。</p></div></div><div class="admin-list-toolbar"><label class="search-box"><Search :size="16"/><input v-model="adminSearch" aria-label="搜索成员" placeholder="搜索姓名、用户名或部门"/></label><select v-if="hasRole('admin')" v-model="adminDepartment" aria-label="按部门筛选"><option value="">全部部门</option><option v-for="item in departmentDirectory" :key="item.name" :value="item.name">{{item.name}}</option></select><select v-model="adminRole" aria-label="按角色筛选"><option value="all">全部角色</option><option v-for="item in roles" :key="item.id" :value="item.id">{{item.name}}</option></select><select v-model="adminStatus" aria-label="按状态筛选"><option value="all">全部状态</option><option value="enabled">已启用</option><option value="disabled">已停用</option></select></div><div class="table-wrap"><table class="data-table"><thead><tr><th>成员</th><th>用户名</th><th>部门</th><th>角色</th><th v-if="hasRole('admin')">飞书通知 open_id</th><th>状态</th><th></th></tr></thead><tbody><tr v-for="item in users" :key="item.id"><td><div class="user-cell"><div class="mini-avatar">{{ item.display_name.slice(0, 1) }}</div><strong>{{ item.display_name }}</strong></div></td><td class="mono">{{ item.username }}</td><td>{{ item.departments?.map(d=>d.path).join('；') || item.department_path || item.department || '待分配' }}<small class="field-help" style="display:block">{{item.positions?.map(p=>p.department_name+' / '+p.name).join('、') || '未分配岗位'}}</small><button v-if="item.can_manage_organization" class="text-button" @click="organizationMember=item">调整组织岗位</button></td><td><div class="member-role-cell"><div class="member-role-tags"><span v-for="r in item.roles.slice(0,2)" :key="r" class="member-role-tag" :class="{admin:r==='admin'}" :title="item.role_sources?.[roleAlias[r] || r]?.join('、')">{{roleLabel[r] || r}}</span><span v-if="item.roles.length>2" class="member-role-more">+{{item.roles.length-2}}</span></div><button v-if="item.can_manage_roles" class="text-button" @click="editUserRoles(item)">管理角色</button></div></td><td v-if="hasRole('admin')"><div v-if="editingFeishu[item.id]" class="feishu-editor"><input v-model="feishuDraft[item.id]" class="inline-input" autocomplete="off" placeholder="粘贴完整 open_id" @keyup.enter="saveFeishu(item)" /><button class="text-button" @click="saveFeishu(item)">保存</button><button class="text-button muted" @click="cancelFeishu(item)">取消</button></div><div v-else class="feishu-display"><code v-if="item.feishu_open_id_set">{{ item.feishu_open_id }}</code><span v-else class="muted">未配置</span><button class="text-button" @click="editFeishu(item)">{{ item.feishu_open_id_set ? '修改' : '配置' }}</button></div></td><td><span class="status-tag" :class="item.active ? 'green' : 'gray'"><i></i>{{ item.active ? '启用' : '停用' }}</span></td><td><button v-if="hasRole('admin')" class="text-button" @click="updateUser(item, { active: !item.active })">{{ item.active ? '停用' : '启用' }}</button></td></tr></tbody></table></div><div class="pagination-bar"><div class="pagination-summary">共 <strong>{{adminTotal}}</strong> 位成员</div><label class="page-size-control">每页<select v-model.number="adminPageSize" aria-label="成员每页条数"><option v-for="size in [10,20,50]" :key="size" :value="size">{{size}}</option></select>条</label><nav class="pagination-controls" aria-label="成员分页"><button class="page-button" :disabled="loading||adminPage<=1" @click="adminPage--">上一页</button><template v-for="(n,index) in adminPaginationNumbers" :key="index"><span v-if="n==='…'" class="page-ellipsis">…</span><button v-else class="page-button page-number" :class="{active:n===adminPage}" @click="adminPage=n">{{n}}</button></template><button class="page-button" :disabled="loading||adminPage>=adminTotalPages" @click="adminPage++">下一页</button></nav><span class="pagination-current">{{adminPage}} / {{adminTotalPages}} 页</span></div></section></template></template>

        <template v-else-if="page === 'notifications'"><div class="section-summary"><div class="summary-icon notifications"><Bell :size="23" /></div><div><strong>消息发送记录</strong><span>飞书凭据和 open_id 配置后，流程流转时自动推送消息。</span></div></div><section class="panel list-panel"><div class="panel-heading list-heading"><div><h3>通知记录</h3><p>可查看待配置、成功与失败的发送状态</p></div></div><div class="admin-list-toolbar"><label class="search-box"><Search :size="16"/><input v-model="adminSearch" aria-label="搜索通知" placeholder="搜索接收人或通知内容"/></label><select v-model="adminStatus" aria-label="通知状态筛选"><option value="all">全部状态</option><option value="sent">已发送</option><option value="failed">失败</option><option value="unconfigured">待配置</option><option value="pending">排队中</option><option value="cancelled">已停止</option><option value="skipped">已跳过</option></select></div><div v-if="!data.length" class="empty-state">暂无通知记录</div><div v-for="item in data" :key="item.id" class="notification-row"><div class="notification-icon"><Bell :size="17" /></div><div><strong>{{ item.recipient_name }} <span>· {{ kindLabel[item.object_type] || item.object_type }}<template v-if="item.object_type!=='security'"> #{{ item.object_id }}</template></span></strong><p>{{ item.message }}</p><small v-if="item.error">{{ item.error }}</small><small v-if="item.next_attempt_at">已尝试 {{ item.attempt_count }} / 3 次 · 下次重试 {{ dateTime(item.next_attempt_at) }}</small><small v-else-if="item.status === 'pending' && item.dispatch_ready === false">历史待发送通知，可手动重试</small></div><span class="status-tag" :class="item.status === 'sent' ? 'green' : item.status === 'failed' ? 'red' : 'amber'"><i></i>{{ { sent: '已发送', failed: '失败', unconfigured: '待配置', pending: '排队中', cancelled: '已停止', skipped: '已跳过' }[item.status] }}</span><button v-if="hasRole('admin') && (['unconfigured', 'failed'].includes(item.status) || (item.status === 'pending' && item.dispatch_ready === false))" class="text-button" @click="retryNotification(item)">重试</button><span class="table-date">{{ dateTime(item.created_at) }}</span></div><div class="pagination-bar"><div class="pagination-summary">共 <strong>{{adminTotal}}</strong> 条通知</div><label class="page-size-control">每页<select v-model.number="adminPageSize" aria-label="通知每页条数"><option v-for="size in [10,20,50]" :key="size" :value="size">{{size}}</option></select>条</label><nav class="pagination-controls" aria-label="通知分页"><button class="page-button" :disabled="loading||adminPage<=1" @click="adminPage--">上一页</button><template v-for="(n,index) in adminPaginationNumbers" :key="index"><span v-if="n==='…'" class="page-ellipsis">…</span><button v-else class="page-button page-number" :class="{active:n===adminPage}" @click="adminPage=n">{{n}}</button></template><button class="page-button" :disabled="loading||adminPage>=adminTotalPages" @click="adminPage++">下一页</button></nav><span class="pagination-current">{{adminPage}} / {{adminTotalPages}} 页</span></div></section></template>

        <div class="content-footer"><span>© 2026 {{ authOptions.site_name }}</span></div>
      </main>
    </div>
  </div>

  <MemberOrganization v-if="organizationMember" :member="organizationMember" :user="user" :departments="departmentDirectory" @close="organizationMember=null" @saved="organizationSaved" @notice="notify" />
  <div v-if="modal" class="modal-backdrop" @click.self="closeModal"><div class="modal-panel" :class="{ 'detail-panel': modal === 'detail' || modal === 'article', 'member-panel': ['user','user-roles'].includes(modal) }"><div class="modal-head"><div><span class="eyebrow">{{ modal === 'detail' ? 'DETAILS' : modal === 'article' ? 'KNOWLEDGE BASE' : 'NEW ENTRY' }}</span><h2>{{ modal === 'detail' ? detail?.project_name || detail?.name || '加载中…' : modal === 'resource' ? '新建资源申请' : modal === 'change' ? '新建变更单' : modal === 'delivery' ? '创建交付项目' : modal === 'user-roles' ? '管理成员角色' : '添加成员' }}</h2></div><button class="icon-button" @click="closeModal"><X :size="21" /></button></div>
      <form v-if="['resource', 'change', 'delivery', 'user'].includes(modal)" class="modal-body form-body" @submit.prevent="submitForm()">
        <label v-if="['resource','change','delivery'].includes(modal)" class="field full"><span>关联项目 <b>*</b></span><select v-model.number="form.project_id" required><option :value="null" disabled>选择参与的项目</option><option v-for="p in projects.filter(p=>p.active)" :key="p.id" :value="p.id">{{p.name}} · {{p.department}}</option></select><small class="field-help">{{ hasRole('admin') ? '系统管理员可申请全部启用项目；审批按本次选择的申请部门匹配。' : '选择本人参与的项目；没有可选项目时请联系管理员。' }}</small></label>
        <ApplicationDepartment v-if="['resource','change','delivery'].includes(modal)" v-model="form.application_department_id" :user="user" />
        <template v-if="modal === 'resource'"><div class="form-section-title">基本信息</div><div class="form-grid"><label class="field"><span>运行环境 <b>*</b></span><select v-model="form.environment"><option>POC</option><option>测试</option><option>生产</option></select></label><label class="field"><span>资源类型 <b>*</b></span><select :value="form.resource_type" required @change="selectResourceType($event.target.value)"><option v-for="type in activeResourceTypes" :key="type.key" :value="type.key">{{ type.name }}</option></select><small v-if="selectedResourceType?.description" class="field-help">{{ selectedResourceType.description }}</small></label><div v-if="resourceCatalogError" class="field full catalog-inline-error" role="alert"><span>资源类型暂时无法加载：{{ resourceCatalogError }}</span><button type="button" class="text-button" @click="loadResourceCatalog">重试</button></div><div v-else-if="!activeResourceTypes.length" class="field full catalog-inline-error" role="alert">当前没有可用的资源类型，请联系系统管理员在系统配置中启用。</div><template v-for="field in selectedResourceType?.fields || []" :key="selectedResourceType.key + ':' + field.key"><label class="field" :class="{'full': field.type === 'textarea'}"><span>{{ field.label }} <b v-if="field.required">*</b></span><select v-if="field.type === 'select'" v-model="form.specification_values[field.key]" :required="field.required"><option value="">{{ field.placeholder || '请选择' }}</option><option v-for="option in field.options" :key="option" :value="option">{{ option }}</option></select><textarea v-else-if="field.type === 'textarea'" v-model="form.specification_values[field.key]" :required="field.required" rows="3" :placeholder="field.placeholder"></textarea><input v-else v-model="form.specification_values[field.key]" :type="field.type === 'number' ? 'number' : 'text'" :required="field.required" :placeholder="field.placeholder" :min="field.type === 'number' ? 0 : undefined" :step="field.type === 'number' ? 'any' : undefined" /></label></template><div v-if="selectedResourceType?.fields?.length" class="field full resource-spec-hint"><span>规格将按 {{ selectedResourceType.name }} 的字段保存，并在申请单中保留本次提交的名称和值。</span></div><label class="field"><span>开始日期 <b>*</b></span><input v-model="form.start_date" type="date" required /></label><label class="field"><span>结束日期 <b>*</b></span><input v-model="form.end_date" type="date" required /></label><div class="field full"><label class="reminder-create"><input v-model="form.reminder_enabled" type="checkbox" /> 开启资源结束飞书提醒</label><small class="field-help">结束前 7 天提醒申请人与本单据的运维执行人员，双方可在详情修改。</small></div></div><div class="form-section-title">申请说明</div><div class="form-grid"><label class="field full"><span>资源用途 <b>*</b></span><textarea v-model="form.purpose" required rows="3" placeholder="描述资源将用于什么业务或服务"></textarea></label><label class="field full"><span>容量依据 <b>*</b></span><textarea v-model="form.capacity_basis" required rows="3" placeholder="预计用户量、数据量、并发量等估算依据"></textarea></label><label class="field"><span>端口开通需求</span><input v-model="form.ports" placeholder="例如：443, 8080" /></label><label class="field"><span>白名单需求</span><input v-model="form.whitelist" placeholder="例如：10.0.0.0/24" /></label></div></template>
        <template v-if="modal === 'change'"><div class="form-section-title">变更信息</div><div class="form-grid"><label class="field full"><span>变更单名称 <b>*</b></span><input v-model="form.name" required placeholder="例如：生产环境网关证书更新" /></label><label class="field"><span>变更类型 <b>*</b></span><select v-model="form.change_type"><option>配置变更</option><option>发布部署</option><option>数据库变更</option><option>网络变更</option><option>紧急变更</option><option>其他</option></select></label><label class="field"><span>变更环境</span><select v-model="form.environment"><option>POC</option><option>测试</option><option>生产</option></select></label><label class="field"><span>运维负责人</span><select v-model.number="form.ops_owner_id"><option :value="null">待分配</option><option v-for="person in selectedProjectOperations" :key="person.id" :value="person.id">{{ person.display_name }}</option></select></label><label class="field"><span>维护窗口开始 <b>*</b></span><input v-model="form.window_start" type="datetime-local" required /></label><label class="field"><span>维护窗口结束 <b>*</b></span><input v-model="form.window_end" type="datetime-local" required /></label><label class="field full"><span>变更问题说明 <b>*</b></span><textarea v-model="form.problem_description" rows="4" required placeholder="说明变更背景、要解决的问题及影响范围"></textarea></label><div class="form-hint full">实施方案和回滚方案将在运维执行 / 回填阶段填写；审批阶段可先根据问题说明与附件评估风险。</div></div><DeliveryAttachments :upload-queue="changeAttachmentQueue" :disabled="saving" kind="change" /></template>
        <template v-if="modal === 'delivery'"><div class="form-section-title">项目与环境</div><div class="form-grid"><label class="field"><span>项目名称 <b>*</b></span><input v-model="form.name" required placeholder="私有化交付项目名称" /></label><label class="field"><span>客户名称 <b>*</b></span><input v-model="form.customer" required placeholder="客户或业务单位" /></label><label class="field"><span>云环境</span><select v-model="form.cloud_type"><option>专有云</option><option>公有云</option></select></label><label class="field"><span>交付阶段</span><select v-model="form.environment"><option>POC</option><option>正式</option></select></label><label class="field"><span>部署方式</span><select v-model="form.deployment_mode"><option>云平台</option><option>虚拟机</option></select></label><label class="field"><span>计算资源</span><select v-model="form.compute_type"><option>云主机</option><option>虚拟机</option><option>GPU</option></select></label><label class="field"><span>加速卡</span><select v-model="form.accelerator"><option>无</option><option>NVIDIA</option><option>NPU</option></select></label></div><div class="form-section-title">网络配置与说明</div><div class="form-grid"><label class="field full"><span>网络说明</span><textarea v-model="form.network_notes" rows="2" placeholder="网络拓扑、专线、VPC 与可达性"></textarea></label><label class="field"><span>数据库白名单</span><input v-model="form.db_whitelist" placeholder="允许访问的 IP / 网段" /></label><label class="field"><span>ECS 安全组</span><input v-model="form.security_group" placeholder="放行端口与规则" /></label><label class="field full"><span>备注</span><textarea v-model="form.notes" rows="2" placeholder="其他交付说明"></textarea></label></div><DeliveryAttachments :upload-queue="deliveryAttachmentQueue" :disabled="saving" /></template>
        <template v-if="modal === 'user'">
          <div class="member-form-intro"><div class="member-form-icon"><Users :size="22" /></div><div><strong>邀请新的协作成员</strong><p>设置账号信息、分配职责，完成后即可登录平台。</p></div></div>
          <div class="form-section-title">01 / 账号信息</div>
          <div class="form-grid"><label class="field"><span>用户名 <b>*</b></span><input v-model.trim="form.username" required minlength="2" maxlength="80" autocomplete="off" placeholder="设置登录用户名" /></label><label class="field"><span>姓名 <b>*</b></span><input v-model.trim="form.display_name" required maxlength="80" placeholder="成员真实姓名" /></label><div class="field"><span>所属部门</span><DepartmentPicker v-model="form.department_id" :departments="departmentDirectory.filter(d=>d.active)" required /><small class="field-help">所属部门由管理员在部门管理中统一维护。</small></div><label class="field"><span>飞书 open_id <small class="optional-label">选填</small></span><input v-model.trim="form.feishu_open_id" placeholder="本应用下的 open_id，用于接收通知" /></label></div>
          <fieldset v-if="selectedCreationDepartment?.positions?.length" class="department-responsibilities"><legend>部门岗位 · 可多选</legend><label v-for="p in selectedCreationDepartment.positions.filter(p=>p.active && p.source_department_active!==false)" :key="p.id" class="field-help" style="margin-right:16px"><input v-model="form.position_ids" type="checkbox" :value="p.id" /> {{p.name}}<small v-if="p.inherited">（来自{{p.source_department_name}}）</small></label></fieldset>
          <div class="form-section-title">02 / 分配角色</div><RolePicker v-model="form.roles" :roles="roles" :disabled="saving" />
          <div class="form-section-title">03 / 登录安全</div><PasswordField v-model="form.password" :disabled="saving" />
        </template>
        <div class="form-footer"><button type="button" class="secondary-button" @click="closeModal">取消</button><button class="primary-button" :disabled="saving || (modal === 'delivery' && deliveryAttachmentErrors) || (modal === 'change' && changeAttachmentErrors) || (modal === 'user' && (!form.roles?.length || !memberPasswordValid))">{{ saving ? '保存中…' : modal === 'user' ? '创建成员' : '提交申请' }} <ArrowRight :size="17" /></button></div>
      </form>

      <form v-else-if="modal === 'user-roles'" class="modal-body" @submit.prevent="saveUserRoles"><div class="member-form-intro"><div class="avatar">{{roleMember?.display_name.slice(0,1)}}</div><div><strong>{{roleMember?.display_name}}</strong><p>{{roleMember?.department || '未设置部门'}} · {{roleMember?.username}}</p></div></div><p class="field-help">此处维护直接授权。岗位带来的权限：{{Object.entries(roleMember?.role_sources || {}).filter(([,sources])=>sources.some(s=>s!=='直接授权')).map(([role,sources])=>(roleLabel[role] || role)+'（'+sources.filter(s=>s!=='直接授权').join('、')+'）').join('；') || '无'}}。部门负责人请在部门管理中任命。</p><RolePicker v-model="roleDraft" :roles="roleOptionsFor(roleMember)" :editable-ids="hasRole('admin') ? null : roleMember?.managed_roles || []" :disabled="saving" /><div class="form-footer"><button type="button" class="secondary-button" :disabled="saving" @click="closeModal">取消</button><button class="primary-button" :disabled="saving || !roleDraft.length">{{saving ? '保存中…' : '保存角色'}}</button></div></form>

      <div v-else-if="modal === 'detail' && detail" class="modal-body detail-body"><div class="detail-topline"><span class="code-label">{{ detail.code }}</span><span class="status-tag" :class="statusClass(detail.status)"><i></i>{{ statusLabel[detail.status] }}</span></div><div class="detail-section"><h3>基本信息</h3><div class="detail-grid"><template v-if="detailKind === 'resource'"><div><span>申请人</span><strong>{{ detail.owner_name || '—' }}</strong></div><div><span>运行环境</span><strong>{{ detail.environment }}</strong></div><div><span>资源类型</span><strong>{{ detail.specification_values?.type_name || resourceCatalog.find(type => type.key === detail.resource_type)?.name || detail.resource_type }}</strong></div><div class="full resource-spec-values"><span>资源规格</span><div v-if="detail.specification_values?.fields?.length" class="resource-spec-chips"><span v-for="field in detail.specification_values.fields" :key="field.key"><b>{{ field.label }}</b>{{ field.value }}</span></div><strong v-else>{{ detail.specification }}</strong></div><div><span>使用期限</span><strong>{{ detail.start_date }} 至 {{ detail.end_date }}</strong></div><div><span>端口需求</span><strong>{{ detail.ports || '无' }}</strong></div><div><span>白名单</span><strong>{{ detail.whitelist || '无' }}</strong></div><div class="full"><span>资源用途</span><strong>{{ detail.purpose }}</strong></div><div class="full"><span>容量依据</span><strong>{{ detail.capacity_basis }}</strong></div><div v-if="detail.fulfillment" class="full"><span>运维回填</span><strong>{{ detail.fulfillment }}</strong></div></template><template v-else-if="detailKind === 'change'"><div><span>变更类型</span><strong>{{ detail.change_type }}</strong></div><div><span>维护窗口</span><strong>{{ detail.window_start?.replace('T',' ') }} 至 {{ detail.window_end?.replace('T',' ') }}</strong></div><div class="full"><span>变更问题说明</span><strong>{{ detail.problem_description || '未填写' }}</strong></div><div v-if="detail.plan" class="full"><span>实施方案</span><strong>{{ detail.plan }}</strong></div><div v-if="detail.rollback_plan" class="full"><span>回滚方案</span><strong>{{ detail.rollback_plan }}</strong></div><div v-if="detail.implementation" class="full"><span>执行结果</span><strong>{{ detail.implementation }}</strong></div></template><template v-else><div><span>客户</span><strong>{{ detail.customer }}</strong></div><div><span>云环境 / 阶段</span><strong>{{ detail.cloud_type }} · {{ detail.environment }}</strong></div><div><span>部署方式</span><strong>{{ detail.deployment_mode }} · {{ detail.compute_type }}</strong></div><div><span>加速卡</span><strong>{{ detail.accelerator }}</strong></div><div><span>数据库白名单</span><strong>{{ detail.db_whitelist || '未填写' }}</strong></div><div><span>ECS 安全组</span><strong>{{ detail.security_group || '未填写' }}</strong></div><div class="full"><span>网络说明</span><strong>{{ detail.network_notes || '未填写' }}</strong></div><div v-if="detail.notes" class="full"><span>交付备注</span><strong>{{ detail.notes }}</strong></div></template></div></div>
        <DeliveryAttachments v-if="detailKind==='delivery'" :key="`${user.id}:${detail.id}`" :delivery-id="detail.id" :upload-queue="deliveryUploadId === detail.id ? deliveryAttachmentQueue : null" /><DeliveryAttachments v-if="detailKind==='change'" :key="`change-${user.id}:${detail.id}`" :delivery-id="detail.id" kind="change" :upload-queue="changeUploadId === detail.id ? changeAttachmentQueue : null" />
        <ExpiryReminder v-if="detailKind === 'resource'" :key="detail.id" kind="resource" :item-id="detail.id" :value="detail.reminder" @updated="value => { detail.reminder = value; detail.reminder_enabled = value.enabled }" /><div class="detail-section"><h3>审批流程与历史轮次</h3>
          <p v-if="detail.approval_department" class="field-help">审批归属部门：{{detail.approval_department}}<span v-if="detail.approval_department_source==='project'"> · 历史流程按关联项目所属部门匹配</span></p>
          <article v-for="round in detail.rounds" :key="round.id" class="round-card"><div class="flow-toolbar"><strong>第 {{round.round}} 轮 · {{round.version_id ? '流程版本 #'+round.version_id : '旧版流程迁移'}}</strong><span>{{statusLabel[round.status] || round.status}}</span></div>
            <p v-for="b in round.snapshot.branches" :key="b.id" class="field-help">{{b.name}}：<span v-for="arm in b.branches" :key="arm.id" :class="{muted:!arm.selected}">{{arm.name}}（{{arm.selected?'命中':'跳过'}}）：{{branchNames(arm.steps) || '直接汇合'}}； </span></p>
            <div v-for="node in round.nodes" :key="node.id" class="round-node"><div class="flow-toolbar"><b>{{node.position+1}}. {{node.name}}</b><span>{{node.mode==='all'?'全部同意（会签）':node.mode==='any'?'任意一人同意（或签）':`任意 ${node.config.required_count || 0} 人同意`}} · {{ {waiting:'未开始',pending:'待处理',completed:'已通过',rejected:'已驳回',closed:'已关闭',legacy_unrecorded:'旧版未记录'}[node.status] }} · {{node.tasks.filter(t=>t.action==='approve').length}}/{{node.config.assignees?.length || 0}}</span></div>
              <p v-if="!node.tasks.length" class="muted">{{node.config.assignees?.map(a=>a.name).join('、') || '旧版未记录处理人'}}</p>
              <div v-for="a in node.tasks" :key="a.id" class="round-task"><span>{{a.assignee_name}} · {{ {pending:'待处理',approve:'已通过',reject:'已驳回',closed:'已关闭'}[a.action] }} {{a.action==='pending' && !a.eligible?'（账号或权限变化，需转交）':''}}</span><p v-if="a.comment">{{a.comment}}</p><small>{{dateTime(a.acted_at || a.created_at)}}</small><div v-if="hasRole('admin') && a.action==='pending'" class="reassign-row"><select v-model.number="a.assignee_id" class="small-select"><option v-for="p in users.filter(p=>p.active && p.id !== (detail.applicant_id || detail.owner_id))" :key="p.id" :value="p.id">{{p.display_name}}</option></select><input v-model="a.transfer_reason" placeholder="转交原因（必填）" /><button class="text-button" @click="reassignApproval(a)">转交待办</button></div></div>
            </div>
          </article>
          <p v-for="t in detail.transfers" :key="t.id" class="field-help">任务 #{{t.task_id}} 转交：{{users.find(u=>u.id===t.from_user_id)?.display_name || t.from_user_id}} → {{users.find(u=>u.id===t.to_user_id)?.display_name || t.to_user_id}} · {{t.reason}}</p>
        </div>
        <div v-if="detail.status==='rejected' && (detail.applicant_id || detail.owner_id)===user.id" class="action-box"><h3>申请已退回修改</h3><p>修改后将按最新已发布流程从第一节点重新审批，历史轮次保留。</p><button class="primary-button" @click="editRejected">修改并重新提交</button></div>
        <form v-if="canAct(detail)" class="action-box" @submit.prevent="submitAction"><h3>{{currentNode?.name}}</h3><p>当前方式：{{currentNode?.mode==='all'?'全部同意（会签）':currentNode?.mode==='any'?'任意一人同意（或签）':`任意 ${currentNode?.config?.required_count || 0} 人同意`}}</p><label v-if="currentNode?.node_type==='execution'" class="field"><span>{{detailKind==='change' ? '变更执行结果' : '运维执行/交付结果'}} <b>*</b></span><textarea v-model="actionForm.fulfillment" rows="3" placeholder="填写操作结果、实例信息或交付说明"></textarea></label><label v-if="currentNode?.node_type==='execution' && detailKind==='change'" class="field"><span>实施方案 <b>*</b></span><textarea v-model="actionForm.implementation_plan" rows="3" required placeholder="填写本次实际执行步骤与关键操作"></textarea></label><label v-if="currentNode?.node_type==='execution' && detailKind==='change'" class="field"><span>回滚方案 <b>*</b></span><textarea v-model="actionForm.rollback_plan" rows="3" required placeholder="填写失败时的回退步骤与验证方式"></textarea></label><label class="field"><span>审批意见 / 备注</span><textarea v-model="actionForm.comment" rows="2"></textarea></label><div class="action-buttons"><button type="button" class="danger-button" :disabled="saving" @click="actionForm.action='reject';submitAction()">驳回修改</button><button class="primary-button" :disabled="saving" @click="actionForm.action='approve'">{{currentNode?.node_type==='execution'?(detailKind==='delivery'?'完成交付':'完成回填'):'同意'}} </button></div></form><div v-if="detail.can_delete" class="record-danger-zone"><div><strong>删除这张申请单</strong><p>删除后列表和详情将隐藏，审批历史和操作日志仍会保留。</p></div><template v-if="deleteConfirm"><span class="record-danger-question">确认删除 {{detail.code}}？</span><button class="secondary-button" :disabled="saving" @click="deleteConfirm=false">取消</button><button class="danger-button" :disabled="saving" @click="deleteCurrentRequest">{{saving?'删除中…':'确认删除'}}</button></template><button v-else class="danger-button" :disabled="saving" @click="requestDelete">删除单据</button></div></div>
      <div v-else class="modal-body empty-state">正在加载详情…</div>
    </div></div>
  <AccountSettings v-if="user && profileOpen" :key="user.id" :user="user" @close="profileOpen=false" @saved="applyProfile" @password-changed="loginForm.password=''" />
  <div v-if="toast" class="toast"><CircleCheck :size="18" />{{ toast }}</div>
</template>

<style scoped>
.profile-row{display:flex;align-items:center;gap:8px;border-top:1px solid #edf0f5;padding-top:16px}.profile-row .profile{min-width:0;flex:1;border:0;padding:4px;border-radius:8px}.profile-row .profile:hover{background:#f4f7ff}.profile-row .avatar{flex-shrink:0}.profile-row .profile strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.profile-row .profile-logout{flex-shrink:0;color:#9aa7ba}.profile-row .profile-logout:hover{color:#b65c61;background:#fff3f1}.profile-avatar{border:0;padding:0}.profile:focus-visible,.profile-avatar:focus-visible{outline:2px solid #6d89e7;outline-offset:3px}
.resource-spec-hint{margin-top:-8px;color:#8795aa;font-size:11px}.resource-spec-values{display:flex;flex-direction:column;gap:10px}.resource-spec-chips{display:flex;flex-wrap:wrap;gap:8px}.resource-spec-chips span{display:inline-flex;align-items:center;gap:6px;background:#f3f6fc;border:1px solid #e5eaf3;border-radius:8px;padding:7px 10px;color:#33445e;font-size:12px}.resource-spec-chips b{font-size:10px;color:#7e8ca3}.catalog-inline-error{display:flex;align-items:center;gap:10px;color:#b05b54;font-size:12px}
</style>
