const { test } = require('node:test')
const assert = require('node:assert/strict')
const { calculateNetProfit } = require('../src/domain/financials.js')

test('net profit follows revenue changes using current calibration inputs', () => {
  assert.ok(Math.abs(calculateNetProfit(60, 23.2, 21.9) - 14.9) < 1e-10)
  assert.ok(Math.abs(calculateNetProfit(59, 23.2, 21.9) - 13.9) < 1e-10)
})

test('net profit remains unset until all inputs are available', () => {
  assert.equal(calculateNetProfit(59, null, 21.9), null)
  assert.equal(calculateNetProfit(null, 23.2, 21.9), null)
})
