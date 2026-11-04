<script setup>
import { computed, ref } from 'vue'
import { Check, Eye, EyeOff, LockKeyhole } from 'lucide-vue-next'
import { passwordChecks, validPassword } from './password-policy'
const props = defineProps({ modelValue: { type: String, default: '' }, disabled: Boolean,
  label: { type: String, default: '初始密码' }, placeholder: { type: String, default: '设置至少 12 位的初始密码' },
  rulesId: { type: String, default: 'member-password-rules' } })
const emit = defineEmits(['update:modelValue'])
const visible = ref(false)
const checks = computed(() => passwordChecks(props.modelValue))
const valid = computed(() => validPassword(props.modelValue))
const labels = ['大写 A–Z', '小写 a–z', '数字 0–9', '特殊字符']
</script>
<template>
  <div class="password-field"><label class="field"><span>{{ label }} <b>*</b></span><div class="password-input"><LockKeyhole :size="16" /><input :value="modelValue" :type="visible ? 'text' : 'password'" :disabled="disabled" required minlength="12" maxlength="128" autocomplete="new-password" :placeholder="placeholder" :aria-describedby="rulesId" @input="emit('update:modelValue', $event.target.value)" /><button type="button" :disabled="disabled" :aria-label="visible ? '隐藏密码' : '显示密码'" @click="visible = !visible"><component :is="visible ? EyeOff : Eye" :size="17" /></button></div></label>
    <div :id="rulesId" class="password-rules" aria-live="polite"><div class="password-rule-title"><span :class="{met:checks.length}"><Check v-if="checks.length" :size="13" /><i v-else></i>12–128 位字符</span><span :class="{met:checks.count >= 3}">{{ checks.count }} / 4 类 · 至少满足 3 类</span></div><div class="password-type-grid"><span v-for="(label, i) in labels" :key="label" :class="{met:checks.types[i]}"><Check v-if="checks.types[i]" :size="12" /><i v-else></i>{{ label }}</span></div><div class="password-meter"><i v-for="n in 4" :key="n" :class="{filled: modelValue && n <= checks.count, strong:valid}"></i></div><small>{{ valid ? '密码符合要求' : '组合使用大小写字母、数字和特殊字符中的至少三类。' }}</small></div>
  </div>
</template>
<style scoped>
.password-input{display:flex;align-items:center;gap:9px;border:1px solid #dfe6f0;border-radius:8px;padding:0 11px;background:#fbfcfe;color:#91a0b6}.password-input:focus-within{border-color:#8fa6f0;box-shadow:0 0 0 3px #5c7df51a;background:white}.password-input input{min-width:0;border:0!important;box-shadow:none!important;background:transparent!important;padding:0!important}.password-input button{border:0;background:transparent;color:#8b9ab1;padding:4px;display:grid;place-items:center}.password-rules{margin-top:10px;background:#f8fafd;border:1px solid #eaf0f6;padding:12px;border-radius:8px;font-size:10px;color:#9aa7ba}.password-rule-title{display:flex;justify-content:space-between;gap:8px;margin-bottom:10px}.password-rule-title>span,.password-type-grid>span{display:flex;gap:5px;align-items:center}.password-rules i{display:block;width:4px;height:4px;border-radius:50%;background:#c5cedc;margin:0 4px}.password-rules .met{color:#448a71}.password-type-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.password-meter{display:flex;gap:4px;margin:11px 0 7px}.password-meter i{width:auto;flex:1;height:3px;border-radius:2px;margin:0;background:#e5eaf1}.password-meter i.filled{background:#a0b4e9}.password-meter i.filled.strong{background:#65ad93}.password-rules small{font-size:10px;line-height:1.6}@media(max-width:450px){.password-type-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
