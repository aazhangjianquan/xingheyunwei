<script setup>
import { computed, onMounted, ref } from 'vue'
import { get, put } from './api'
import { Plus, Save, Server, ChevronRight, GripVertical, Trash2, Info, ArchiveRestore } from 'lucide-vue-next'

const emit = defineEmits(['notice'])
const items = ref([])
const selectedKey = ref('')
const busy = ref(false)
const loading = ref(true)
const loadError = ref('')
const draftKey = ref('')
const activeItem = computed(() => items.value.find(item => item.key === selectedKey.value) || null)
const fieldTypes = [{ key: 'text', name: '单行文本' }, { key: 'textarea', name: '多行文本' }, { key: 'number', name: '数字' }, { key: 'select', name: '下拉选项' }]

async function reload() {
  loading.value = true
  loadError.value = ''
  try {
    const result = await get('/admin/resource-catalog')
    items.value = (result.items || []).map(item => ({ ...item, fields: (item.fields || []).map(field => ({ ...field, optionsText: (field.options || []).join('\n') })) }))
    if (!items.value.some(item => item.key === selectedKey.value)) selectedKey.value = items.value[0]?.key || ''
  } catch (error) { loadError.value = error.message }
  finally { loading.value = false }
}
function newType() {
  const key = 'custom_' + Math.random().toString(36).slice(2, 7)
  const item = { key, name: '新资源类型', description: '', active: true, fields: [], isNew: true }
  items.value.push(item)
  selectedKey.value = key
}
function addField() {
  if (!activeItem.value) return
  const n = activeItem.value.fields.length + 1
  activeItem.value.fields.push({ key: 'field_' + n, label: '新规格字段', type: 'text', required: false, placeholder: '', options: [], optionsText: '' })
}
function removeField(index) { activeItem.value?.fields.splice(index, 1) }
function save() {
  if (busy.value) return
  const keys = new Set()
  for (const item of items.value) {
    const key = String(item.key || '').trim()
    if (!/^[A-Za-z][A-Za-z0-9_-]{0,29}$/.test(key)) { emit('notice', '类型标识需以英文字母开头，只能包含字母、数字、下划线和短横线，最多 30 位'); return }
    if (keys.has(key)) { emit('notice', '资源类型标识不能重复'); return }
    keys.add(key)
    if (!String(item.name || '').trim()) { selectedKey.value = item.key; emit('notice', '请填写资源类型名称'); return }
    const fieldKeys = new Set()
    for (const field of item.fields) {
      if (!/^[a-z][a-z0-9_]{0,39}$/.test(String(field.key || '').trim())) { selectedKey.value = item.key; emit('notice', '字段标识需使用小写字母开头的英文、数字和下划线'); return }
      if (fieldKeys.has(field.key)) { selectedKey.value = item.key; emit('notice', '同一资源类型下的字段标识不能重复'); return }
      fieldKeys.add(field.key)
      if (!String(field.label || '').trim()) { selectedKey.value = item.key; emit('notice', '请填写规格字段名称'); return }
      field.options = field.type === 'select' ? String(field.optionsText || '').split('\n').map(value => value.trim()).filter(Boolean) : []
      if (field.type === 'select' && !field.options.length) { selectedKey.value = item.key; emit('notice', '下拉字段至少需要一个可选项'); return }
    }
  }
  busy.value = true
  put('/admin/resource-catalog', { items: items.value.map(({ optionsText, isNew, ...item }) => ({ ...item, fields: item.fields.map(({ optionsText: text, ...field }) => ({ ...field, options: field.type === 'select' ? String(text ?? field.options?.join('\n') ?? '').split('\n').map(value => value.trim()).filter(Boolean) : [] })) })) })
    .then(async result => { items.value = (result.items || []).map(item => ({ ...item, fields: item.fields.map(field => ({ ...field, optionsText: (field.options || []).join('\n') })) })); emit('notice', '资源类型与规格配置已保存'); await reload() })
    .catch(error => emit('notice', error.message))
    .finally(() => { busy.value = false })
}
function toggleActive() {
  if (!activeItem.value) return
  activeItem.value.active = !activeItem.value.active
}
onMounted(reload)
</script>

