<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { AlertCircle, Download, Eye, FileText, Paperclip, RefreshCw, Upload, X } from 'lucide-vue-next'
import { get, getBlob, uploadFile } from './api'
import { createClientId } from './client-id'

const props = defineProps({ deliveryId: { type: Number, default: null }, uploadQueue: { type: Array, default: null }, disabled: Boolean, kind: { type: String, default: 'delivery' } })
const emit = defineEmits(['busy', 'blocked'])
const draft = !props.deliveryId
const items = ref([]), canUpload = ref(draft), loading = ref(!draft), error = ref('')
const maxSize = ref(20 * 1024 * 1024), extensions = ref(['.doc','.docx','.xls','.xlsx','.pdf','.txt','.csv','.md','.ppt','.pptx','.rtf','.png','.jpg','.jpeg','.gif','.webp'])
const picker = ref(null), queue = ref(props.uploadQueue || []), uploading = ref(false), dragOver = ref(false)
watch(uploading, value => emit('busy', value), {flush:'sync'})
const queueBlocked = computed(() => queue.value.some(entry => entry.status !== 'done'))
watch(queueBlocked, value => emit('blocked', value), {flush:'sync'})
const preview = ref(null), previewUrl = ref(''), previewText = ref(''), previewTruncated = ref(false)
const previewLoading = ref(false), previewError = ref(''), closeButton = ref(null), downloading = ref(null)
const previewPanel = ref(null), downloadError = ref('')
const downloadUrls = new Set()
let disposed = false, revision = 0, previewRevision = 0, previousFocus
const endpoint = () => `/${props.kind === 'personal_reminder' ? 'personal-reminders' : props.kind === 'change' ? 'changes' : 'deliveries'}/${props.deliveryId}/attachments`
const formatSize = value => value >= 1024 * 1024 ? `${(value / (1024 * 1024)).toFixed(1)} MB` : value >= 1024 ? `${(value / 1024).toFixed(1)} KB` : `${value} B`
const dateTime = value => new Date(/Z$|[+-]\d{2}:\d{2}$/.test(value) ? value : value + 'Z').toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false})

