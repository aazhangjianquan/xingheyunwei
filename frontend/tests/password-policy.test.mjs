import { test } from 'node:test'
import assert from 'node:assert/strict'
import { passwordChecks, validPassword } from '../src/password-policy.js'

test('passwords require at least 12 characters and three of four categories', () => {
  for (const value of ['Abcdefghi123', 'Abcdefghij!@', 'abcdefghi12!', 'ABCDEFGHI12!', 'StrongPass12!']) assert.equal(validPassword(value), true, value)
  for (const value of ['', 'Aa1!short', 'abcdefghijkl', 'ABCDEFGHIJKL', '123456789012', 'Abcdefghijkl', '中文中文中文中文1234', 'abcdefghijkl ', 'Aa1!'.repeat(33)]) assert.equal(validPassword(value), false, value)
  assert.deepEqual(passwordChecks('Abcdefghi123'), { length: true, types: [true, true, true, false], count: 3 })
})
