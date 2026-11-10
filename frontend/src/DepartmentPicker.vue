<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { Check, ChevronDown, ChevronRight, Search, X } from 'lucide-vue-next'

const props = defineProps({
  modelValue: Number,
  departments: { type: Array, default: () => [] },
  disabled: Boolean,
  required: Boolean,
  label: { type: String, default: '所属部门' },
  placeholder: { type: String, default: '选择已有部门' },
})
const emit = defineEmits(['update:modelValue', 'change'])
const id = useId(), root = ref(null), trigger = ref(null), panel = ref(null), search = ref(null)
const opened = ref(false), query = ref(''), parentId = ref(null), invalid = ref(false), panelStyle = ref({})
const directory = computed(() => new Map(props.departments.map(d => [d.id, d])))
const selected = computed(() => directory.value.get(props.modelValue))
const current = computed(() => directory.value.get(parentId.value))
const children = computed(() => {
  const groups = new Map()
  for (const d of props.departments) {
    // 负责人目录可能不包含上级；只在现有可见目录中构建层级。
    const parent = directory.value.has(d.parent_id) && d.parent_id !== d.id ? d.parent_id : null
    if (!groups.has(parent)) groups.set(parent, [])
    groups.get(parent).push(d)
  }
  return groups
})
const breadcrumb = computed(() => {
  const trail = [], seen = new Set()
  let d = current.value
  while (d && !seen.has(d.id)) {
    seen.add(d.id); trail.unshift(d); d = directory.value.get(d.parent_id)
  }
  return trail
})
const term = computed(() => query.value.trim().toLocaleLowerCase())
const rows = computed(() => term.value
  ? props.departments.filter(d => `${d.name} ${d.path || ''}`.toLocaleLowerCase().includes(term.value))
  : children.value.get(parentId.value) || [])
const hasChildren = d => Boolean(children.value.get(d.id)?.length)

function positionPanel() {
  if (!opened.value || !trigger.value || !panel.value) return
  const box = trigger.value.getBoundingClientRect()
  const width = Math.min(Math.max(box.width, 360), window.innerWidth - 24)
  const below = window.innerHeight - box.bottom - 16, above = box.top - 16
  const down = below >= Math.min(420, above)
  const maxHeight = Math.min(420, Math.max(below, above))
  const height = Math.min(panel.value.scrollHeight, maxHeight)
  panelStyle.value = {
    width: `${width}px`, maxHeight: `${maxHeight}px`,
    left: `${Math.max(12, Math.min(box.left, window.innerWidth - width - 12))}px`,
    top: `${Math.max(8, down ? box.bottom + 6 : box.top - height - 6)}px`,
  }
}
async function open() {
  if (props.disabled || opened.value) return
  query.value = ''; parentId.value = null; opened.value = true
  await nextTick(); positionPanel(); search.value?.focus()
}
function close(restoreFocus = false) {
  opened.value = false
  if (restoreFocus) trigger.value?.focus()
}
function choose(d) {
  if (!d?.active || props.disabled) return
  invalid.value = false
  if (d.id !== props.modelValue) {
    emit('update:modelValue', d.id)
    emit('change', d.id)
  }
  close(true)
}
async function browse(id) {
  parentId.value = id
  await nextTick()
  // 层级切换会移除原行，将焦点保留在弹层内，便于继续键盘选择和 Esc 关闭。
  panel.value?.focus({ preventScroll: true })
}
function enter(d) {
  if (hasChildren(d) && !term.value) browse(d.id)
  else choose(d)
}
function validate() { invalid.value = true; open() }
function contains(target) { return root.value?.contains(target) || panel.value?.contains(target) }
function outside(event) { if (opened.value && !contains(event.target)) close() }
function reposition(event) { if (!panel.value?.contains(event.target)) positionPanel() }
function keyboard(event) {
  if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(true); return }
  if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return
  event.preventDefault()
  const options = [...panel.value.querySelectorAll('[data-department-option]:not(:disabled)')]
  if (!options.length) return
  const index = options.indexOf(document.activeElement)
  const down = event.key === 'ArrowDown'
  options[index < 0 ? (down ? 0 : options.length - 1) : (index + (down ? 1 : -1) + options.length) % options.length].focus()
}
watch([rows, parentId], async () => { await nextTick(); positionPanel() })
watch(() => props.departments, () => {
  if (parentId.value && !directory.value.has(parentId.value)) parentId.value = null
})
watch(() => props.disabled, value => { if (value) close() })
onMounted(() => {
  document.addEventListener('pointerdown', outside)
  document.addEventListener('focusin', outside)
  window.addEventListener('resize', positionPanel)
  window.addEventListener('scroll', reposition, true)
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', outside)
  document.removeEventListener('focusin', outside)
  window.removeEventListener('resize', positionPanel)
  window.removeEventListener('scroll', reposition, true)
})
</script>