<template>
  <div class="catalog-shell">
    <div class="catalog-intro"><div class="catalog-mark"><Server :size="20" /></div><div><h3>资源类型与规格字段</h3><p>按资源特性配置申请字段。停用会保留历史申请和原始规格快照，不会删除单据。</p></div><button class="secondary-button catalog-reload" :disabled="loading || busy" @click="reload"><ArchiveRestore :size="15" />重新载入</button></div>
    <div v-if="loadError" class="catalog-error" role="alert"><strong>配置暂时无法读取</strong><span>{{ loadError }}</span><button class="secondary-button" @click="reload">重试</button></div>
    <div v-else-if="loading" class="catalog-loading">正在读取资源目录…</div>
    <div v-else class="catalog-layout">
      <aside class="catalog-sidebar"><div class="catalog-sidebar-heading"><div><strong>资源类型</strong><span>{{ items.length }} 项</span></div><button class="catalog-add" aria-label="新增资源类型" title="新增资源类型" @click="newType"><Plus :size="17" /></button></div><button v-for="item in items" :key="item.key" class="catalog-type" :class="{ selected: selectedKey === item.key, inactive: !item.active }" @click="selectedKey = item.key"><span class="catalog-type-icon"><Server :size="16" /></span><span class="catalog-type-copy"><strong>{{ item.name }}</strong><small>{{ item.fields.length }} 个规格字段 · {{ item.active ? '启用中' : '已停用' }}</small></span><ChevronRight :size="15" class="catalog-chevron" /></button><div class="catalog-side-note"><Info :size="15" /><span>标识用于系统关联，类型创建后保持稳定；需要移除时请停用。</span></div></aside>
      <section v-if="activeItem" class="catalog-editor">
        <div class="catalog-editor-heading"><div><span class="catalog-eyebrow">RESOURCE TYPE</span><h4>{{ activeItem.isNew ? '新增资源类型' : '编辑资源类型' }}</h4><p>字段名称可以调整；历史申请会保留提交时的字段名称和值。</p></div><button class="catalog-toggle" :class="{ enabled: activeItem.active }" @click="toggleActive"><i></i>{{ activeItem.active ? '使用中' : '已停用' }}</button></div>
        <div class="catalog-basic-grid"><label class="field"><span>资源类型名称 <b>*</b></span><input v-model="activeItem.name" maxlength="60" placeholder="例如：云服务器 ECS" /></label><label class="field"><span>稳定标识 <b>*</b></span><input v-model="activeItem.key" :disabled="!activeItem.isNew" maxlength="30" placeholder="例如：ECS 或 APIKEY" /><small class="field-help">仅创建时可填写，供单据和接口稳定引用。</small></label><label class="field full"><span>用途说明</span><input v-model="activeItem.description" maxlength="200" placeholder="简要说明申请此类资源的使用场景" /></label></div>
        <div class="catalog-fields-heading"><div><h4>申请规格字段</h4><p>按申请人填写顺序展示。必填项会在提交和服务端再次校验。</p></div><button class="secondary-button" @click="addField"><Plus :size="15" />添加字段</button></div>
        <div v-if="!activeItem.fields.length" class="catalog-empty"><strong>此类型尚无专属字段</strong><span>建议添加能够帮助审批和交付的信息，例如厂商、规格、额度或有效期。</span><button class="secondary-button" @click="addField">添加第一个字段</button></div>
        <div v-for="(field, index) in activeItem.fields" :key="index" class="catalog-field-card"><div class="field-order"><GripVertical :size="16" /><span>{{ String(index + 1).padStart(2, '0') }}</span></div><div class="catalog-field-main"><div class="catalog-field-grid"><label class="field"><span>字段名称 <b>*</b></span><input v-model="field.label" maxlength="60" placeholder="例如：厂商" /></label><label class="field"><span>字段标识 <b>*</b></span><input v-model="field.key" maxlength="40" placeholder="例如：vendor" /></label><label class="field"><span>填写方式</span><select v-model="field.type"><option v-for="type in fieldTypes" :key="type.key" :value="type.key">{{ type.name }}</option></select></label><label class="field"><span>输入提示</span><input v-model="field.placeholder" maxlength="160" placeholder="例如：阿里云 / OpenAI" /></label><label v-if="field.type === 'select'" class="field full"><span>下拉选项 <b>*</b></span><textarea v-model="field.optionsText" rows="3" placeholder="每行填写一个选项"></textarea><small class="field-help">一行一个选项，申请人只能从配置的选项中选择。</small></label></div><label class="catalog-required"><input v-model="field.required" type="checkbox" /><span><strong>申请时必填</strong><small>提交时校验此字段，并提示填写内容</small></span></label></div><button class="catalog-remove-field" :aria-label="'移除' + field.label" title="移除此字段" @click="removeField(index)"><Trash2 :size="16" /></button></div>
        <div class="catalog-footer"><span>资源规格名称和值会随单据保存，供后续审批、审计与查询。</span><button class="primary-button" :disabled="busy" @click="save"><Save :size="16" />{{ busy ? '保存中…' : '保存资源目录' }}</button></div>
      </section>
      <div v-else class="catalog-empty-main"><strong>还没有资源类型</strong><button class="primary-button" @click="newType">新增资源类型</button></div>
    </div>
  </div>
