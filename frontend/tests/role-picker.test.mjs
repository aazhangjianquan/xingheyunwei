import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { ref, computed } from 'vue'
const script=readFileSync(new URL('../src/RolePicker.vue',import.meta.url),'utf8').match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm,'')
const roles=[{id:'applicant',name:'申请人',group:'基础与系统'},{id:'commercial_head',name:'商业化负责人',group:'审批与执行'},{id:'sales_presales',name:'销售/售前',group:'项目与业务'},{id:'project_manager',name:'项目经理',group:'项目与业务'},{id:'manager',name:'管理者',group:'审批与执行'}]
function setup(values={}, grants=['applicant']){
  const changes=[];const props={modelValue:grants,roles,disabled:false,editableIds:null,...values}
  const factory=new Function('ref','computed','defineProps','defineEmits',script+';return {groups,selected,toggle,isSelected,locked,query}')
  const state=factory(ref,computed,()=>props,()=>((event,value)=>{changes.push(value);props.modelValue=value}))
  return {state,props,changes}
}
test('role groups and search follow the server catalogue and keep responsibilities discoverable',()=>{
  const {state}=setup()
  assert.deepEqual(state.groups.value.map(g=>g.name),['基础与系统','审批与执行','项目与业务'])
  assert.deepEqual(state.groups.value[2].items.map(r=>r.id),['sales_presales','project_manager'])
  state.query.value='本部门'
  assert.deepEqual(state.groups.value.flatMap(g=>g.items).map(r=>r.id),['commercial_head'])
})
test('scoped managers can toggle their business role while existing roles remain locked',()=>{
  const {state,changes}=setup({editableIds:['sales_presales']})
  state.toggle('applicant');state.toggle('commercial_head')
  assert.equal(changes.length,0)
  state.toggle('sales_presales')
  assert.deepEqual(changes[0],['applicant','sales_presales'])
  state.toggle('sales_presales')
  assert.deepEqual(changes[1],['applicant'])
})
test('legacy aliases collapse to a single visible selection and can be removed consistently',()=>{
  const {state,changes}=setup({},['project_owner','project_manager','leader'])
  assert.deepEqual(state.selected.value.map(r=>r.id),['project_manager','manager'])
  state.toggle('project_manager')
  assert.deepEqual(changes[0],['leader'])
  state.toggle('manager')
  assert.deepEqual(changes[1],[])
})
test('a pending save prevents any role change',()=>{
  const {state,changes}=setup({disabled:true})
  state.toggle('sales_presales');state.toggle('applicant')
  assert.equal(changes.length,0)
})
