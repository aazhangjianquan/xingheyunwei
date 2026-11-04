<script setup>
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { Search, RefreshCw, X, ShieldCheck, ChevronRight, FileClock, ArrowRight } from 'lucide-vue-next'
import { get } from './api'

const kind = ref('business'), search = ref(''), actor = ref(''), targetType = ref(''), action = ref(''), result = ref('all')
const fromDate = ref(''), toDate = ref(''), page = ref(1), pageSize = ref(20)
const rows = ref([]), total = ref(0), totalPages = ref(1), types = ref([]), actions = ref([])
const loading = ref(false), error = ref(''), detail = ref(null), detailLoading = ref(false), detailError = ref(''), detailOpen = ref(false)
const modalElement = ref(null), closeButton = ref(null)
let revision = 0, detailRevision = 0, timer, disposed = false
let previousFocus = null
const hasFilters = computed(() => Boolean(search.value.trim() || actor.value.trim() || targetType.value || action.value || result.value !== 'all' || fromDate.value || toDate.value))
const rangeStart = computed(() => total.value ? (page.value - 1) * pageSize.value + 1 : 0)
const rangeEnd = computed(() => Math.min(page.value * pageSize.value, total.value))
const pageNumbers = computed(() => {
  const values = [...new Set([1, totalPages.value, page.value - 1, page.value, page.value + 1])].filter(n => n >= 1 && n <= totalPages.value).sort((a,b) => a-b)
  return values.flatMap((n, i) => i && n-values[i-1] > 1 ? ['…', n] : [n])
})
function dateTime(value) { return new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value)) }
function display(value) { return value === null || value === undefined || value === '' ? '—' : value }
function schedule(delay = 0) {
  ++revision
  clearTimeout(timer)
  loading.value = true
  timer = window.setTimeout(load, delay)
}
async function load() {
  clearTimeout(timer)
  const current = ++revision
  loading.value = true; error.value = ''
  if (fromDate.value && toDate.value && fromDate.value > toDate.value) {
    error.value = '开始日期不能晚于结束日期'; rows.value = []; total.value = 0; totalPages.value = 1; loading.value = false
    return
  }
  const params = new URLSearchParams({record_kind:kind.value, page:String(page.value), page_size:String(pageSize.value), q:search.value.trim(), actor:actor.value.trim(), result:result.value})
  if (targetType.value) params.set('target_type', targetType.value)
  if (action.value) params.set('action', action.value)
  if (fromDate.value) params.set('from_date', fromDate.value)
  if (toDate.value) params.set('to_date', toDate.value)
  try {
    const data = await get(`/audit?${params}`)
    if (disposed || current !== revision) return
    rows.value = data.items; total.value = data.total; totalPages.value = data.total_pages
    types.value = data.types; actions.value = data.actions
    if (page.value !== data.page) {
      // Clamping is a server result, not a new page request.
      ignorePage = true; page.value = data.page; ignorePage = false
    }
  } catch (e) {
    if (!disposed && current === revision) { error.value = e.message; rows.value = []; total.value = 0; totalPages.value = 1 }
  } finally { if (!disposed && current === revision) loading.value = false }
}
let ignorePage = false
function resetFilters() { search.value=''; actor.value=''; targetType.value=''; action.value=''; result.value='all'; fromDate.value=''; toDate.value='' }
watch(kind, () => { resetFilters(); page.value=1; schedule() }, {flush:'sync'})
watch([search, actor], () => { page.value=1; schedule(300) }, {flush:'sync'})
watch([targetType, action, result, fromDate, toDate, pageSize], () => { page.value=1; schedule() }, {flush:'sync'})
watch(page, () => { if (!ignorePage) schedule() }, {flush:'sync'})
async function openDetail(row) {
  const current = ++detailRevision
    if (!detailOpen.value && typeof document !== 'undefined') previousFocus = document.activeElement
  detailOpen.value = true; detailLoading.value = true; detailError.value = ''; detail.value = null
    await nextTick()
    if (current !== detailRevision || disposed) return
    closeButton.value?.focus()
  try {
    const data = await get(`/audit/${row.id}`)
    if (!disposed && current === detailRevision) detail.value = data
  } catch (e) { if (!disposed && current === detailRevision) detailError.value = e.message }
  finally { if (!disposed && current === detailRevision) detailLoading.value = false }
}
function closeDetail() {
  ++detailRevision; detailOpen.value=false; detail.value=null; detailError.value=''; detailLoading.value=false
  if (previousFocus?.isConnected) previousFocus.focus()
}
function onKey(event) {
  if (!detailOpen.value) return
  if (event.key === 'Escape') { event.preventDefault(); closeDetail() }
  if (event.key === 'Tab' && modalElement.value) {
    const controls = [...modalElement.value.querySelectorAll('button, summary, [href], [tabindex="0"]')].filter(el => !el.disabled && el.getClientRects().length)
    const first = controls[0], last = controls.at(-1)
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
  }
}
onMounted(() => { load(); window.addEventListener('keydown', onKey) })
onBeforeUnmount(() => { disposed=true; ++revision; ++detailRevision; clearTimeout(timer); window.removeEventListener('keydown', onKey) })
</script>