</template>

<style scoped>
.catalog-shell{display:flex;flex-direction:column;gap:20px}.catalog-intro{display:flex;align-items:center;gap:14px;padding:18px 20px;border:1px solid #e8edf5;border-radius:14px;background:linear-gradient(115deg,#f8faff,#fff)}.catalog-mark{width:42px;height:42px;display:grid;place-items:center;border-radius:12px;background:#eaf0ff;color:#5f78c4;flex-shrink:0}.catalog-intro h3,.catalog-editor-heading h4,.catalog-fields-heading h4{margin:0;color:#25344a;font-size:15px}.catalog-intro p,.catalog-editor-heading p,.catalog-fields-heading p{margin:5px 0 0;color:#8492a9;font-size:11px;line-height:1.6}.catalog-reload{margin-left:auto;white-space:nowrap}.catalog-layout{display:grid;grid-template-columns:245px minmax(0,1fr);min-height:500px;border:1px solid #e7ebf2;border-radius:14px;background:#fff;overflow:hidden}.catalog-sidebar{padding:17px 12px;background:#f9fafe;border-right:1px solid #e9edf4}.catalog-sidebar-heading{display:flex;align-items:center;justify-content:space-between;padding:2px 5px 12px}.catalog-sidebar-heading div{display:flex;align-items:baseline;gap:8px}.catalog-sidebar-heading strong{font-size:12px;color:#45546d}.catalog-sidebar-heading span{font-size:10px;color:#a0aabe}.catalog-add{width:29px;height:29px;display:grid;place-items:center;border:1px solid #dfe6f2;border-radius:8px;background:#fff;color:#5e78c0;cursor:pointer}.catalog-type{width:100%;display:flex;align-items:center;gap:10px;padding:10px 8px;border:1px solid transparent;border-radius:10px;background:transparent;text-align:left;cursor:pointer}.catalog-type:hover{background:#f2f5fc}.catalog-type.selected{background:#fff;border-color:#e1e8f5;box-shadow:0 3px 10px #263c6610}.catalog-type.inactive{opacity:.65}.catalog-type-icon{display:grid;place-items:center;width:31px;height:31px;border-radius:9px;background:#eff3fc;color:#7588b7;flex-shrink:0}.catalog-type-copy{display:flex;flex:1;min-width:0;flex-direction:column;gap:4px}.catalog-type-copy strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px;color:#384962}.catalog-type-copy small{font-size:9px;color:#9ba6b8}.catalog-chevron{color:#aab3c1}.catalog-side-note{display:flex;align-items:flex-start;gap:7px;margin:17px 4px 0;padding:11px 9px;border-radius:9px;background:#f1f4fa;color:#8794aa;font-size:10px;line-height:1.6}.catalog-side-note svg{flex-shrink:0;margin-top:1px}.catalog-editor{padding:22px 24px}.catalog-editor-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;padding-bottom:18px;border-bottom:1px solid #edf0f5}.catalog-eyebrow{display:block;margin-bottom:5px;font-size:9px;letter-spacing:.14em;color:#8b9bc0;font-weight:700}.catalog-editor-heading p{margin-top:6px}.catalog-toggle{display:flex;align-items:center;gap:8px;padding:8px 11px;border:1px solid #e4e8ef;border-radius:999px;background:#f7f8fa;color:#8390a3;font-size:10px;cursor:pointer;white-space:nowrap}.catalog-toggle i{width:7px;height:7px;border-radius:50%;background:#aab3c0}.catalog-toggle.enabled{border-color:#dcebe4;background:#f1faf5;color:#4e8b69}.catalog-toggle.enabled i{background:#54a879}.catalog-basic-grid,.catalog-field-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px 18px}.catalog-basic-grid{padding:18px 0 22px}.catalog-basic-grid .field,.catalog-field-grid .field{display:flex;flex-direction:column;gap:7px}.catalog-basic-grid .field>span,.catalog-field-grid .field>span{font-size:10px;color:#687990}.catalog-basic-grid input,.catalog-field-grid input,.catalog-field-grid select,.catalog-field-grid textarea{width:100%;min-height:36px;padding:8px 10px;border:1px solid #e5eaf1;border-radius:8px;background:#fff;font:inherit;font-size:11px;color:#394b63;outline:none}.catalog-basic-grid input:focus,.catalog-field-grid input:focus,.catalog-field-grid select:focus,.catalog-field-grid textarea:focus{border-color:#91a5df;box-shadow:0 0 0 3px #90a2d519}.catalog-basic-grid input:disabled{background:#f5f6f8;color:#8590a2}.catalog-basic-grid .full,.catalog-field-grid .full{grid-column:1/-1}.catalog-fields-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:17px 0 12px;border-top:1px solid #edf0f5}.catalog-fields-heading h4{font-size:13px}.catalog-fields-heading p{font-size:10px}.catalog-field-card{display:flex;align-items:flex-start;gap:11px;padding:14px 12px;margin-top:9px;border:1px solid #e9edf3;border-radius:11px;background:#fff}.field-order{display:flex;align-items:center;gap:4px;padding-top:8px;color:#a5afbf}.field-order span{font-size:9px;font-weight:700;color:#8794a9}.catalog-field-main{flex:1;min-width:0}.catalog-field-grid{gap:12px}.catalog-field-grid .field>span{font-size:9px}.catalog-required{display:flex;align-items:center;gap:8px;margin-top:11px;cursor:pointer}.catalog-required input{accent-color:#657dc2}.catalog-required span{display:flex;flex-direction:column;gap:3px}.catalog-required strong{font-size:10px;color:#61728b}.catalog-required small{font-size:9px;color:#9ca7b7}.catalog-remove-field{display:grid;place-items:center;width:29px;height:29px;margin-top:4px;border:0;border-radius:8px;background:#fff6f4;color:#ba7770;cursor:pointer}.catalog-remove-field:hover{background:#ffebe7}.catalog-empty{display:flex;flex-direction:column;align-items:center;gap:8px;padding:28px 12px;border:1px dashed #dfe5ef;border-radius:11px;color:#8b98ac;text-align:center;font-size:10px}.catalog-empty strong{font-size:12px;color:#5b6b83}.catalog-empty-main{display:flex;flex-direction:column;justify-content:center;align-items:center;gap:14px;grid-column:1/-1;color:#718098}.catalog-footer{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-top:19px;padding-top:15px;border-top:1px solid #edf0f5;color:#939fb1;font-size:10px}.catalog-footer .primary-button{display:flex;align-items:center;gap:7px;white-space:nowrap}.catalog-error{display:flex;align-items:center;gap:12px;padding:14px;border:1px solid #f1d9d6;border-radius:10px;background:#fff8f7;color:#9d5f5c;font-size:11px}.catalog-error strong{color:#814b48}.catalog-error button{margin-left:auto}.catalog-loading{padding:45px;text-align:center;color:#91a0b4;font-size:12px}.field-help{font-size:9px;color:#9aa6b8}@media(max-width:780px){.catalog-layout{grid-template-columns:1fr}.catalog-sidebar{border-right:0;border-bottom:1px solid #e9edf4}.catalog-sidebar .catalog-type{display:inline-flex;width:calc(50% - 5px);vertical-align:top;margin:2px}.catalog-editor{padding:18px 15px}.catalog-intro{align-items:flex-start;flex-wrap:wrap}.catalog-reload{margin-left:auto}.catalog-footer{align-items:flex-start;flex-direction:column}.catalog-footer .primary-button{align-self:flex-end}}@media(max-width:520px){.catalog-basic-grid,.catalog-field-grid{grid-template-columns:1fr}.catalog-basic-grid .full,.catalog-field-grid .full{grid-column:auto}.catalog-sidebar .catalog-type{width:100%}.catalog-editor-heading{align-items:flex-start}.catalog-error{align-items:flex-start;flex-direction:column}.catalog-error button{margin-left:0}.catalog-fields-heading{align-items:flex-start}.catalog-footer .primary-button{align-self:stretch;justify-content:center}}
</style>
