<script setup>
import WorkflowEditor from './WorkflowEditor.vue'
import ResourceCatalogEditor from './ResourceCatalogEditor.vue'
import { onMounted, ref } from 'vue'
import { get, post, put } from './api'
import { Save, Bell, Send, CheckCircle2, KeyRound } from 'lucide-vue-next'

const emit = defineEmits(['notice', 'saved'])
const config = ref(null)
const busy = ref(false)
const tab = ref('feishu')
const tabItems = [{ key: 'feishu', label: '飞书通知' }, { key: 'workflow', label: '审批流程' }, { key: 'resource-catalog', label: '资源类型' }]
async function reload() {
  const result = await get('/admin/settings')
  config.value = { ...result, feishu_app_secret: '' }
}
async function act(fn) {
  busy.value = true
  try { await fn() } catch (e) { emit('notice', e.message) } finally { busy.value = false }
}
function save() {
  return act(async () => {
    const values = Object.fromEntries(['site_name', 'public_base_url', 'feishu_app_id', 'feishu_app_secret', 'feishu_notifications_enabled', 'feishu_task_login_enabled'].map(key => [key, config.value[key]]))
    const result = await put('/admin/settings', { values })
    config.value = { ...result, feishu_app_secret: '' }
    emit('notice', '配置已保存到数据库并即时生效')
    emit('saved')
  })
}
function checkFeishu() { return act(async () => emit('notice', (await post('/admin/feishu/check', {})).message)) }
onMounted(() => act(reload))
</script>