async function load() {
  if (draft) return
  const current = ++revision
  loading.value = true; error.value = ''
  try {
    const result = await get(endpoint())
    if (disposed || current !== revision) return
    items.value = result.items; canUpload.value = result.can_upload
    maxSize.value = result.max_size; extensions.value = result.extensions
  } catch (e) {
    if (disposed || current !== revision) return
    items.value = []; canUpload.value = false; error.value = e.message
    if ([401,403,404].includes(e.status)) closePreview()
  } finally { if (!disposed && current === revision) loading.value = false }
}
function addFiles(files) {
  if (!canUpload.value || uploading.value || props.disabled) return
  for (const file of files) {
    const extension = '.' + file.name.split('.').at(-1).toLowerCase()
    const allowedExtensions = props.kind !== 'delivery' ? extensions.value : extensions.value.filter(ext => !['.png','.jpg','.jpeg','.gif','.webp'].includes(ext))
    const message = !allowedExtensions.includes(extension) ? '不支持此文件类型' : !file.size ? '不能上传空文件' : file.size > maxSize.value ? `文件超过 ${formatSize(maxSize.value)} 限制` : ''
    queue.value.push({id:createClientId(), file, name:file.name, size:file.size, status:message ? 'invalid' : 'waiting', message})
  }
  if (picker.value) picker.value.value = ''
  if (!draft) return processQueue()
}
async function processQueue(retryId = null) {
  if (draft || !canUpload.value || uploading.value) return
  if (retryId) {
    const item = queue.value.find(item => item.id === retryId && item.status === 'failed')
    if (item) { item.status = 'waiting'; item.message = '' }
  }
  uploading.value = true
  try {
    for (const entry of queue.value.filter(item => item.status === 'waiting')) {
      if (disposed) break
      entry.status = 'uploading'; entry.message = ''
      try {
        const result = await uploadFile(endpoint(), entry.file)
        if (disposed && !props.uploadQueue) break
        entry.status = 'done'; entry.file = null
        if (disposed) break
        items.value.unshift(result)
      } catch (e) {
        if (disposed && !props.uploadQueue) break
        entry.status = 'failed'; entry.message = e.message
        if (disposed) break
        if ([401,403,404].includes(e.status)) { canUpload.value = false; break }
        if (e.status === 429) break
      }
    }
  } finally { if (!disposed) uploading.value = false }
}
function onDrop(event) { dragOver.value = false; return addFiles([...event.dataTransfer.files]) }
function clearQueue() { if (!uploading.value && !props.disabled) queue.value.splice(0) }
function removeFile(id) {
  if (!draft || props.disabled) return
  const index = queue.value.findIndex(entry => entry.id === id)
  if (index >= 0) queue.value.splice(index, 1)
}
function closePreview() {
  ++previewRevision
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''; previewText.value = ''; preview.value = null
  previewError.value = ''; previewLoading.value = false; previewTruncated.value = false
  downloadError.value = ''
  if (previousFocus?.isConnected) previousFocus.focus()
}
async function openPreview(item) {
  closePreview()
  previousFocus = document.activeElement
  preview.value = item; previewLoading.value = true
  const current = ++previewRevision
  await nextTick()
  closeButton.value?.focus()
  try {
    const blob = await getBlob(`${endpoint()}/${item.id}/preview`)
    if (disposed || current !== previewRevision) return
    if (item.preview_type === 'text') {
      const text = await blob.text()
      if (disposed || current !== previewRevision) return
      previewText.value = text.slice(0,500000); previewTruncated.value = text.length > 500000
    } else previewUrl.value = URL.createObjectURL(blob)
  } catch (e) { if (!disposed && current === previewRevision) previewError.value = e.message }
  finally { if (!disposed && current === previewRevision) previewLoading.value = false }
}
async function download(item) {
  if (downloading.value !== null) return
  downloading.value = item.id; error.value = ''; downloadError.value = ''
  try {
    const blob = await getBlob(`${endpoint()}/${item.id}/download`)
    if (disposed) return
    const url = URL.createObjectURL(blob); downloadUrls.add(url)
    const anchor = document.createElement('a')
    anchor.href = url; anchor.download = item.filename
    document.body.appendChild(anchor); anchor.click(); anchor.remove()
    window.setTimeout(() => { URL.revokeObjectURL(url); downloadUrls.delete(url) }, 1000)
  } catch (e) { if (!disposed) { error.value = e.message; downloadError.value = e.message } }
  finally { if (!disposed) downloading.value = null }
}
function onPreviewKey(event) {
  if (event.key === 'Escape') { event.stopPropagation(); closePreview() }
  if (event.key === 'Tab' && previewPanel.value) {
    const controls = [...previewPanel.value.querySelectorAll('button:not(:disabled), iframe')]
    const first = controls[0], last = controls.at(-1)
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
  }
}
onMounted(async () => {
  await load()
  if (!disposed && !draft && canUpload.value && queue.value.some(entry => entry.status === 'waiting')) await processQueue()
})
onBeforeUnmount(() => {
  disposed = true; ++revision; closePreview()
  downloadUrls.forEach(url => URL.revokeObjectURL(url)); downloadUrls.clear()
  // Selected files survive the create-form → detail transition and upload failures.
  if (!props.uploadQueue) queue.value = []
})
</script>

