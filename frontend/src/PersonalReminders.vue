<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { Bell, CalendarDays, ChevronRight, Plus, RefreshCw, X } from 'lucide-vue-next'
import { del, get, post, put } from './api'
import DeliveryAttachments from './DeliveryAttachments.vue'

const props = defineProps({ user: { type: Object, required: true }, sharedId: { type: Number, default: null } })
const emit = defineEmits(['notice'])
const items = ref([]), people = ref([]), loading = ref(false), error = ref(''), saving = ref(false)
const page = ref(1), total = ref(0), pages = ref(1), search = ref(''), detail = ref(null), formOpen = ref(false)
const content = ref(''), remindAt = ref(''), selectedIds = ref([]), uploadBusy = ref(false), deleting = ref(false)
const attachmentBlocked = ref(false)
const formError = ref(''), dialog = ref(null), contentInput = ref(null)
let disposed = false, revision = 0, previousFocus
const labels = { draft: '草稿', scheduled: '待提醒', dispatched: '已触发', cancelled: '已取消' }
const deliveryLabels = { scheduled: '等待提醒', pending: '发送排队中', sent: '已发送', failed: '发送失败', unconfigured: '待配置', cancelled: '已停止', skipped: '已跳过' }
const editing = computed(() => !detail.value || detail.value.state === 'draft')
const filteredPeople = computed(() => people.value.filter(p => `${p.name} ${p.department}`.includes(search.value.trim())))
const self = computed(() => people.value.find(p => p.id === props.user.id))
const selectedNames = computed(() => selectedIds.value.map(id => people.value.find(p => p.id === id)?.name || detail.value?.recipients.find(p => p.id === id)?.name || '原成员').join('、'))
const valid = computed(() => content.value.trim() && remindAt.value && selectedIds.value.length && !saving.value && !uploadBusy.value)
const dateTime = value => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
const inputTime = value => new Date(new Date(value).getTime() + 8 * 3600000).toISOString().slice(0, 16)
const minTime = computed(() => inputTime(new Date(Date.now() + 60000).toISOString()))

