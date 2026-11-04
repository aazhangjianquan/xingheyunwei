const API = '/api'
const PUBLIC_AUTH_PATHS = new Set(['/login', '/auth/options', '/auth/ticket', '/auth/feishu/start'])
let rateLimitToken, rateLimitUntil = 0

export class ApiError extends Error {
  constructor(message, status, {retryAfter = 0, code = ''} = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.retryAfter = retryAfter
    this.code = code
  }
}

export async function request(path, options = {}, responseType = 'json') {
  const token = localStorage.getItem('yunwei_token')
  if (token !== rateLimitToken) { rateLimitToken = token; rateLimitUntil = 0 }
  const remaining = Math.ceil((rateLimitUntil - Date.now()) / 1000)
  if (remaining > 0) {
    throw new ApiError(`请求过于频繁，请勿连续操作，请在 ${remaining} 秒后重试。`, 429, {retryAfter:remaining, code:'RATE_LIMITED'})
  }
  const response = await fetch(`${API}${path}`, {
    cache: 'no-store',
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {})
    }
  })
  const data = response.ok && responseType === 'blob' ? {} : await response.json().catch(() => ({}))
  const message = typeof data?.detail === 'string' ? data.detail : '请求失败，请稍后重试'
  if (response.status === 429) {
    const seconds = Number(response.headers?.get?.('Retry-After') || data?.retry_after || 1)
    const retryAfter = Number.isFinite(seconds) && seconds > 0 ? Math.min(60, Math.ceil(seconds)) : 1
    if (localStorage.getItem('yunwei_token') === token && rateLimitToken === token) rateLimitUntil = Math.max(rateLimitUntil, Date.now() + retryAfter * 1000)
    throw new ApiError(typeof data?.detail === 'string' ? message : `请求过于频繁，请勿连续操作，请在 ${retryAfter} 秒后重试。`, 429, {retryAfter, code:'RATE_LIMITED'})
  }
  // Only a definitive 401 for the current authenticated session invalidates it.
  // A late response from an older login must not erase a newly issued token.
  if (response.status === 401 && token && !PUBLIC_AUTH_PATHS.has(path.split('?')[0]) && localStorage.getItem('yunwei_token') === token) {
    localStorage.removeItem('yunwei_token')
    window.dispatchEvent(new CustomEvent('yunwei-unauthorized', { detail: { message } }))
  }
  if (!response.ok) {
    throw new ApiError(message, response.status)
  }
  return responseType === 'blob' ? response.blob() : data
}

export const get = (path, options = {}) => request(path, options)
export const getBlob = path => request(path, {}, 'blob')
export const uploadFile = (path, file) => request(`${path}?${new URLSearchParams({filename:file.name})}`, {
  method: 'POST', headers: {'Content-Type':'application/octet-stream'}, body:file,
})
export const post = (path, body, options = {}) => request(path, { ...options, method: 'POST', body: JSON.stringify(body) })
export const put = (path, body, options = {}) => request(path, { ...options, method: 'PUT', body: JSON.stringify(body) })
export const patch = (path, body) => request(path, { method: 'PATCH', body: JSON.stringify(body) })
export const del = (path, body) => request(path, { method: 'DELETE', ...(body === undefined ? {} : {body:JSON.stringify(body)}) })