<template>
  <section class="detail-section delivery-attachments" aria-labelledby="delivery-attachments-title">
    <div class="attachment-heading"><div><h3 id="delivery-attachments-title"><Paperclip :size="16" />{{props.kind === 'personal_reminder' ? '提醒附件' : props.kind === 'change' ? '变更附件' : '项目附件'}} <span>{{draft ? queue.length : items.length}}</span></h3><p>{{props.kind === 'personal_reminder' ? '附件仅创建人与指定的被提醒人可以访问' : props.kind === 'change' ? (draft ? '随变更申请提交问题截图、方案文档与其他依据（选填）' : '集中保存变更问题截图、方案文档与其他依据') : (draft ? '随申请提交交付方案、项目清单与相关文档（选填）' : '集中保存交付方案、项目清单与相关文档')}}</p></div><button type="button" v-if="canUpload" class="secondary-button" :disabled="uploading || disabled" @click="picker.click()"><Upload :size="15" />添加附件</button></div>
    <p v-if="error" class="attachment-error" role="alert"><AlertCircle :size="15" />{{error}}<button type="button" class="text-button" @click="load">刷新附件</button></p>
    <div v-if="canUpload" class="attachment-drop" :class="{dragging:dragOver,disabled:uploading || disabled}" @dragover.prevent="dragOver=!uploading && !disabled" @dragleave.prevent="dragOver=false" @drop.prevent="onDrop">
      <input ref="picker" class="attachment-picker" type="file" multiple tabindex="-1" :accept="(props.kind !== 'delivery' ? extensions : extensions.filter(ext => !['.png','.jpg','.jpeg','.gif','.webp'].includes(ext))).join(',')" :disabled="uploading || disabled" :aria-label="props.kind === 'personal_reminder' ? '选择提醒附件' : '选择附件'" @change="addFiles([...$event.target.files])" />
      <FileText :size="23" /><div><strong>{{uploading ? '正在上传，请稍候…' : '拖入文档，或点击添加附件'}}</strong><small>Word / Excel / PDF / TXT / CSV / Markdown / PPT / RTF / 图片 · 单个文件不超过 {{formatSize(maxSize)}}</small></div>
    </div>
    <p v-else-if="!draft && !loading" class="attachment-hint">{{props.kind === 'personal_reminder' ? '已启用的提醒不能再添加附件；被提醒人到时可在线预览和下载。' : '仅创建人可上传；有此单据查看权限的成员可在线预览和下载。'}}</p>
    <p v-if="draft" class="attachment-hint">选择后可移除或重新选择，提交申请后自动上传。支持多选和拖拽。</p>
    <div v-if="queue.length" class="attachment-queue" aria-live="polite"><div class="attachment-queue-title"><strong>{{draft ? '已选附件' : '本次上传'}}</strong><div><button type="button" v-if="!draft && !uploading && queue.some(e=>e.status==='waiting')" class="text-button" @click="processQueue()">继续上传</button><button type="button" class="text-button" :disabled="uploading || disabled" @click="clearQueue">{{draft ? '清空选择' : '清空记录'}}</button></div></div><div v-for="entry in queue" :key="entry.id" class="attachment-queue-row"><div><strong>{{entry.name}}</strong><small v-if="entry.message" :class="{failure:entry.status!=='done'}">{{entry.message}}</small></div><span :class="{failure:['failed','invalid'].includes(entry.status),success:entry.status==='done'}">{{{waiting:draft?'待提交':'等待上传',uploading:'上传中',done:'已上传',failed:'上传失败',invalid:'未上传'}[entry.status]}}</span><button type="button" v-if="draft" class="text-button" :disabled="disabled" @click="removeFile(entry.id)">移除</button><button type="button" v-if="!draft && entry.status==='failed'" class="text-button" :disabled="uploading || !canUpload" @click="processQueue(entry.id)">重试</button></div></div>
    <p v-if="loading" class="attachment-empty" role="status">正在加载附件…</p>
    <div v-else-if="!draft && !items.length && !error" class="attachment-empty"><Paperclip :size="22" /><strong>{{props.kind === 'personal_reminder' ? '暂无提醒附件' : props.kind === 'change' ? '暂无变更附件' : '暂无项目附件'}}</strong><span>{{canUpload ? '添加文档或图片，便于审批与协作。' : '创建人上传后，可在这里查看。'}}</span></div>
    <div v-for="item in items" :key="item.id" class="attachment-row"><div class="attachment-type" :class="item.extension.slice(1)">{{item.extension.slice(1).toUpperCase()}}</div><div class="attachment-main"><strong :title="item.filename">{{item.filename}}</strong><small>{{formatSize(item.size)}} · {{item.uploader_name}} · {{dateTime(item.created_at)}}</small></div><div class="attachment-actions"><button type="button" class="text-button" @click="openPreview(item)"><Eye :size="14" />预览</button><button type="button" class="text-button" :disabled="downloading!==null" @click="download(item)"><Download :size="14" />{{downloading===item.id?'下载中':'下载'}}</button></div></div>
    <Teleport to="body"><div v-if="preview" class="attachment-preview-backdrop" @click.self="closePreview" @keydown="onPreviewKey"><section ref="previewPanel" class="attachment-preview-panel" role="dialog" aria-modal="true" aria-labelledby="attachment-preview-title"><div class="attachment-preview-head"><div><span>文档预览</span><h3 id="attachment-preview-title">{{preview.filename}}</h3></div><div><button type="button" class="secondary-button" :disabled="downloading!==null" @click="download(preview)"><Download :size="15" />下载原文件</button><button ref="closeButton" type="button" class="icon-button" aria-label="关闭附件预览" @click="closePreview"><X :size="21" /></button></div></div><p v-if="downloadError" class="attachment-error" role="alert">{{downloadError}}</p><div v-if="previewLoading" class="attachment-preview-message" role="status"><RefreshCw :size="26" /><strong>正在准备预览</strong><p>Office 文档首次预览需要转换，请稍候。</p></div><div v-else-if="previewError" class="attachment-preview-message" role="alert"><AlertCircle :size="27" /><strong>暂时无法预览</strong><p>{{previewError}}</p><button type="button" class="secondary-button" @click="openPreview(preview)">重新预览</button></div><template v-else-if="preview.preview_type==='text'"><p v-if="previewTruncated" class="attachment-text-note">仅预览前 50 万个字符，完整内容请下载原文件。</p><pre class="attachment-text">{{previewText}}</pre></template><img v-else-if="previewUrl && preview.preview_type==='image'" :src="previewUrl" :alt="preview.filename" class="attachment-image" /><iframe v-else-if="previewUrl" :src="previewUrl" :title="preview.filename" class="attachment-pdf"></iframe><p v-if="!previewLoading && !previewError && preview.preview_type==='pdf'" class="attachment-preview-foot">{{preview.preview_type==='image'?'图片预览；原始图片可下载查看。':'Office 文档以 PDF 展示，分页和公式显示以转换结果为准；原始文档可下载查看。'}}</p></section></div></Teleport>
  </section>
