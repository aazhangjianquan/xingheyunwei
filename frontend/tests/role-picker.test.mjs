import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { ref, computed } from 'vue'
const script=readFileSync(new URL('../src/RolePicker.vue',import.meta.url),'utf8').match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm,'')
const roles=[{id:'applicant',name:'申请人',group:'基础与系统'},{id:'commercial_head',name:'商业化负责人',group:'审批与执行'},{id:'department_head',name:'部门负责人',group:'审批与执行',assignment_source:'department'},{id:'sales_presales',name:'销售/售前',group:'项目与业务'},{id:'project_manager',name:'项目经理',group:'项目与业务'},{id:'manager',name:'管理者',group:'审批与执行'}]
function setup(values={}, grants=['applicant']){
  const changes=[];const props={modelValue:grants,roles,disabled:false,editableIds:null,...values}
  const factory=new Function('ref','computed','defineProps','defineEmits',script+';return {groups,selected,toggle,isSelected,locked,query}')
  const state=factory(ref,computed,()=>props,()=>((event,value)=>{changes.push(value);props.modelValue=value}))
  return {state,props,changes}
}
test('member picker exposes only basic system roles',()=>{
  const {state}=setup()
  assert.deepEqual(state.groups.value.map(g=>g.name),['基础与系统'])
  assert.deepEqual(state.groups.value.flatMap(g=>g.items).map(r=>r.id),['applicant'])
  state.query.value='客户'
  assert.deepEqual(state.groups.value.flatMap(g=>g.items),[])
})
test('member picker rejects business grants even when editableIds includes them',()=>{
  const {state,changes}=setup({editableIds:['sales_presales']})
  state.toggle('applicant');state.toggle('commercial_head');state.toggle('sales_presales')
  assert.deepEqual(changes,[])
})
test('legacy business aliases stay outside the direct system picker',()=>{
  const {state,changes}=setup({},['project_owner','project_manager','leader'])
  assert.deepEqual(state.selected.value,[])
  state.toggle('project_manager');state.toggle('manager')
  assert.deepEqual(changes,[])
})
test('a pending save prevents any role change',()=>{
  const {state,changes}=setup({disabled:true})
  state.toggle('sales_presales');state.toggle('applicant')
  assert.equal(changes.length,0)
})

test('retired heads and appointed department heads cannot be granted in the picker',()=>{
  const {state,changes}=setup({},['applicant','commercial_head','department_head'])
  assert.deepEqual(state.selected.value.map(r=>r.id),['applicant'])
  assert.ok(!state.groups.value.flatMap(g=>g.items).some(r=>r.id.endsWith('_head')))
  state.toggle('commercial_head');state.toggle('department_head')
  assert.deepEqual(changes,[])
})
