let sequence = 0

// UI identifiers only: never use these for authentication or secret tokens.
export function createClientId() {
  const cryptoApi = globalThis.crypto
  if (typeof cryptoApi?.randomUUID === 'function') return cryptoApi.randomUUID()

  // getRandomValues remains available on HTTP, where randomUUID is unavailable.
  if (typeof cryptoApi?.getRandomValues === 'function') {
    const bytes = cryptoApi.getRandomValues(new Uint8Array(16))
    bytes[6] = (bytes[6] & 0x0f) | 0x40
    bytes[8] = (bytes[8] & 0x3f) | 0x80
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
  }

  // Older browsers without Web Crypto still need stable keys within this page.
  return `client-${Date.now().toString(36)}-${(++sequence).toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}
