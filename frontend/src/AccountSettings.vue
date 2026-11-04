<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CircleCheck, LockKeyhole, UserRound, X } from 'lucide-vue-next'
import { patch, post } from './api'
import { validPassword } from './password-policy'
import PasswordField from './PasswordField.vue'

const props = defineProps({ user: { type: Object, required: true } })
const emit = defineEmits(['close', 'saved', 'password-changed'])
const tab = ref('profile'), displayName = ref(props.user.display_name)
const passwords = ref({ current: '', next: '', confirm: '' })
const saving = ref(false), error = ref(''), success = ref('')
const dialog = ref(null), nameInput = ref(null), currentInput = ref(null)
const nameValid = computed(() => Boolean(displayName.value.trim()) && displayName.value.trim().length <= 80)
const nameChanged = computed(() => displayName.value.trim() !== props.user.display_name)
const passwordValid = computed(() => Boolean(passwords.value.current) && validPassword(passwords.value.next)
  && passwords.value.next === passwords.value.confirm && passwords.value.next !== passwords.value.current)
let disposed = false, previousFocus

watch([displayName, () => passwords.value.current, () => passwords.value.next, () => passwords.value.confirm], () => {
  error.value = ''; success.value = ''
}, { flush: 'sync' })
function clearPasswords() { passwords.value = { current: '', next: '', confirm: '' } }
function close() { if (!saving.value) { clearPasswords(); emit('close') } }
async function selectTab(value, focusField = true) {
  if (saving.value) return
  tab.value = value; error.value = ''; success.value = ''
  if (value === 'profile') clearPasswords()
  await nextTick()
  if (!disposed && focusField) (value === 'profile' ? nameInput.value : currentInput.value)?.focus()
}
async function onTabKeydown(event) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key) || saving.value) return
  event.preventDefault()
  const value = event.key === 'Home' ? 'profile' : event.key === 'End' ? 'security' : tab.value === 'profile' ? 'security' : 'profile'
  await selectTab(value, false)
  if (!disposed) document.getElementById(`account-${value === 'profile' ? 'profile' : 'security'}-tab`)?.focus()
}
async function saveName() {
  if (saving.value) return
  error.value = ''; success.value = ''
  if (!nameValid.value) { error.value = '姓名不能为空，且最多 80 个字符'; return }
  if (!nameChanged.value) { success.value = '姓名没有变化'; return }
  saving.value = true
  try {
    const result = await patch('/me', { display_name: displayName.value.trim() })
    if (disposed) return
    displayName.value = result.display_name
    emit('saved', result)
    success.value = '姓名已保存'
  } catch (e) { if (!disposed) error.value = e.message }
  finally { saving.value = false }
}
async function savePassword() {
  if (saving.value) return
  error.value = ''; success.value = ''
  if (!passwords.value.current) { error.value = '请输入当前密码'; return }
  if (!validPassword(passwords.value.next)) { error.value = '新密码需为 12–128 位，且包含大小写字母、数字、特殊字符中的至少三类'; return }
  if (passwords.value.next !== passwords.value.confirm) { error.value = '两次输入的新密码不一致'; return }
  if (passwords.value.next === passwords.value.current) { error.value = '新密码不能与当前密码相同'; return }
  saving.value = true
  try {
    const result = await post('/me/password', { current_password: passwords.value.current, new_password: passwords.value.next })
    if (disposed) return
    clearPasswords()
    emit('password-changed')
    success.value = result.message
  } catch (e) { if (!disposed) error.value = e.message }
  finally { saving.value = false }
}
function onKeydown(event) {
  if (event.key === 'Escape') { event.preventDefault(); close() }
  if (event.key !== 'Tab') return
  const items = [...dialog.value.querySelectorAll('button:not(:disabled):not([tabindex="-1"]), input:not(:disabled):not([tabindex="-1"]), [tabindex="0"]:not(:disabled)')]
  if (!items.length) { event.preventDefault(); dialog.value.focus(); return }
  const first = items[0], last = items.at(-1)
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}
onMounted(() => { previousFocus = document.activeElement; nameInput.value?.focus() })
onBeforeUnmount(() => { disposed = true; clearPasswords(); if (previousFocus?.isConnected) previousFocus.focus() })
</script>

