import { computed, ref, watch } from 'vue'
import { post, put } from './api.js'

export const WIKI_TITLE_LIMIT = 180
export const WIKI_CATEGORY_LIMIT = 60
export const wikiTextLength = value => Array.from(value || '').length

// Only editable fields participate in dirty checks; metadata from a save response
// must never replace text typed while that request was in flight.
const payloadOf = form => ({
  title: (form.title || '').trim(), category: (form.category || '').trim(),
  content: form.content || '', space_id: form.space_id, parent_id: form.parent_id || null,
  status: form.status || 'draft',
})
const fingerprint = form => JSON.stringify(payloadOf(form))
const valid = form => Boolean(form.title && form.category && form.content.trim() && form.space_id)

export function useWikiEditor({form, userId, browser, onSaved = () => {}, onDenied = () => {}, api = {post, put}, delay = 1000}) {
  const active = ref(false), syncing = ref(false), error = ref(''), conflict = ref(false)
  const lastSaved = ref(''), restored = ref(false), backedUp = ref(false), localError = ref('')
  const baseline = ref('')
  const dirty = computed(() => active.value && fingerprint(form.value) !== baseline.value)
  const isNew = computed(() => !form.value.id)
  const fieldError = computed(() => {
    if (wikiTextLength(form.value.title) > WIKI_TITLE_LIMIT) return `文档标题最多 ${WIKI_TITLE_LIMIT} 个字符，请缩短后保存；当前草稿已保留。`
    if (wikiTextLength(form.value.category) > WIKI_CATEGORY_LIMIT) return `内容分类最多 ${WIKI_CATEGORY_LIMIT} 个字符，请缩短后保存；当前草稿已保留。`
    return ''
  })
  const complete = computed(() => valid(payloadOf(form.value)) && !fieldError.value)
  const writer = Math.random().toString(36).slice(2) + Date.now().toString(36)
  let generation = 0, remoteVersion, remoteRevision, timer, pending = null, applying = false, retries = 0, lastKey
  const keyFor = value => `xinghe:wiki-draft:v1:${userId}:${value.space_id}:${value.id || 'new'}`
  const storage = () => browser.localStorage
  function read(key) {
    try { return JSON.parse(storage()?.getItem(key) || 'null') }
    catch { return null }
  }
  function remove(key, force = false) {
    try {
      if (force || read(key)?.writer === writer) storage()?.removeItem(key)
    } catch { /* A failed delete is harmless; opening revalidates access. */ }
  }
  function persist() {
    if (!active.value) return
    const key = keyFor(form.value)
    if (lastKey && lastKey !== key) remove(lastKey)
    lastKey = key
    if (!dirty.value) { remove(key); backedUp.value = false; return }
    try {
      if (!storage()) throw new Error('storage unavailable')
      storage().setItem(key, JSON.stringify({
        writer, baseVersion: remoteVersion, baseRevision: remoteRevision, form: {...payloadOf(form.value), id: form.value.id},
        savedAt: new Date().toISOString(),
      }))
      backedUp.value = true; localError.value = ''
    } catch {
      backedUp.value = false
      localError.value = '浏览器草稿保存失败，请立即保存到服务器或下载草稿后再离开。'
    }
  }
  function schedule(wait = delay) {
    clearTimeout(timer)
    if (!active.value || !dirty.value || isNew.value || conflict.value || !complete.value || pending) return
    timer = setTimeout(() => void save(), wait)
  }
  const stopWatching = watch(form, () => {
    if (!active.value || applying) return
    persist(); schedule()
  }, {deep: true, flush: 'sync'})

  function stop({forget = false} = {}) {
    if (active.value) {
      if (forget) remove(keyFor(form.value), true)
      else persist()
    }
    active.value = false; ++generation; clearTimeout(timer); pending = null; syncing.value = false
  }
  function start(remote) {
    stop()
    applying = true
    form.value = {...remote}; baseline.value = fingerprint(remote); remoteVersion = remote.version; remoteRevision = remote.revision; lastKey = keyFor(remote)
    error.value = ''; localError.value = ''; conflict.value = false; restored.value = false
    lastSaved.value = remote.updated_at || ''; backedUp.value = false; retries = 0
    const key = keyFor(remote), draft = read(key)
    // Restoring is called only after current server-side access verification.
    if (draft?.form?.space_id === remote.space_id && draft.form.id === remote.id &&
        ['title', 'category', 'content'].every(field => typeof draft.form[field] === 'string') &&
        ['draft', 'published', 'archived'].includes(draft.form.status)) {
      if (fingerprint(draft.form) !== baseline.value) {
        form.value = {...remote, ...payloadOf(draft.form)}
        remoteVersion = draft.baseVersion; remoteRevision = draft.baseRevision; restored.value = true
        if (remote.id && (draft.baseVersion !== remote.version || draft.baseRevision !== remote.revision)) {
          conflict.value = true
          error.value = '已恢复本地草稿，但服务器文档已有更新。请下载草稿并读取最新版本后合并，避免覆盖他人修改。'
        }
      } else remove(key, true)
    }
    applying = false; active.value = true
    persist(); schedule()
  }

  async function save({create = false, manual = false} = {}) {
    clearTimeout(timer)
    if (pending) {
      const current = generation
      const ok = await pending
      if (!ok || current !== generation || !active.value) return false
      return (manual || dirty.value) ? save({create, manual}) : true
    }
    if (!active.value || conflict.value) return false
    if (fieldError.value) { error.value = fieldError.value; persist(); return false }
    if (!isNew.value && !dirty.value && !manual) return true
    if (!complete.value) {
      error.value = '请填写标题、内容分类和正文；尚未完成的内容会保留为本地草稿。'
      persist(); return false
    }
    // Unsaved new documents stay local until the user explicitly creates them.
    // This also avoids duplicate documents after an ambiguous POST failure.
    if (isNew.value && !create) { persist(); return false }
    const current = generation, id = form.value.id, oldKey = keyFor(form.value)
    const snapshot = payloadOf(form.value), expectedVersion = remoteVersion, expectedRevision = remoteRevision
    syncing.value = true; error.value = ''
    pending = (async () => {
      try {
        const options = {signal: AbortSignal.timeout(15000)}
        const result = id ? await api.put(`/wiki/${id}`, {...snapshot, version: expectedVersion, revision: expectedRevision, save_mode: manual ? 'manual' : 'auto'}, options)
          : await api.post('/wiki', snapshot, options)
        if (current !== generation || !active.value) return false
        baseline.value = fingerprint(snapshot); remoteVersion = result.version; remoteRevision = result.revision
        applying = true
        form.value = {...form.value, id: result.id, version: result.version, revision: result.revision, updated_at: result.updated_at}
        applying = false
        lastSaved.value = result.updated_at || new Date().toISOString()
        restored.value = false; localError.value = ''; retries = 0
        remove(oldKey); persist(); onSaved(result)
        return true
      } catch (failure) {
        if (current !== generation || !active.value) return false
        error.value = failure.name === 'TimeoutError' ? '自动保存超时，将自动重试。' : failure.message || '自动保存失败，将自动重试。'
        if ([403, 404].includes(failure.status)) {
          stop({forget: true}); onDenied(failure)
        } else {
          conflict.value = failure.status === 409
          ++retries; persist()
        }
        return false
      } finally {
        if (current === generation) {
          pending = null; syncing.value = false
          schedule(retries ? Math.min(30000, 5000 * 2 ** Math.min(retries - 1, 3)) : delay)
        }
      }
    })()
    return pending
  }
  const status = computed(() => {
    if (conflict.value) return '版本冲突，已暂停同步'
    if (syncing.value) return '正在保存到服务器…'
    if (dirty.value) {
      if (localError.value) return '内容尚未保存'
      if (fieldError.value) return '内容超出字数限制，草稿已保留'
      if (isNew.value) return '草稿已保存在此浏览器'
      if (error.value) return backedUp.value ? '同步失败，本地草稿已保留' : '同步失败，请勿离开'
      return complete.value ? '本地草稿已保存，等待同步…' : '内容尚未填写完整，已保留本地草稿'
    }
    return lastSaved.value ? '所有修改已保存到服务器' : '输入后自动保留草稿'
  })
  function beforeUnload(event) {
    persist()
    if (dirty.value) { event.preventDefault(); event.returnValue = '' }
  }
  function retry() { if (active.value && !isNew.value) void save() }
  function pageHide() { persist() }
  browser.addEventListener('beforeunload', beforeUnload)
  browser.addEventListener('pagehide', pageHide)
  browser.addEventListener('online', retry)
  function dispose() {
    stop(); stopWatching()
    browser.removeEventListener('beforeunload', beforeUnload)
    browser.removeEventListener('pagehide', pageHide)
    browser.removeEventListener('online', retry)
  }
  return {active, dirty, syncing, error, fieldError, conflict, localError, lastSaved, restored, backedUp, status, start, save, stop, persist, retry, dispose}
}