async function load() {
  const current = ++revision
  loading.value = true; error.value = ''
  try {
    const [result, candidates] = await Promise.all([get(`/personal-reminders?page=${page.value}`), get('/personal-reminders/people')])
    if (disposed || current !== revision) return
    items.value = result.items; total.value = result.total; pages.value = result.total_pages; page.value = result.page
    people.value = candidates
  } catch (e) { if (!disposed && current === revision) error.value = e.message }
  finally { if (!disposed && current === revision) loading.value = false }
}
function focusForm() {
  nextTick(() => contentInput.value?.focus())
}
function openNew() {
  previousFocus = document.activeElement
  detail.value = null; content.value = ''; remindAt.value = ''; search.value = ''; formError.value = ''
  attachmentBlocked.value = false
  selectedIds.value = self.value ? [props.user.id] : []; formOpen.value = true; deleting.value = false; focusForm()
}
async function openItem(id) {
  if (saving.value) return
  formError.value = ''; saving.value = true
  try {
    const result = await get(`/personal-reminders/${id}`)
    if (disposed) return
    previousFocus = document.activeElement
    detail.value = result; content.value = result.content; remindAt.value = inputTime(result.remind_at)
    selectedIds.value = result.recipients.map(p => p.id); search.value = ''; formOpen.value = true; deleting.value = false; focusForm()
    attachmentBlocked.value = false
  } catch (e) { if (!disposed) emit('notice', e.message) }
  finally { if (!disposed) saving.value = false }
}
function close() {
  if (saving.value || uploadBusy.value) return
  formOpen.value = false; detail.value = null; formError.value = ''; deleting.value = false
  if (previousFocus?.isConnected) previousFocus.focus()
}
function keydown(event) {
  if (event.key === 'Escape') { event.stopPropagation(); close() }
  if (event.key === 'Tab' && dialog.value) {
    const controls = [...dialog.value.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled)')].filter(el => el.getClientRects().length)
    const first = controls[0], last = controls.at(-1)
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
  }
}
function payload() {
  return { content: content.value.trim(), remind_at: `${remindAt.value}:00+08:00`, recipient_ids: selectedIds.value }
}
async function save(activate = false) {
  if (!valid.value) return
  if (activate && attachmentBlocked.value) { formError.value = '请先完成附件上传，或清空失败记录后再启用提醒'; return }
  formError.value = ''
  if (new Date(payload().remind_at).getTime() <= Date.now()) { formError.value = '请选择未来的提醒时间（北京时间）'; return }
  saving.value = true
  try {
    const result = detail.value ? await put(`/personal-reminders/${detail.value.id}`, payload()) : await post('/personal-reminders', payload())
    if (disposed) return
    detail.value = result
    if (activate) {
      detail.value = await post(`/personal-reminders/${result.id}/activate`, {})
      if (disposed) return
      emit('notice', '定时提醒已启用')
    } else emit('notice', '草稿已保存，可添加附件后启用提醒')
    await load()
  } catch (e) { if (!disposed) formError.value = e.message }
  finally { if (!disposed) saving.value = false }
}
async function cancel() {
  if (saving.value || !detail.value) return
  saving.value = true; formError.value = ''
  try { const result = await post(`/personal-reminders/${detail.value.id}/cancel`, {}); if (disposed) return; detail.value = result; await load(); emit('notice', '提醒已取消，已发出的消息不会撤回') }
  catch (e) { if (!disposed) formError.value = e.message }
  finally { if (!disposed) saving.value = false }
}
async function remove() {
  if (saving.value || uploadBusy.value || !detail.value) return
  saving.value = true; formError.value = ''
  try { await del(`/personal-reminders/${detail.value.id}`); if (disposed) return; formOpen.value = false; detail.value = null; await load(); emit('notice', '提醒已删除') }
  catch (e) { if (!disposed) formError.value = e.message }
  finally { if (!disposed) saving.value = false }
}
async function retry(person) {
  if (saving.value || !detail.value) return
  saving.value = true; formError.value = ''
  try { const result = await post(`/personal-reminders/${detail.value.id}/retry/${person.id}`, {}); if (disposed) return; detail.value = result; emit('notice', '已安排重试') }
  catch (e) { if (!disposed) formError.value = e.message }
  finally { if (!disposed) saving.value = false }
}
onMounted(() => { if (!props.sharedId) load() })
onBeforeUnmount(() => { disposed = true; ++revision })
</script>