<template>
  <div class="audit-page">
    <div class="section-summary audit-overview">
      <div class="summary-icon audit"><ShieldCheck :size="23" /></div>
      <div><strong>每一次业务变动，都有据可查</strong><span>追溯操作人、操作时间、具体单据与修改内容。时间统一显示为北京时间。</span></div>
      <div class="summary-stats"><span>符合条件 <strong>{{loading ? '—' : total}}</strong> 条</span></div>
    </div>
    <section class="panel audit-panel">
      <div class="audit-toolbar"><div class="audit-tabs" role="tablist" aria-label="日志类型"><button role="tab" :aria-selected="kind==='business'" :class="{active:kind==='business'}" @click="kind='business'">业务操作</button><button role="tab" :aria-selected="kind==='request'" :class="{active:kind==='request'}" @click="kind='request'">接口访问与异常</button></div><button class="icon-button" aria-label="刷新操作日志" :disabled="loading" @click="load"><RefreshCw :size="16" /></button></div>
      <div class="audit-intro"><strong>{{kind==='business'?'谁，改动了哪一张单据':'接口访问与操作失败排查'}}</strong><span>{{kind==='business'?'记录创建、修改、审批、回填、停用等实际发生的业务操作。':'保留接口访问和失败尝试；历史版本的日志也在此查询。'}}</span></div>
      <div class="audit-filters">
        <label class="audit-keyword"><span>单据 / 关键词</span><div class="search-box"><Search :size="16" /><input v-model="search" type="search" :placeholder="kind==='business'?'搜索单号、项目名或操作内容':'搜索接口路径或记录编号'" maxlength="100" /></div></label>
        <label><span>操作人</span><input v-model="actor" placeholder="姓名或用户名" maxlength="80" /></label>
        <label v-if="kind==='business'"><span>业务类型</span><select v-model="targetType"><option value="">全部业务</option><option v-for="item in types" :key="item.value" :value="item.value">{{item.label}}</option></select></label>
        <label v-if="kind==='business'"><span>操作动作</span><select v-model="action"><option value="">全部动作</option><option v-for="item in actions" :key="item.value" :value="item.value">{{item.label}}</option></select></label>
        <label v-if="kind==='request'"><span>操作结果</span><select v-model="result"><option value="all">全部结果</option><option value="failure">失败 / 被拒绝</option><option value="success">成功</option></select></label>
        <div class="audit-dates"><label><span>开始日期</span><input v-model="fromDate" type="date" aria-label="开始日期（北京时间）" /></label><span>至</span><label><span>结束日期</span><input v-model="toDate" type="date" aria-label="结束日期（北京时间）" /></label></div>
        <button class="text-button audit-reset" :disabled="!hasFilters" @click="resetFilters">重置筛选</button>
      </div>
      <div v-if="error" class="audit-load-error" role="alert">{{error}} <button class="text-button" @click="load">重新加载</button></div>
      <div class="table-wrap" :aria-busy="loading">
        <table class="data-table audit-table"><thead><tr><th>操作时间 <small>北京时间</small></th><th>操作人</th><th>业务对象</th><th>操作内容</th><th>结果</th><th></th></tr></thead>
          <tbody v-if="loading"><tr><td colspan="6" class="audit-empty" role="status">正在查询操作记录…</td></tr></tbody>
          <tbody v-else><tr v-for="row in rows" :key="row.id">
            <td class="table-date">{{dateTime(row.created_at)}}</td>
            <td><div class="audit-actor"><span class="mini-avatar">{{row.actor_name.slice(0,1)}}</span><div><strong>{{row.actor_name}}</strong><small>{{row.actor_username || '身份未确认'}}</small></div></div></td>
            <td><span class="audit-type">{{row.target_label}}</span><strong class="audit-target">{{row.target_code || row.target_name || (row.record_kind==='business' ? `#${row.target_id}` : row.method)}}</strong><small v-if="row.target_code && row.target_name" class="audit-target-name">{{row.target_name}}</small></td>
            <td class="audit-content"><span class="audit-action">{{row.action_label}}</span><p>{{row.summary}}</p></td>
            <td><span class="status-tag" :class="row.result==='success'?'green':'red'"><i></i>{{row.result==='success'?'成功':'失败 / 被拒绝'}}</span></td>
            <td><button class="text-button audit-detail-button" @click="openDetail(row)" :aria-label="`查看操作记录 ${row.id} 详情`">详情<ChevronRight :size="14" /></button></td>
          </tr></tbody>
        </table>
        <div v-if="!loading && !error && !rows.length" class="audit-empty"><FileClock :size="29" /><strong>{{hasFilters?'没有符合条件的操作记录':'暂无操作记录'}}</strong><p>{{hasFilters?'试试调整人员、动作或日期范围。':'新版上线后的业务变动会显示在这里；历史接口日志可切换上方标签查看。'}}</p><button v-if="hasFilters" class="text-button" @click="resetFilters">清除筛选</button></div>
      </div>
      <div class="pagination-bar"><div class="pagination-summary">共 <strong>{{total}}</strong> 条<span v-if="total"> · 第 {{rangeStart}}–{{rangeEnd}} 条</span></div><label class="page-size-control">每页<select v-model.number="pageSize" aria-label="日志每页条数"><option v-for="size in [20,50,100]" :key="size" :value="size">{{size}}</option></select>条</label><nav class="pagination-controls" aria-label="操作日志分页"><button class="page-button" :disabled="loading || page<=1" @click="page--">上一页</button><template v-for="(n,i) in pageNumbers" :key="i"><span v-if="n==='…'" class="page-ellipsis">…</span><button v-else class="page-button page-number" :class="{active:n===page}" :aria-current="n===page?'page':undefined" :disabled="loading" @click="page=n">{{n}}</button></template><button class="page-button" :disabled="loading || page>=totalPages" @click="page++">下一页</button></nav><span class="pagination-current">{{page}} / {{totalPages}} 页</span></div>
    </section>
    <div v-if="detailOpen" class="modal-backdrop" @click.self="closeDetail">
      <section ref="modalElement" class="modal-panel audit-modal" role="dialog" aria-modal="true" aria-labelledby="audit-detail-title" tabindex="-1">
        <header class="audit-modal-header"><div><span class="audit-eyebrow">操作记录 · 仅管理员可见</span><h2 id="audit-detail-title">操作详情</h2></div><button ref="closeButton" class="icon-button" aria-label="关闭操作详情" @click="closeDetail"><X :size="20" /></button></header>
        <div class="audit-modal-body"><p v-if="detailLoading" class="audit-empty" role="status">正在加载操作详情…</p><p v-else-if="detailError" class="audit-load-error" role="alert">{{detailError}}</p>
          <template v-else-if="detail">
            <div class="audit-event"><span class="audit-action">{{detail.action_label}}</span><h3>{{detail.summary}}</h3><p>{{dateTime(detail.created_at)}}（北京时间）</p></div>
            <dl class="audit-facts"><div><dt>操作人</dt><dd>{{detail.actor_name}}<small>{{detail.actor_username || '身份未确认'}}<template v-if="detail.actor_id"> · 用户 #{{detail.actor_id}}</template></small></dd></div><div><dt>业务对象</dt><dd>{{detail.target_label}}<small>{{detail.target_code || detail.target_name || '—'}}<template v-if="detail.target_id"> · ID {{detail.target_id}}</template></small></dd></div><div><dt>操作结果</dt><dd><span class="status-tag" :class="detail.result==='success'?'green':'red'"><i></i>{{detail.result==='success'?'成功':'失败 / 被拒绝'}}</span></dd></div><div><dt>来源 IP</dt><dd class="mono">{{detail.ip_address || '—'}}</dd></div></dl>
            <div class="audit-change-heading"><h3>{{detail.action==='create'?'创建内容':detail.action==='delete'?'删除前内容':'变更与操作明细'}}</h3><p>保留操作发生时的记录；密码、密钥和执行回填正文不展示具体值。</p></div>
            <div v-if="detail.changes.length" class="audit-changes"><div class="audit-change-head"><span>字段</span><span>操作前</span><span></span><span>操作后</span></div><div v-for="(change,i) in detail.changes" :key="`${change.field}-${i}`" class="audit-change"><strong>{{change.label}}</strong><pre class="audit-before">{{display(change.before)}}</pre><ArrowRight :size="14" /><pre class="audit-after">{{display(change.after)}}</pre></div></div>
            <p v-else class="audit-no-changes">{{detail.record_kind==='request'?'此记录为接口访问或失败尝试，不代表业务数据已修改。':'此操作无需记录字段差异。'}}</p>
            <details class="audit-technical"><summary>接口信息</summary><div><span>{{detail.method}}</span><code>{{detail.path || '—'}}</code><small>HTTP {{detail.status_code}} · 记录 #{{detail.id}}</small></div></details>
          </template>
        </div><footer class="audit-modal-footer"><button class="secondary-button" @click="closeDetail">关闭</button></footer>
      </section>
    </div>
  </div>