<template>
  <div class="settings-shell">
    <div class="settings-tabs"><button v-for="item in tabItems" :key="item.key" :class="{ selected: tab === item.key }" @click="tab = item.key">{{ item.label }}</button></div>
    <section v-if="config && tab === 'feishu'" class="panel settings-card">
      <div class="settings-heading"><div class="summary-icon"><Bell :size="22" /></div><div><h3>飞书通知与任务登录</h3><p>飞书消息可免输入密码进入任务；普通浏览器未登录时使用平台账号密码。</p></div></div>
      <div class="notification-scenarios" aria-label="通知发送规则"><div><Send :size="18" /><strong>提交申请</strong><p>通知申请人提交成功，并告知当前环节和下一位处理人。</p></div><div><Bell :size="18" /><strong>审批流转</strong><p>提醒当前审批人处理待办；同步告知申请人下一位由谁处理。</p></div><div><CheckCircle2 :size="18" /><strong>流程结束</strong><p>完成后通知申请人查看结果；驳回时说明原因，方便修改重提。</p></div></div>
      <div class="notification-setup"><h4>异常访问自动提醒管理员</h4><p class="field-help">每个客户端 IP 单独限流：滚动 10 秒最多 20 次、60 秒最多 120 次，超限仅限制该 IP；不同 IP 的额度互不影响，不限制全平台请求总数。单个 IP 一分钟超过 120 次时通知所有启用的系统管理员；同一 IP 每 60 秒最多提醒一次，包含该 IP 被拦截和失败的请求。</p><p class="field-help">请在「用户与权限」为管理员配置本应用的 open_id，并启用飞书通知。通知异步发送，失败最多自动尝试 3 次，结果保留在通知记录中。</p></div>
      <form @submit.prevent="save"><div class="form-grid"><label class="field"><span>平台名称</span><input v-model="config.site_name" required /></label><label class="field"><span>平台访问地址</span><input v-model="config.public_base_url" type="url" required placeholder="https://ops.example.com" /><small class="field-help">通知中的单据链接使用此地址，请填写成员能够访问的地址。</small></label><label class="field"><span>飞书 App ID</span><input v-model="config.feishu_app_id" autocomplete="off" placeholder="cli_xxx" /></label><label class="field"><span>飞书 App Secret</span><input v-model="config.feishu_app_secret" type="password" autocomplete="new-password" :placeholder="config.feishu_app_secret_set ? '已加密保存，留空保留原密钥' : '填写应用密钥'" /></label></div>
        <div class="setting-toggle"><div><strong>启用飞书通知</strong><p>适用于业务审批通知与异常访问提醒。关闭后保留通知记录，后端限流与业务审批照常执行。</p></div><input v-model="config.feishu_notifications_enabled" type="checkbox" aria-label="启用飞书通知" /></div>
        <div class="setting-toggle"><div><strong>启用飞书任务免密</strong><p>从飞书内打开任务时，验证当前飞书身份并进入对应单据；首次使用可能需要确认授权。仅已绑定且启用的成员可登录，任务权限照常检查。</p></div><input v-model="config.feishu_task_login_enabled" type="checkbox" aria-label="启用飞书任务免密" /></div>
        <div class="notification-setup"><h4>飞书任务免密配置</h4><ol><li>在同一个企业自建应用中启用网页应用，将平台访问地址配置为应用主页，完成网页授权设置并发布应用。</li><li>在安全设置的重定向 URL 中添加：<code>{{ (config.public_base_url || '').replace(/\/$/, '') }}/api/auth/feishu/callback</code>。必须与平台访问地址一致，正式环境使用 HTTPS 域名。</li><li>确认成员在应用可用范围内，且「用户与权限」已绑定本应用的 open_id，再开启任务免密并在飞书内点击任务消息联调。检查应用凭据不能代替登录联调。</li></ol></div>
        <div class="notification-setup"><h4>完成这三步即可接收提醒</h4><ol><li>在飞书开放平台创建企业自建应用，启用机器人，开通 <code>im:message:send_as_bot</code> 权限并发布应用。</li><li>在「用户与权限」填写申请人、审批人及系统管理员在本应用下的 <code>open_id</code>，确保成员在应用可用范围内。</li><li>保存 App ID、App Secret 并启用通知，可在「通知记录」查看结果并重试失败消息。</li></ol></div>
        <div class="settings-note"><KeyRound :size="18" /><p>App Secret 加密保存，留空保留原密钥。通知包含申请摘要、处理人、业务链接，安全告警会包含账号、来源 IP 和请求频率；密码及 License Key 请登录平台按权限查看。</p></div><div class="settings-footer"><button type="button" class="secondary-button" :disabled="busy" @click="checkFeishu">检查已保存的应用凭据</button><button class="primary-button" :disabled="busy"><Save :size="16" /> 保存配置</button></div>
      </form>
    </section>
    <WorkflowEditor v-if="tab === 'workflow'" @notice="emit('notice', $event)" />
    <section v-if="tab === 'resource-catalog'" class="panel settings-card resource-catalog-card"><ResourceCatalogEditor @notice="emit('notice', $event)" /></section>
    <div v-if="!config" class="empty-state">正在加载系统配置…</div>
  </div>
</template>
<style scoped>
.notification-scenarios{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-bottom:25px}.notification-scenarios>div{padding:18px;background:#f8faff;border:1px solid #e5ebf8;border-radius:10px;color:#6f89ce}.notification-scenarios strong{display:block;font-size:12px;color:#4b6082;margin:10px 0 7px}.notification-scenarios p{font-size:11px;line-height:1.8;color:#8a9ab1;margin:0}.notification-setup{margin-top:23px;padding:2px 0;color:#8192aa}.notification-setup h4{margin:0 0 11px}.notification-setup ol{padding-left:18px;margin:0;font-size:11px;line-height:1.9}.notification-setup li+li{margin-top:6px}.notification-setup code{font-size:10px;color:#647eaf;background:#f0f4fb;border-radius:4px;padding:2px 4px;overflow-wrap:anywhere}@media(max-width:600px){.notification-scenarios{grid-template-columns:1fr;gap:10px}.notification-scenarios>div{padding:14px}.notification-scenarios strong{display:inline-block;margin:0 0 8px 9px;vertical-align:top}}
</style>