<template>
  <section v-if="sharedId" class="panel shared-reminder-files">
    <h2><Bell :size="20" />个人提醒附件</h2><p>仅提醒创建人与指定的被提醒人可访问。</p>
    <DeliveryAttachments :delivery-id="sharedId" kind="personal_reminder" />
  </section>
  <template v-else>
    <div class="section-summary"><div class="summary-icon notifications"><Bell :size="23" /></div><div><strong>为重要事项定时提醒</strong><span>只有你能管理自己创建的提醒，到时通过飞书通知指定成员。</span></div><button type="button" class="primary-button" :disabled="loading" @click="openNew"><Plus :size="17" />新建提醒</button></div>
    <p v-if="error" class="personal-error" role="alert">{{error}} <button type="button" class="text-button" @click="load">重试</button></p>
    <section class="panel list-panel" :aria-busy="loading">
      <div class="panel-heading list-heading"><div><h3>我创建的提醒</h3><p>一次性发送 · 北京时间 · 已启用的提醒可取消</p></div><button type="button" class="text-button" :disabled="loading" @click="load"><RefreshCw :size="15" />刷新</button></div>
      <p v-if="loading" class="personal-empty" role="status">正在加载提醒…</p>
      <div v-else-if="!items.length" class="empty-state"><div class="empty-icon"><Bell :size="26" /></div><strong>还没有个人提醒</strong><p>写下要提醒的事情，选择时间和提醒对象。</p></div>
      <button v-for="item in items" v-else :key="item.id" type="button" class="personal-row" :disabled="saving" @click="openItem(item.id)"><div class="personal-leading"><CalendarDays :size="21" /></div><div class="personal-main"><strong>{{item.content}}</strong><small>{{dateTime(item.remind_at)}} · 北京时间</small></div><span class="status-tag" :class="item.state === 'scheduled' ? 'blue' : item.state === 'dispatched' ? 'green' : 'gray'">{{labels[item.state]}}</span><ChevronRight :size="17" /></button>
      <div class="pagination-bar"><div class="pagination-summary">共 <strong>{{total}}</strong> 条提醒</div><nav class="pagination-controls" aria-label="提醒分页"><button class="page-button" :disabled="loading || page <= 1" @click="page--; load()">上一页</button><span>{{page}} / {{pages}}</span><button class="page-button" :disabled="loading || page >= pages" @click="page++; load()">下一页</button></nav></div>
    </section>
    <Teleport to="body"><div v-if="formOpen" class="modal-backdrop" @click.self="close" @keydown="keydown"><section ref="dialog" class="modal-panel personal-dialog" role="dialog" aria-modal="true" aria-labelledby="personal-reminder-title">
      <header class="modal-head"><div><h2 id="personal-reminder-title">{{detail ? labels[detail.state] + ' · 个人提醒' : '新建个人提醒'}}</h2><p>提醒内容仅你管理，飞书消息发送给选定成员。</p></div><button type="button" class="icon-button" :disabled="saving || uploadBusy" aria-label="关闭个人提醒" @click="close"><X :size="21" /></button></header>
      <form class="modal-body personal-form" @submit.prevent="save(false)">
        <label class="field"><span>提醒内容 <b>*</b></span><textarea ref="contentInput" v-model="content" rows="5" maxlength="4000" required :disabled="!editing || saving" placeholder="写下需要提醒的事情…" /><small class="field-help">{{content.length}} / 4000</small></label>
        <label class="field"><span>提醒时间（北京时间） <b>*</b></span><input v-model="remindAt" type="datetime-local" :min="minTime" required :disabled="!editing || saving" /></label>
        <fieldset class="personal-recipients" :disabled="!editing || saving"><legend>被提醒人 <b>*</b></legend><div v-if="editing" class="personal-person-tools"><button v-if="self" type="button" class="text-button" @click="selectedIds=[user.id]">只提醒自己</button><span v-else class="field-help">你的账号尚未配置飞书 ID，不能选择自己。</span><input v-model="search" placeholder="搜索姓名或部门" aria-label="搜索提醒对象" /></div><p v-if="editing" class="field-help">只能选择已启用并配置飞书 ID 的成员。</p><div v-if="editing" class="personal-people"><label v-for="person in filteredPeople" :key="person.id"><input v-model="selectedIds" type="checkbox" :value="person.id" /><span><strong>{{person.name}}{{person.is_self ? '（自己）' : ''}}</strong><small>{{person.department || '未分配部门'}}</small></span></label><p v-if="!filteredPeople.length" class="field-help">没有符合条件的成员。</p></div><p class="personal-selected">已选 {{selectedIds.length}} 人：{{selectedNames || '请选择提醒对象'}}</p></fieldset>
        <div v-if="detail && detail.state === 'draft'" class="personal-files"><DeliveryAttachments :key="detail.id" :delivery-id="detail.id" kind="personal_reminder" :disabled="saving" @busy="uploadBusy=$event" @blocked="attachmentBlocked=$event" /></div>
        <p v-else-if="!detail" class="form-hint">先保存草稿，即可添加附件；确认内容和附件后启用定时提醒。</p>
        <DeliveryAttachments v-else :key="detail.id + ':' + detail.state" :delivery-id="detail.id" kind="personal_reminder" />
        <div v-if="detail?.state === 'dispatched'" class="personal-deliveries"><h3>发送状态</h3><div v-for="person in detail.recipients" :key="person.id"><strong>{{person.name}}</strong><span>{{deliveryLabels[person.status] || person.status}}</span><small v-if="person.error">{{person.error}}</small><button v-if="person.can_retry" type="button" class="text-button" :disabled="saving" @click="retry(person)">重试</button></div></div>
        <p v-if="formError" class="personal-error" role="alert">{{formError}}</p>
        <footer class="personal-footer"><template v-if="editing"><button type="submit" class="secondary-button" :disabled="!valid">{{saving ? '保存中…' : '保存草稿'}}</button><button v-if="detail" type="button" class="primary-button" :disabled="!valid || attachmentBlocked" @click="save(true)">启用定时提醒</button></template><button v-else-if="detail.state !== 'cancelled'" type="button" class="secondary-button" :disabled="saving" @click="cancel">取消后续发送</button><span v-if="detail" class="personal-delete"><template v-if="deleting"><span>确认删除？</span><button type="button" class="text-button" :disabled="saving" @click="deleting=false">返回</button><button type="button" class="danger-button" :disabled="saving || uploadBusy" @click="remove">确认删除</button></template><button v-else type="button" class="text-button" :disabled="saving || uploadBusy" @click="deleting=true">删除提醒</button></span></footer>
      </form>
    </section></div></Teleport>
  </template>