</template>

<style scoped>
.audit-panel{overflow:hidden}.audit-toolbar{display:flex;align-items:center;justify-content:space-between;padding:0 22px;border-bottom:1px solid #eaf0f6}.audit-tabs{display:flex;gap:26px}.audit-tabs button{padding:19px 0 16px;border:0;border-bottom:2px solid transparent;background:transparent;color:#8694a9;font-size:12px;cursor:pointer}.audit-tabs button.active{color:#526fc8;border-bottom-color:#617ed8;font-weight:600}.audit-intro{display:flex;align-items:baseline;gap:14px;padding:21px 22px 0}.audit-intro strong{font-size:13px;color:#3c4f6a}.audit-intro>span{font-size:11px;color:#94a1b4;line-height:1.6}.audit-filters{display:flex;flex-wrap:wrap;gap:14px 12px;align-items:flex-end;margin:18px 22px 22px;padding:16px;border:1px solid #e9eef5;border-radius:9px;background:#fafbfd}.audit-filters>label{flex:1 1 135px;min-width:0}.audit-filters>label.audit-keyword{flex:2 1 240px}.audit-filters label>span{display:block;font-size:10px;font-weight:600;color:#7b8ba2;margin-bottom:7px}.audit-filters input,.audit-filters select{height:35px;width:100%;min-width:0;border:1px solid #e2e8f0;border-radius:6px;background:#fff;padding:0 9px;font:inherit;font-size:11px;color:#536781;box-sizing:border-box}.audit-filters input::placeholder{color:#a8b3c2}.audit-keyword .search-box{height:35px;min-width:0;background:#fff}.audit-keyword .search-box input{border:0;height:100%;padding:0;background:transparent;outline:none}.audit-dates{display:flex;align-items:flex-end;gap:9px;flex:2 1 290px}.audit-dates>label{flex:1;min-width:0}.audit-dates>span{font-size:10px;color:#a0adbf;padding-bottom:12px}.audit-reset{height:35px;white-space:nowrap}.audit-reset:disabled{color:#afbac9;cursor:default}.audit-table{min-width:980px}.audit-table thead small{display:block;font-size:9px;font-weight:400;color:#a1afc0;margin-top:4px}.audit-table td{vertical-align:middle;padding-top:17px;padding-bottom:17px}.audit-table .table-date{font-size:10px;white-space:nowrap}.audit-actor{display:flex;align-items:center;gap:9px}.audit-actor .mini-avatar{flex-shrink:0}.audit-actor strong{font-size:11px;display:block}.audit-actor small{font-size:10px;color:#99a6b8;display:block;margin-top:5px}.audit-type{display:block;color:#8f9db2;font-size:10px;margin-bottom:7px}.audit-target{display:block;color:#4e6382;font-size:11px;font-weight:600;overflow-wrap:anywhere;max-width:215px}.audit-target-name{font-size:10px;color:#8d9eb5;display:block;margin-top:5px;overflow-wrap:anywhere;max-width:215px}.audit-content{max-width:340px;min-width:220px}.audit-action{display:inline-block;font-size:10px;line-height:1.5;border-radius:5px;padding:3px 7px;background:#f0f4ff;color:#6179ba;border:1px solid #e4ebfc}.audit-content p{margin:8px 0 0;font-size:11px;color:#6c7e97;line-height:1.8;overflow-wrap:anywhere}.audit-detail-button{display:flex;align-items:center;gap:2px;white-space:nowrap}.audit-empty{padding:45px 22px;text-align:center;color:#9aaac0;font-size:12px}.audit-empty>svg{display:block;margin:0 auto 14px;color:#acb9cd}.audit-empty strong{display:block;font-size:13px;color:#7186a2}.audit-empty p{font-size:11px;line-height:1.9;margin:10px auto;max-width:420px}.audit-load-error{margin:0 22px 18px;padding:13px 16px;border-radius:7px;background:#fff6f4;border:1px solid #f4ded9;color:#b66c5e;font-size:12px}.audit-modal{margin:auto;width:min(860px,94vw);max-height:90vh;display:flex;flex-direction:column;padding:0;overflow:hidden;border-radius:13px}.audit-modal-header{padding:23px 27px 20px;border-bottom:1px solid #edf0f6;display:flex;justify-content:space-between;align-items:center}.audit-eyebrow{font-size:10px;color:#94a4bc;letter-spacing:.5px}.audit-modal-header h2{font-size:18px;margin:7px 0 0;color:#344b6b}.audit-modal-body{padding:24px 27px;overflow:auto;min-height:0}.audit-event{padding:18px 20px;background:#f8faff;border:1px solid #e6edf9;border-radius:9px}.audit-event h3{font-size:14px;line-height:1.9;margin:10px 0 8px;color:#3c5273;overflow-wrap:anywhere}.audit-event p{font-size:10px;color:#8f9fb6;margin:0}.audit-facts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px 28px;margin:25px 0;padding-bottom:23px;border-bottom:1px solid #edf1f7}.audit-facts dt{font-size:10px;color:#96a5ba;margin-bottom:9px}.audit-facts dd{margin:0;font-size:12px;color:#4a6181;overflow-wrap:anywhere}.audit-facts dd small{display:block;font-size:10px;color:#9aabc2;margin-top:7px}.audit-change-heading h3{font-size:13px;color:#41597a;margin:0 0 9px}.audit-change-heading p{font-size:10px;color:#9cabc0;line-height:1.8;margin:0 0 17px}.audit-changes{border:1px solid #e8edf5;border-radius:8px;overflow:hidden}.audit-change-head,.audit-change{display:grid;grid-template-columns:105px minmax(0,1fr) 20px minmax(0,1fr);gap:10px;align-items:start;padding:12px 14px}.audit-change-head{background:#f8fafd;font-size:10px;color:#93a2b8}.audit-change{border-top:1px solid #edf1f6;padding-top:15px;padding-bottom:15px}.audit-change>strong{font-size:11px;font-weight:500;line-height:1.8;color:#6b809f}.audit-change>pre{margin:0;font-family:inherit;white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px;line-height:1.8}.audit-before{color:#94a2b6}.audit-after{color:#4b6f9f}.audit-change>svg{color:#b8c5d6;margin-top:3px}.audit-no-changes{font-size:11px;color:#9cacc2;padding:17px;background:#fafbfd;border-radius:6px;line-height:1.8}.audit-technical{font-size:11px;margin-top:23px;color:#94a4bb}.audit-technical summary{cursor:pointer;user-select:none}.audit-technical>div{display:flex;gap:10px;flex-wrap:wrap;padding:14px;background:#f9fbfe;margin-top:12px;border-radius:6px;align-items:center}.audit-technical code{overflow-wrap:anywhere;color:#7a8ea9}.audit-technical small{margin-left:auto;font-size:10px}.audit-modal-footer{border-top:1px solid #edf1f7;padding:15px 27px;display:flex;justify-content:flex-end}.audit-tabs button:focus-visible,.audit-filters input:focus-visible,.audit-filters select:focus-visible{outline:2px solid #90a7e5;outline-offset:2px}@media(max-width:700px){.audit-overview{flex-wrap:wrap}.audit-overview .summary-stats{margin-left:48px}.audit-intro{align-items:flex-start;flex-direction:column;gap:8px}.audit-toolbar{padding:0 16px}.audit-tabs{gap:20px}.audit-filters{margin-left:14px;margin-right:14px;padding:12px}.audit-filters>label.audit-keyword{flex-basis:100%}.audit-dates{flex-basis:100%}.audit-modal-header{padding:19px}.audit-modal-body{padding:18px}.audit-facts{gap:20px 15px}.audit-change-head,.audit-change{grid-template-columns:70px minmax(0,1fr) 14px minmax(0,1fr);gap:7px;padding-left:9px;padding-right:9px}.audit-event{padding:14px}.audit-event h3{font-size:13px}}
</style>