<template>
  <div class="modal-backdrop account-backdrop" @click.self="close">
    <section ref="dialog" class="modal-panel account-panel" role="dialog" aria-modal="true" aria-labelledby="account-title" tabindex="-1" @keydown="onKeydown">
      <div class="modal-head"><div><span class="eyebrow">MY ACCOUNT</span><h2 id="account-title">个人资料与安全</h2><p>管理你的姓名和登录密码</p></div><button class="icon-button" :disabled="saving" aria-label="关闭个人资料" @click="close"><X :size="20" /></button></div>
      <div class="account-identity"><div class="avatar">{{user.display_name?.slice(0,1)}}</div><div><strong>{{user.display_name}}</strong><span>{{user.username}}<template v-if="user.department"> · {{user.department}}</template></span></div></div>
      <div class="account-tabs" role="tablist" aria-label="个人设置" @keydown="onTabKeydown">
        <button id="account-profile-tab" role="tab" :tabindex="tab==='profile' ? 0 : -1" :aria-selected="tab==='profile'" aria-controls="account-profile" :disabled="saving" @click="selectTab('profile')"><UserRound :size="16" />基本资料</button>
        <button id="account-security-tab" role="tab" :tabindex="tab==='security' ? 0 : -1" :aria-selected="tab==='security'" aria-controls="account-security" :disabled="saving" @click="selectTab('security')"><LockKeyhole :size="16" />登录密码</button>
      </div>
      <div class="modal-body account-body" :aria-busy="saving">
        <p v-if="error" class="account-message error" role="alert">{{error}}</p>
        <p v-if="success" class="account-message success" role="status"><CircleCheck :size="16" />{{success}}</p>
        <form v-if="tab==='profile'" id="account-profile" role="tabpanel" aria-labelledby="account-profile-tab" @submit.prevent="saveName">
          <label class="field"><span>姓名 <b>*</b></span><input ref="nameInput" v-model="displayName" :disabled="saving" required maxlength="80" autocomplete="name" placeholder="请输入你的姓名" /><small class="field-help">用于单据申请人、审批人及个人资料展示。</small></label>
          <div class="account-readonly"><div><span>登录账号</span><strong>{{user.username}}</strong></div><div><span>所属部门</span><strong>{{user.department || '未设置'}}</strong></div><div><span>当前角色</span><strong>{{user.role_name}}</strong></div></div>
          <p class="field-help">账号、部门和角色由系统管理员维护。</p>
          <div class="form-footer"><button type="button" class="secondary-button" :disabled="saving" @click="close">关闭</button><button class="primary-button" :disabled="saving || !nameValid || !nameChanged">{{saving ? '保存中…' : '保存姓名'}}</button></div>
        </form>
        <form v-else id="account-security" role="tabpanel" aria-labelledby="account-security-tab" @submit.prevent="savePassword">
          <input class="account-autofill-username" :value="user.username" autocomplete="username" aria-hidden="true" tabindex="-1" readonly />
          <label class="field"><span>当前密码 <b>*</b></span><input ref="currentInput" v-model="passwords.current" :disabled="saving" type="password" required maxlength="128" autocomplete="current-password" placeholder="输入当前登录密码以验证身份" /></label>
          <PasswordField v-model="passwords.next" :disabled="saving" label="新密码" placeholder="设置 12–128 位的新密码" rules-id="account-password-rules" />
          <small v-if="passwords.current && passwords.next===passwords.current" class="account-mismatch">新密码不能与当前密码相同</small>
          <label class="field"><span>确认新密码 <b>*</b></span><input v-model="passwords.confirm" :disabled="saving" type="password" required minlength="12" maxlength="128" autocomplete="new-password" placeholder="再次输入新密码" :aria-invalid="Boolean(passwords.confirm && passwords.confirm!==passwords.next)" /><small v-if="passwords.confirm && passwords.confirm!==passwords.next" class="account-mismatch">两次输入的新密码不一致</small></label>
          <div class="form-footer"><button type="button" class="secondary-button" :disabled="saving" @click="close">关闭</button><button class="primary-button" :disabled="saving || !passwordValid">{{saving ? '保存中…' : '修改密码'}}</button></div>
        </form>
      </div>
    </section>
  </div>
</template>

<style scoped>
.account-backdrop{z-index:110}.account-panel{width:560px;max-width:calc(100vw - 32px);outline:0}.modal-head p{font-size:12px;color:#8b99ad;margin:7px 0 0}.account-identity{display:flex;align-items:center;gap:12px;margin:0 28px;padding:20px 0}.account-identity .avatar{width:44px;height:44px;border-radius:13px;font-size:18px}.account-identity strong{display:block;font-size:15px}.account-identity span{display:block;font-size:12px;color:#8593a8;margin-top:5px}.account-tabs{display:flex;gap:26px;padding:0 28px;border-bottom:1px solid #e9edf4}.account-tabs button{display:flex;align-items:center;gap:7px;padding:12px 0;border:0;border-bottom:2px solid transparent;background:none;color:#8491a6;font-size:13px;font-weight:600}.account-tabs button[aria-selected=true]{color:#4164d4;border-bottom-color:#4b6ff1}.account-identity,.account-tabs,.modal-head{flex-shrink:0}.account-body{padding-top:22px;min-height:0}.account-body form{display:flex;flex-direction:column;gap:18px}.account-body .form-footer{margin-top:6px}.account-readonly{padding:16px 18px;background:#f8fafd;border:1px solid #eaf0f6;border-radius:10px;display:grid;gap:13px}.account-readonly>div{display:grid;grid-template-columns:80px 1fr;gap:12px;font-size:12px;line-height:1.7}.account-readonly span{color:#93a0b3}.account-readonly strong{color:#54627a;font-weight:500;overflow-wrap:anywhere}.account-message{display:flex;align-items:center;gap:7px;padding:11px 13px;border-radius:8px;font-size:12px;line-height:1.6;margin:0 0 18px}.account-message.error{color:#b54d50;background:#fff2f1;border:1px solid #f8dbd8}.account-message.success{color:#397b62;background:#eff9f3;border:1px solid #d5eddd}.account-mismatch{color:#bd5659;font-size:11px}.account-autofill-username{position:absolute;width:1px;height:1px;opacity:0;pointer-events:none}.account-body .field-help{margin:0}.account-identity>div:last-child{min-width:0;overflow-wrap:anywhere}@media(max-width:520px){.account-identity{margin:0 20px}.account-tabs{padding:0 20px}.account-panel{max-height:calc(100dvh - 24px)}}
</style>