<template>
  <div ref="root" class="department-picker">
    <button ref="trigger" type="button" role="combobox" class="department-trigger"
      :class="{ placeholder: !selected, invalid }" :disabled="disabled" :aria-label="label"
      :aria-expanded="opened" aria-haspopup="dialog" :aria-controls="id" :aria-required="required"
      :aria-invalid="invalid" :aria-describedby="invalid ? `${id}-error` : undefined"
      :title="selected?.path || selected?.name" @click="opened ? close() : open()"
      @keydown.down.prevent="open" @keydown.esc.stop.prevent="close(true)">
      <span>{{selected ? (selected.path || selected.name) + (!selected.active ? '（已停用）' : '') : placeholder}}</span>
      <ChevronDown :size="16" />
    </button>
    <!-- 保留原生表单必填校验；invalid 时将焦点交给可见的选择器。 -->
    <input class="department-validation" :value="selected ? modelValue : ''" :required="required"
      :disabled="disabled" tabindex="-1" aria-hidden="true" @invalid.prevent="validate" />
    <small v-if="invalid" :id="`${id}-error`" class="department-error" role="alert">请选择所属部门</small>
    <Teleport to="body">
      <div v-if="opened" :id="id" ref="panel" class="department-popover" :style="panelStyle"
        role="dialog" tabindex="-1" :aria-label="`选择${label}`" @keydown="keyboard">
        <div class="department-search">
          <Search :size="16" />
          <input ref="search" v-model="query" aria-label="搜索部门" placeholder="搜索部门名称或路径" autocomplete="off" @keydown.enter.prevent />
          <button v-if="query" type="button" aria-label="清空部门搜索" @click="query='';search.focus()"><X :size="15" /></button>
        </div>
        <nav v-if="!term" class="department-breadcrumb" aria-label="部门层级">
          <button type="button" :aria-current="!current ? 'location' : undefined" @click="browse(null)">全部部门</button>
          <template v-for="d in breadcrumb" :key="d.id">
            <ChevronRight :size="12" />
            <button type="button" :aria-current="d.id===parentId ? 'location' : undefined" @click="browse(d.id)">{{d.name}}</button>
          </template>
        </nav>
        <p v-else class="department-search-hint">搜索结果 · {{rows.length}} 个部门</p>
        <div class="department-options">
          <div v-if="current && !term" class="department-current">
            <span>当前层级：{{current.name}}</span>
            <button type="button" data-department-option :disabled="!current.active" @click="choose(current)">选择本级</button>
          </div>
          <button v-for="d in rows" :key="d.id" type="button" data-department-option class="department-option"
            :class="{ selected: d.id===modelValue }" :disabled="!d.active && (term || !hasChildren(d))"
            :aria-label="`${d.name}${!d.active ? '（已停用）' : ''}${!term && hasChildren(d) ? '，查看下级' : '，选择部门'}`"
            @click="enter(d)">
            <span class="department-option-label"><strong>{{d.name}}<small v-if="!d.active">（已停用）</small></strong>
              <small v-if="term">{{d.path || d.name}}</small>
            </span>
            <Check v-if="d.id===modelValue" :size="15" />
            <ChevronRight v-if="!term && hasChildren(d)" :size="16" />
          </button>
          <p v-if="!rows.length" class="department-empty">{{term ? '未找到匹配部门，试试其他名称' : current ? '本级暂无下级部门' : '暂无可选部门，请联系管理员创建'}}</p>
        </div>
        <p class="department-picker-help">{{term ? '点击搜索结果即可选择部门' : '逐级查看下级部门，也可选择当前层级'}}</p>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.department-picker{position:relative;min-width:0}
