<script setup>
import { computed, ref } from 'vue'
import { get, patch } from './api'

const props = defineProps({ kind: { type: String, required: true }, itemId: { type: Number, required: true }, value: Object })
const emit = defineEmits(['updated'])
const saving = ref(false), error = ref(''), notice = ref('')
const editingContacts = ref(false), candidates = ref([]), contactIds = ref([]), contactReason = ref('')
const endpoint = computed(() => `/${props.kind === 'license' ? 'licenses' : 'resource'}/${props.itemId}/reminder`)
const format = value => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '—'
const statuses = { sent: '已发送', pending: '排队中', failed: '发送失败', unconfigured: '待配置', cancelled: '已停止', skipped: '已跳过' }

async function toggle() {
  if (saving.value || !props.value?.can_edit) return
  saving.value = true; error.value = ''; notice.value = ''
  try {
    const result = await patch(endpoint.value, { enabled: !props.value.enabled, expected_revision: props.value.revision })
    emit('updated', result)
    notice.value = result.enabled ? '提醒已开启' : '本单据所有接收人的后续到期提醒已关闭'
  } catch (failure) {
    error.value = failure.message
    if (failure.status === 409) {
      try { emit('updated', await get(endpoint.value)) } catch { /* Keep the conflict visible. */ }
    }
  } finally { saving.value = false }
}

async function editContacts() {
  error.value = ''; saving.value = true
  try {
    candidates.value = await get(endpoint.value + '/contacts')
    contactIds.value = props.value.recipients.filter(p => candidates.value.some(c => c.id === p.id)).map(p => p.id)
    editingContacts.value = true
  } catch (failure) { error.value = failure.message }
  finally { saving.value = false }
}
async function saveContacts() {
  if (!contactIds.value.length || !contactReason.value.trim()) return
  saving.value = true; error.value = ''
  try {
    emit('updated', await patch(endpoint.value + '/contacts', { user_ids: contactIds.value, reason: contactReason.value.trim() }))
    editingContacts.value = false; contactReason.value = ''; notice.value = '历史运维责任人已保存，操作已记录'
  } catch (failure) { error.value = failure.message }
  finally { saving.value = false }
}
</script>

