<script setup>
import { computed, onBeforeUnmount, onMounted, ref, useId } from 'vue'
import { ChevronDown, Search } from 'lucide-vue-next'
const props=defineProps({modelValue:{type:Array,default:()=>[]},options:{type:Array,default:()=>[]},label:String,disabled:Boolean})
const emit=defineEmits(['update:modelValue'])
const root=ref(null),button=ref(null),searchInput=ref(null),opened=ref(false),query=ref(''),id=useId()
const selected=computed(()=>new Set(props.modelValue))
const names=computed(()=>props.options.filter(p=>selected.value.has(p.id)).map(p=>p.display_name))
const matches=computed(()=>props.options.filter(p=>`${p.display_name} ${p.department || ''}`.toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())))
function toggle(person){if(props.disabled || !person.active && !selected.value.has(person.id))return;emit('update:modelValue',selected.value.has(person.id)?props.modelValue.filter(id=>id!==person.id):[...props.modelValue,person.id])}
function outside(e){if(!root.value?.contains(e.target))opened.value=false}
function close(){opened.value=false;button.value?.focus()}
onMounted(()=>document.addEventListener('pointerdown',outside))
onBeforeUnmount(()=>document.removeEventListener('pointerdown',outside))
</script>
<template>
  <div ref="root" class="member-select" @keydown.esc.stop.prevent="close">
    <button ref="button" type="button" class="member-select-trigger" :disabled="disabled" :aria-label="label" :aria-expanded="opened" :aria-controls="id" @click="opened=!opened;query=''">
      <span>{{names.length?names.join('、'):'请选择平台成员（可多选）'}}</span><ChevronDown :size="15" />
    </button>
    <div v-if="opened" :id="id" class="member-select-dropdown">
      <label class="member-select-search"><Search :size="14"/><input ref="searchInput" v-model="query" :aria-label="`搜索${label}人员`" placeholder="搜索姓名或部门" @keydown.enter.prevent /></label>
      <div class="member-select-options" role="group" :aria-label="label+'候选人员'">
        <label v-for="person in matches" :key="person.id"><input type="checkbox" :checked="selected.has(person.id)" :disabled="disabled || (!person.active && !selected.has(person.id))" @change="toggle(person)"/><span><strong>{{person.display_name}}{{!person.active?'（已停用）':''}}</strong><small>{{person.department || '未分配部门'}}</small></span></label>
        <p v-if="!matches.length">没有匹配的成员</p>
      </div>
      <button type="button" class="text-button" @click="close">完成选择 · 已选 {{modelValue.length}} 人</button>
    </div>
  </div>
</template>
<style scoped>
.member-select{width:100%;min-width:0}.member-select-trigger{width:100%;display:flex;align-items:center;justify-content:space-between;gap:10px;border:1px solid #dfe5ef;border-radius:7px;padding:11px 12px;background:white;color:#536580;font:inherit;font-size:12px;text-align:left}.member-select-trigger span{min-width:0;overflow-wrap:anywhere}.member-select-trigger svg{flex-shrink:0;color:#91a0b7}.member-select-trigger:disabled{background:#f5f7fb;color:#9aa7b8}.member-select-trigger:focus-visible{outline:2px solid #94aaf0;outline-offset:2px}.member-select-dropdown{border:1px solid #dce4f3;border-radius:8px;margin-top:6px;padding:10px;background:#fff;box-shadow:0 4px 14px #2948730b}.member-select-search{display:flex;align-items:center;gap:8px;border:1px solid #e1e7f1;padding:0 8px;border-radius:6px;color:#99a6b9}.member-select-search input{border:0!important;outline:0;width:100%;padding:9px 0!important;min-width:0;font-size:12px;background:transparent}.member-select-options{max-height:210px;overflow-y:auto;overscroll-behavior:contain;padding-top:7px}.member-select-options label{display:flex;align-items:center;gap:9px;padding:9px 6px;border-radius:5px;cursor:pointer}.member-select-options label:hover{background:#f3f6ff}.member-select-options input{width:auto;margin:0;flex-shrink:0}.member-select-options strong,.member-select-options small{display:block;overflow-wrap:anywhere}.member-select-options strong{font-size:12px;font-weight:500;color:#42536e}.member-select-options small,.member-select-options p{font-size:10px;color:#98a6ba;margin:4px 0}.member-select-dropdown>.text-button{display:block;margin-left:auto;padding-top:10px}
</style>