.department-trigger{display:flex;align-items:center;justify-content:space-between;gap:8px;width:100%;min-height:39px;border:1px solid #dfe5ef;border-radius:7px;padding:10px 12px;background:#fff;color:#34445f;font:inherit;font-size:12px;text-align:left;cursor:pointer}
.department-trigger>span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.department-trigger>svg{flex-shrink:0;color:#8a99af}.department-trigger.placeholder{color:#99a5b7}.department-trigger:hover{border-color:#9aafe5}.department-trigger:focus-visible{outline:2px solid #7291e9;outline-offset:2px}.department-trigger:disabled{background:#f5f7fa;color:#97a3b6;cursor:not-allowed}.department-trigger.invalid{border-color:#d97979}
.department-validation{position:absolute;width:1px;height:1px;padding:0;border:0;opacity:0;pointer-events:none;bottom:0;left:0}.department-error{display:block;margin-top:6px;color:#b75555;font-size:11px}
.department-popover{position:fixed;z-index:120;display:flex;flex-direction:column;box-sizing:border-box;border:1px solid #dee5f1;border-radius:11px;background:#fff;box-shadow:0 10px 40px #182d5330;overflow:hidden;color:#34445f;font-size:12px}
.department-search{display:flex;align-items:center;gap:8px;margin:12px 12px 0;border:1px solid #dfe5ef;border-radius:7px;padding:0 9px;flex-shrink:0;color:#8a99af}.department-search:focus-within{border-color:#829de8;box-shadow:0 0 0 2px #eaf0ff}.department-search input{width:100%;min-width:0;height:36px;border:0;outline:0;background:transparent;color:#34445f;font:inherit}.department-search input::placeholder{color:#99a5b7}.department-search button{border:0;background:transparent;display:flex;color:#8a99af;padding:4px;cursor:pointer}
.department-breadcrumb{display:flex;align-items:center;flex-wrap:wrap;gap:4px;padding:10px 12px;flex-shrink:0;border-bottom:1px solid #eef1f6}.department-breadcrumb svg{color:#a8b2c3;flex-shrink:0}.department-breadcrumb button{max-width:100%;border:0;border-radius:4px;background:transparent;color:#6e7d96;padding:3px 2px;font:inherit;overflow-wrap:anywhere;text-align:left;cursor:pointer}.department-breadcrumb button:hover{background:#f0f4ff;color:#5674d0}.department-breadcrumb [aria-current]{color:#34445f;font-weight:600}
.department-options{overflow-y:auto;overscroll-behavior:contain;padding:5px;min-height:0}.department-current{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:7px 9px 10px;margin-bottom:4px;border-bottom:1px solid #eef1f6;font-size:11px}.department-current>span{min-width:0;overflow-wrap:anywhere;color:#7e8da4}.department-current button{flex-shrink:0;border:1px solid #dce5fb;border-radius:5px;background:#f1f5ff;color:#5876d1;padding:5px 8px;font:inherit;cursor:pointer}
.department-option{display:flex;align-items:center;gap:8px;width:100%;border:0;border-radius:6px;background:transparent;color:#34445f;padding:11px 10px;font:inherit;text-align:left;cursor:pointer}.department-option:hover,.department-option:focus-visible{background:#f0f4ff;outline:0}.department-option.selected{background:#f3f6ff;color:#5573d0}.department-option-label{flex:1;min-width:0;overflow-wrap:anywhere}.department-option strong{display:block;font-weight:500;line-height:1.5}.department-option small{display:block;color:#8b99ae;font-size:11px;line-height:1.6;margin-top:2px}.department-option strong small{display:inline}.department-option svg{flex-shrink:0;color:#8a9ab4}.department-option.selected>svg{color:#5a79d5}.department-option:disabled,.department-current button:disabled{color:#aab3c2;background:transparent;cursor:not-allowed}
.department-search-hint{margin:0;padding:10px 14px 6px;color:#8a99af;font-size:11px;flex-shrink:0}.department-empty{padding:18px 12px;text-align:center;color:#8a99af;line-height:1.8}.department-picker-help{margin:0;padding:10px 14px;border-top:1px solid #eef1f6;color:#98a4b6;font-size:10px;flex-shrink:0}
@media(max-width:700px){.department-trigger{min-height:42px}.department-option{padding:12px 10px}.department-search input{font-size:16px}.department-breadcrumb button{padding:5px 2px}}
</style>
