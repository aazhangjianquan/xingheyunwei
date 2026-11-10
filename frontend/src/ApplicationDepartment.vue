<script setup>
import { computed, watch } from 'vue'
const props = defineProps({ modelValue: Number, user: { type: Object, default: () => ({}) } })
const emit = defineEmits(['update:modelValue'])
const departments = computed(() => (props.user.departments || []).filter(d => d.active))
watch([departments, () => props.modelValue], () => {
  if (!departments.value.some(d => d.id === props.modelValue)) {
    const value = departments.value.length === 1 ? departments.value[0].id : null
    if (value !== props.modelValue) emit('update:modelValue', value)
  }
}, { immediate: true })
</script>
<template>
  <label class="field full"><span>本次申请部门 <b>*</b></span><select :value="modelValue ?? ''" required aria-label="本次申请部门" @change="emit('update:modelValue', Number($event.target.value))"><option value="" disabled>选择本人已加入的部门</option><option v-for="d in departments" :key="d.id" :value="d.id">{{ d.path || d.name }}</option></select><small class="field-help">首审由本部门负责人处理；负责人本人申请则从上级查找，无可用负责人时交由其他启用的系统管理员，始终排除申请人本人。</small><small v-if="!departments.length" class="field-help">暂无可选部门，请联系管理员添加部门成员关系。</small></label>
</template>