</template>

<style scoped>
.attachment-heading,.attachment-heading h3,.attachment-actions,.attachment-preview-head,.attachment-preview-head>div:last-child{display:flex;align-items:center;gap:10px}.attachment-heading{justify-content:space-between;gap:16px;margin-bottom:16px}.attachment-heading h3{margin:0;font-size:13px}.attachment-heading h3>svg{color:#6c82b9}.attachment-heading h3>span{padding:2px 6px;border-radius:5px;background:#edf2fc;color:#7790bd;font-size:10px}.attachment-heading p{font-size:11px;color:#9ba7b9;margin:7px 0 0}.attachment-heading .secondary-button{font-size:11px;white-space:nowrap;gap:6px}.attachment-drop{display:flex;align-items:center;gap:13px;background:#f8faff;border:1px dashed #cbd8f2;border-radius:10px;padding:18px;color:#7993c9;margin-bottom:16px;transition:background .15s}.attachment-drop.dragging{background:#edf2ff;border-color:#7190ec}.attachment-drop.disabled{opacity:.65}.attachment-drop strong{display:block;color:#566b91;font-size:12px}.attachment-drop small{display:block;color:#9ba8bd;font-size:10px;margin-top:7px;line-height:1.6}.attachment-picker{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}.attachment-hint{font-size:11px;line-height:1.7;color:#9aa8ba;margin:0 0 14px}.attachment-empty{display:flex;align-items:center;flex-direction:column;gap:9px;padding:26px;background:#fafbfd;border-radius:9px;font-size:11px;color:#a5b2c6}.attachment-empty strong{color:#8292ab;font-weight:500}.attachment-row{display:flex;gap:11px;align-items:center;padding:14px 0;border-bottom:1px solid #edf1f6}.attachment-row:last-child{border-bottom:0}.attachment-type{display:grid;place-items:center;flex-shrink:0;width:43px;height:45px;background:#eef2fc;color:#6e89bd;border-radius:9px;font-size:9px;font-weight:800}.attachment-type.pdf{background:#fff0ed;color:#c67b6c}.attachment-type.xlsx,.attachment-type.xls,.attachment-type.csv{background:#eef8f2;color:#619979}.attachment-main{min-width:0;flex:1}.attachment-main>strong{display:block;font-size:12px;color:#4e607b;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}.attachment-main small{display:block;font-size:10px;color:#a1adbe;margin-top:6px;line-height:1.7}.attachment-actions{flex-shrink:0;gap:12px}.attachment-actions button{display:flex;gap:4px;align-items:center;font-size:11px}.attachment-error{display:flex;align-items:center;flex-wrap:wrap;gap:7px;color:#b85b61;background:#fff3f1;border:1px solid #f6dfdc;border-radius:8px;padding:12px;font-size:11px;line-height:1.7}.attachment-queue{padding:12px 14px;border:1px solid #e7edf8;border-radius:9px;margin-bottom:16px;font-size:10px}.attachment-queue-title,.attachment-queue-row{display:flex;align-items:center;justify-content:space-between;gap:10px}.attachment-queue-title>div{display:flex;gap:10px}.attachment-queue-title{color:#8b9bb3;margin-bottom:8px}.attachment-queue-row{padding:8px 0;border-top:1px solid #f0f3f8;color:#90a0b8}.attachment-queue-row>div{flex:1;min-width:0}.attachment-queue-row strong{display:block;font-size:11px;color:#63738b;overflow-wrap:anywhere;font-weight:500}.attachment-queue-row small{display:block;margin-top:4px;line-height:1.7}.attachment-queue-row>span{white-space:nowrap}.failure{color:#bd656a!important}.success{color:#589b79!important}.attachment-preview-backdrop{position:fixed;inset:0;z-index:150;background:#18233a99;backdrop-filter:blur(3px);padding:26px;display:flex;align-items:center;justify-content:center}.attachment-preview-panel{width:min(1120px,100%);height:min(90vh,1000px);display:flex;flex-direction:column;background:#fff;border-radius:13px;overflow:hidden;box-shadow:0 30px 90px #17263c4d}.attachment-preview-head{justify-content:space-between;padding:17px 22px;border-bottom:1px solid #e7edf6;flex-shrink:0}.attachment-preview-head>div:first-child{min-width:0}.attachment-preview-head span{font-size:10px;color:#a1aec2}.attachment-preview-head h3{font-size:14px;margin:6px 0 0;color:#4b5e7c;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.attachment-preview-head .secondary-button{font-size:11px;white-space:nowrap}.attachment-preview-message{flex:1;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:14px;color:#91a2bf;padding:30px;text-align:center}.attachment-preview-message strong{font-size:14px;color:#7087ad}.attachment-preview-message p{font-size:12px;margin:0;line-height:1.8;max-width:600px}.attachment-pdf{flex:1;min-height:0;border:0;background:#eef1f6;width:100%}.attachment-image{flex:1;min-height:0;max-width:100%;max-height:100%;align-self:center;object-fit:contain;background:#f6f8fb}.attachment-text{flex:1;overflow:auto;margin:0;padding:24px 28px;background:#fbfcfe;color:#425571;font-size:13px;line-height:1.85;white-space:pre;tab-size:4}.attachment-preview-foot,.attachment-text-note{font-size:10px;color:#94a2b8;padding:10px 20px;margin:0;border-top:1px solid #eaf0f7;flex-shrink:0;line-height:1.6}@media(max-width:600px){.attachment-row{flex-wrap:wrap}.attachment-actions{margin-left:54px}.attachment-preview-backdrop{padding:10px}.attachment-preview-panel{height:calc(100dvh - 20px)}.attachment-preview-head{padding:14px;gap:8px}.attachment-preview-head .secondary-button{padding:7px}.attachment-heading p{max-width:190px}.attachment-text{padding:18px}}
</style>