</template>

<style scoped>
.section-summary>.primary-button{margin-left:auto;flex-shrink:0}.personal-row{display:flex;align-items:center;gap:16px;width:100%;text-align:left;border:0;border-top:1px solid #edf1f6;padding:20px 24px;background:white;color:#53637b;cursor:pointer}.personal-row:hover{background:#f8faff}.personal-leading{padding:11px;background:#edf3ff;border-radius:10px;color:#6c88cd}.personal-main{flex:1;min-width:0}.personal-main strong{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-size:13px;line-height:1.7;white-space:pre-wrap;overflow-wrap:anywhere}.personal-main small{display:block;margin-top:6px;color:#8795aa;font-size:11px}.personal-empty{padding:36px;text-align:center;color:#7b8aa1}.personal-error{background:#fff3f2;color:#b04e57;padding:12px 16px;border:1px solid #f0d5d7;border-radius:8px;font-size:12px;line-height:1.7}.personal-dialog{max-width:760px}.personal-dialog .modal-head p{font-size:12px;line-height:1.7;color:#7f90a8;margin:8px 0 0}.personal-form{display:grid;gap:20px}.personal-recipients{min-width:0;margin:0;padding:16px;border:1px solid #e0e7f1;border-radius:10px}.personal-recipients legend{font-size:12px;color:#52647f;padding:0 6px}.personal-recipients b{color:#d8757c}.personal-person-tools{display:flex;align-items:center;justify-content:space-between;gap:12px}.personal-person-tools input{max-width:240px;min-width:0;width:100%;padding:9px 11px;border:1px solid #dce5f3;border-radius:8px;background:#fafcff;font-size:12px;color:#52647f}.personal-people{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;max-height:220px;overflow:auto;padding:4px}.personal-people label{display:flex;align-items:center;gap:9px;padding:9px;border-radius:7px;background:#f8faff;font-size:12px}.personal-people input{width:16px;height:16px;min-height:0;padding:0;margin:0}.personal-people strong,.personal-people small{display:block}.personal-people small{font-size:10px;color:#8a99ae;margin-top:4px}.personal-selected{font-size:12px;line-height:1.8;overflow-wrap:anywhere;margin:12px 0 0;color:#657c9e}.personal-files{min-width:0}.personal-footer{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding-top:12px;border-top:1px solid #e7edf5}.personal-delete{margin-left:auto;display:flex;align-items:center;gap:8px;font-size:12px;color:#8d6670}.personal-deliveries h3{font-size:13px}.personal-deliveries>div{display:flex;align-items:center;gap:12px;flex-wrap:wrap;border-bottom:1px solid #edf1f6;padding:10px 0;font-size:12px}.personal-deliveries small{color:#b04e57}.shared-reminder-files{padding:24px}.shared-reminder-files h2{display:flex;align-items:center;gap:10px;font-size:18px}.shared-reminder-files>p{color:#7b8ba2;font-size:12px}.pagination-controls span{font-size:12px;padding:0 12px}@media(max-width:600px){.personal-row{padding:16px 12px;gap:10px}.personal-leading{display:none}.personal-people{grid-template-columns:1fr}.personal-person-tools{align-items:stretch;flex-direction:column}.personal-person-tools input{max-width:none}.section-summary{flex-wrap:wrap}.section-summary>.primary-button{margin-left:0}.personal-footer>.primary-button{flex:1}.personal-delete{width:100%;justify-content:flex-end}.shared-reminder-files{padding:16px}}
</style>
