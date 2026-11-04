import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { ref } from 'vue'

const source = readFileSync(new URL('../src/SystemSettings.vue', import.meta.url), 'utf8')
const script = source.match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
function setup() {
  const calls=[], events=[]
  const saved={site_name:'通知测试',public_base_url:'https://ops.example.com',feishu_app_id:'cli_app',feishu_app_secret_set:true,
    feishu_notifications_enabled:false,feishu_login_enabled:false,local_login_enabled:true}
  const factory=new Function('d',`const {ref,get,post,put,defineEmits,onMounted}=d;${script};return {reload,save,config,busy};`)
  const state=factory({ref,onMounted(){},defineEmits:()=>((...args)=>events.push(args)),
    get:async path=>{calls.push({path});return path==='/admin/settings'?saved:{configured:{}}},
    put:async(path,body)=>{calls.push({path,body});return {...saved,...body.values}},post:async()=>{},
  })
  return {state,calls,events}
}
test('notification settings save only message configuration and preserve a saved secret',async()=>{
  const h=setup()
  await h.state.reload()
  assert.deepEqual(h.calls.map(c=>c.path),['/admin/settings'])
  assert.equal(h.state.config.value.feishu_app_secret,'')
  h.state.config.value.feishu_notifications_enabled=true
  await h.state.save()
  const {values}=h.calls.at(-1).body
  assert.deepEqual(values,{site_name:'通知测试',public_base_url:'https://ops.example.com',feishu_app_id:'cli_app',feishu_app_secret:'',feishu_notifications_enabled:true})
  assert.equal(h.state.busy.value,false)
  assert.equal(h.events.some(([event])=>event==='saved'),true)
})
