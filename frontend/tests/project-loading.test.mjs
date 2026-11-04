import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { computed, ref } from 'vue'

const source=readFileSync(new URL('../src/ProjectManagement.vue',import.meta.url),'utf8')
const script=source.match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm,'')
function setup(get){
  const calls=[]
  const factory=new Function('d',`const {computed,ref,get,post,put,defineProps,defineEmits,onMounted}=d;${script};return {load,edit,save,projects,people,roles,editing,loadError,loaded,search,departmentFilter,statusFilter,projectCounts,departments,filteredProjects,hasFilters,resetFilters};`)
  const state=factory({computed,ref,get:async p=>{calls.push(p);return get(p)},post:async()=>{},put:async()=>{},
    defineProps:()=>({user:{roles:['admin']}}),defineEmits:()=>()=>{},onMounted:()=>{}})
  return {state,calls}
}
test('projects load once with roles; the user directory loads only when editing',async()=>{
  const project={id:1,name:'测试项目',members:[]}
  const h=setup(p=>({'/projects':[project],'/roles':[{id:'project_manager'}],'/users':[{id:4}]}[p]))
  await h.state.load()
  assert.deepEqual(h.calls,['/projects','/roles'])
  assert.deepEqual(h.state.projects.value,[project]);assert.deepEqual(h.state.people.value,[])
  await h.state.edit(project)
  assert.deepEqual(h.calls,['/projects','/roles','/users'])
  assert.deepEqual(h.state.people.value,[{id:4}]);assert.equal(h.state.editing.value.id,1)
})
test('failed project fetch has a retry state instead of claiming there are no projects',async()=>{
  let failed=true
  const h=setup(p=>{if(p==='/projects'&&failed)throw new Error('服务暂不可用');return []})
  await assert.rejects(h.state.load())
  assert.match(h.state.loadError.value,/项目加载失败/)
  failed=false;await h.state.load();assert.equal(h.state.loadError.value,'')
})
test('initial HTML has no network font stylesheet that can block rendering',()=>{
  const html=readFileSync(new URL('../index.html',import.meta.url),'utf8')
  assert.doesNotMatch(html,/fonts\.googleapis\.com|fonts\.gstatic\.com/)
})

const fixtures=[
  {id:1,name:'Atlas 生产平台',department:'研发部',active:true,members:[{name:'王小明'},{name:'林知行'}]},
  {id:2,name:'Atlas 测试平台',department:'研发部',active:false,members:[{name:'王小明'}]},
  {id:3,name:'监控 100%_迁移',department:'运维部',active:true,members:[{name:'陈一诺'}]},
  {id:4,name:'旧项目',department:'运维部',active:false,members:[]},
]
function projectSetup(){return setup(p=>p==='/projects'?structuredClone(fixtures):[])}

test('project totals cover the full visible list while filters combine and reset',async()=>{
  const {state,calls}=projectSetup()
  assert.equal(state.loaded.value,false)
  await state.load()
  assert.equal(state.loaded.value,true)
  assert.deepEqual(state.projectCounts.value,{total:4,active:2,inactive:2})
  assert.deepEqual(state.departments.value,['研发部','运维部'])
  state.search.value=' atlas '
  state.departmentFilter.value='研发部'
  state.statusFilter.value='inactive'
  assert.deepEqual(state.filteredProjects.value.map(p=>p.id),[2])
  assert.equal(state.hasFilters.value,true)
  assert.deepEqual(state.projectCounts.value,{total:4,active:2,inactive:2})
  assert.deepEqual(calls,['/projects','/roles'])
  state.resetFilters()
  assert.equal(state.hasFilters.value,false)
  assert.equal(state.filteredProjects.value.length,4)
})

test('project search matches visible names, departments and members and handles no results',async()=>{
  const {state}=projectSetup()
  await state.load()
  state.search.value=' 王小明 '
  assert.deepEqual(state.filteredProjects.value.map(p=>p.id),[1,2])
  state.search.value='运维部'
  assert.deepEqual(state.filteredProjects.value.map(p=>p.id),[3,4])
  state.search.value='100%_'
  assert.deepEqual(state.filteredProjects.value.map(p=>p.id),[3])
  state.statusFilter.value='inactive'
  assert.equal(state.filteredProjects.value.length,0)
  assert.equal(state.projectCounts.value.total,4)
  state.resetFilters()
  state.departmentFilter.value='运维部'
  state.statusFilter.value='active'
  assert.deepEqual(state.filteredProjects.value.map(p=>p.id),[3])
})

test('saving a project refreshes totals and filtered results without losing filters',async()=>{
  let projects=structuredClone(fixtures)
  const {state}=setup(p=>p==='/projects'?structuredClone(projects):[])
  await state.load()
  state.departmentFilter.value='研发部'
  state.statusFilter.value='active'
  state.editing.value={...projects[0],active:false}
  projects[0].active=false
  await state.save()
  assert.equal(state.editing.value,null)
  assert.deepEqual(state.projectCounts.value,{total:4,active:1,inactive:3})
  assert.equal(state.filteredProjects.value.length,0)
  assert.equal(state.departmentFilter.value,'研发部')
  assert.equal(state.statusFilter.value,'active')
  projects=[]
  await state.load()
  assert.deepEqual(state.projectCounts.value,{total:0,active:0,inactive:0})
  assert.deepEqual(state.departments.value,[])
})