<template>
  <div v-if="value" class="detail-section expiry-reminder">
    <div class="expiry-reminder-heading"><h3>{{ kind === 'license' ? 'License 到期提醒' : '资源结束提醒' }}</h3><button v-if="value.can_edit" type="button" class="expiry-reminder-switch" role="switch" :aria-checked="value.enabled" :aria-label="kind === 'license' ? 'License 到期提醒' : '资源结束提醒'" :disabled="saving" @click="toggle"><span :class="{ on: value.enabled }"></span>{{ saving ? '保存中…' : value.enabled ? '已开启' : '已关闭' }}</button><span v-else class="status-tag" :class="value.enabled ? 'green' : 'amber'">{{ value.enabled ? '已开启' : '已关闭' }}</span></div>
    <p class="field-help">通过飞书提醒 · {{ value.rules.join('、') }}{{ kind === 'resource' ? '，北京时间 09:00' : '，1 个月按自然月计算' }}</p>
    <p class="field-help">关闭后，本单据所有接收人将停止收到后续到期提醒。</p>
    <p v-if="!value.channel_enabled" class="expiry-reminder-warning">系统飞书通知尚未启用，请联系管理员配置。</p>
    <div class="expiry-reminder-grid"><div><span>提醒接收人</span><strong v-for="person in value.recipients" :key="person.id">{{ person.name }}<small v-if="!person.active">（账号已停用）</small><small v-else-if="!person.role_valid">（运维权限已变更）</small><small v-else-if="!person.configured">（未配置飞书）</small></strong><small v-if="kind === 'resource' && !value.effective && !value.missing_operator">运维执行后确定实际执行人员</small><small v-if="kind === 'license' && !value.effective">最终审批后确定最后审批人</small></div><div><span>下次提醒（北京时间）</span><strong>{{ !value.enabled ? '已关闭' : value.next_at ? format(value.next_at) : value.effective ? '本周期提醒已处理' : '待生效或使用期已结束' }}</strong></div></div>
    <p v-if="value.missing_operator" class="expiry-reminder-warning">历史单据缺少可识别的运维执行人，申请人提醒正常；请运维负责人核对补录。</p>
    <button v-if="value.can_fill_contacts && !editingContacts" type="button" class="text-button" :disabled="saving" @click="editContacts">补录 / 修改历史运维责任人</button>
    <div v-if="editingContacts" class="expiry-reminder-contact-form"><label v-for="person in candidates" :key="person.id"><input v-model="contactIds" type="checkbox" :value="person.id" :disabled="saving" /> {{ person.name }}</label><p v-if="!candidates.length" class="field-help">暂无已启用的运维人员，请先配置账号角色。</p><label class="field"><span>补录依据</span><input v-model="contactReason" maxlength="500" placeholder="填写已核对的历史执行依据" :disabled="saving" /></label><div><button type="button" class="secondary-button" :disabled="saving" @click="editingContacts = false">取消</button> <button type="button" class="primary-button" :disabled="saving || !contactIds.length || !contactReason.trim()" @click="saveContacts">保存责任人</button></div></div>
    <p v-if="value.updated_at" class="field-help">最近修改：{{ value.updated_by_name }} · {{ format(value.updated_at) }}</p>
    <p v-if="error" class="expiry-reminder-warning" role="alert">{{ error }}</p><p v-if="notice" class="field-help" role="status">{{ notice }}</p>
    <details v-if="value.records.length" class="expiry-reminder-records"><summary>查看提醒记录（最近 {{ value.records.length }} 条）</summary><div v-for="record in value.records" :key="record.id"><strong>{{ record.rule }} · {{ record.recipient_name }}</strong><span>{{ statuses[record.status] || record.status }} · {{ format(record.sent_at || record.created_at) }}</span><small v-if="record.error">{{ record.error }}</small></div></details>
  </div>
</template>

<style scoped>
.expiry-reminder-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 12px; }
.expiry-reminder-heading h3 { margin: 0; }
.expiry-reminder-switch { display: inline-flex; align-items: center; gap: 8px; padding: 7px 10px; border: 1px solid #dce3ed; border-radius: 8px; background: #fff; color: #334155; cursor: pointer; }
.expiry-reminder-switch:disabled { opacity: .6; cursor: wait; }
.expiry-reminder-switch > span { position: relative; width: 30px; height: 18px; border-radius: 12px; background: #94a3b8; }
.expiry-reminder-switch > span::after { content: ''; position: absolute; top: 3px; left: 3px; width: 12px; height: 12px; border-radius: 50%; background: #fff; }
.expiry-reminder-switch > span.on { background: #2563eb; }
.expiry-reminder-switch > span.on::after { left: 15px; }
.expiry-reminder-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin: 16px 0; }
.expiry-reminder-grid > div { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
.expiry-reminder-grid > div > span { color: #64748b; }
.expiry-reminder-grid strong { font-weight: 500; overflow-wrap: anywhere; }
.expiry-reminder-grid small, .expiry-reminder-records small { color: #64748b; }
.expiry-reminder-warning { font-size: 13px; color: #b45309; }
.expiry-reminder-records { margin-top: 14px; font-size: 13px; }
.expiry-reminder-records summary { cursor: pointer; color: #475569; }
.expiry-reminder-records > div { display: flex; flex-direction: column; gap: 5px; padding: 12px 0; border-bottom: 1px solid #eef2f6; }
.expiry-reminder-contact-form { display: grid; gap: 12px; margin: 12px 0; padding: 14px; border: 1px solid #e2e8f0; border-radius: 8px; }
.expiry-reminder-contact-form > label { display: flex; align-items: center; gap: 7px; font-size: 13px; }
.expiry-reminder-contact-form input[type=checkbox] { width: 16px; height: 16px; min-height: 0; margin: 0; }
@media (max-width: 600px) { .expiry-reminder-grid { grid-template-columns: 1fr; } }
</style>
