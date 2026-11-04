import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { webcrypto } from 'node:crypto'
import { runInNewContext } from 'node:vm'

const source = readFileSync(new URL('../src/client-id.js', import.meta.url), 'utf8')
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
function generator(context) {
  return runInNewContext(source.replace('export function', 'function') + '\ncreateClientId', context)
}

test('secure browsers use the native UUID method with its crypto receiver', () => {
  const createId = generator({ crypto: webcrypto })
  assert.match(createId(), uuid)
})

test('HTTP browsers without randomUUID can generate unique workflow and attachment IDs', () => {
  const createId = generator({ crypto: { getRandomValues: bytes => webcrypto.getRandomValues(bytes) } })
  const ids = Array.from({ length: 1000 }, () => createId())
  assert.equal(new Set(ids).size, ids.length)
  for (const id of ids) assert.match(id, uuid)
})

test('browsers without Web Crypto can add many items within the same millisecond', () => {
  for (const crypto of [undefined, {}]) {
    const createId = generator({ crypto, Date: { now: () => 1 }, Math: { random: () => 0.5 } })
    const ids = Array.from({ length: 1000 }, () => createId())
    assert.equal(new Set(ids).size, ids.length)
    assert.ok(ids.every(id => id.length > 0 && id.length <= 60))
  }
})
