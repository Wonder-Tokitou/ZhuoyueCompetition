const { test } = require('node:test')
const assert = require('node:assert/strict')

test('idempotency keys accept Chinese UI values and stay compact ASCII', async () => {
  const { stableIdempotencyKey } = await import('../src/domain/idempotency.js')
  const payload = { base: { revenue: 128, cash_flow: '短期现金流稳健改善' }, operatingCost: 56, operatingExpense: 19 }
  const first = stableIdempotencyKey('nodes-case-4-v7', payload)
  const same = stableIdempotencyKey('nodes-case-4-v7', payload)
  const changed = stableIdempotencyKey('nodes-case-4-v7', { ...payload, base: { ...payload.base, cash_flow: '现金流短期承压' } })

  assert.match(first, /^[\x00-\x7f]+$/)
  assert.ok(first.length < 96)
  assert.equal(first, same)
  assert.notEqual(first, changed)
})
