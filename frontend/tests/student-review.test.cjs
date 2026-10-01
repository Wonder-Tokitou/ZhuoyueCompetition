const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

// Execute the actual page effect with deterministic API responses and virtual timers.
function mount(fetchReview, initial = null) {
  const state = [initial, '', 0, ''], effects = []
  let cursor = 0, cleanup
  const exports = {}
  const mocks = {
    react: { useState: () => { const i = cursor++; return [state[i], (v) => { state[i] = v }] }, useEffect: (f) => effects.push(f) },
    'react/jsx-runtime': { jsx: () => null, jsxs: () => null },
    antd: {},
    'react-router-dom': { useNavigate: () => () => {}, useLocation: () => ({ search: '?session=9' }) },
    './api': { review: fetchReview },
    '../../context/StudentTokenContext': { useStudentToken: () => 'case-token' },
  }
  const filename = path.resolve(__dirname, '../src/features/ai/StudentReview.tsx')
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022,
  } }).outputText
  vm.runInNewContext(code, { exports, require: (name) => {
    if (name in mocks) return mocks[name]
    throw new Error(`Unexpected import ${name}`)
  }, AbortController, DOMException, Promise, Error, URLSearchParams,
  window: { setTimeout: (fn) => { queueMicrotask(fn); return 1 }, clearTimeout: () => {} } })
  // Provide opaque component types without running a DOM renderer.
  mocks.antd.Typography = { Text: () => null, Title: () => null }
  exports.default({ token: 'case-token', sid: 9, onRestart() {} })
  cleanup = effects[0]()
  return { state, unmount: () => cleanup() }
}

test('a review still running after 60 polls is not falsely failed', async () => {
  let requests = 0
  const page = mount(async () => ({ status: ++requests <= 90 ? 'running' : 'succeeded', dimensions: [], conclusion: '完成' }))
  await new Promise(setImmediate)
  page.unmount()
  assert.equal(page.state[1], '')
  assert.equal(page.state[0].status, 'succeeded')
  assert.equal(requests, 91)
})

test('leaving a case discards its in-flight response', async () => {
  let resolve
  const page = mount(() => new Promise((r) => { resolve = r }))
  page.unmount()
  resolve({ status: 'succeeded', conclusion: '旧案例' })
  await new Promise(setImmediate)
  assert.equal(page.state[0], null)
})

test('temporary network errors reconnect instead of failing the review', async () => {
  let n = 0
  const page = mount(async () => {
    if (++n < 3) throw new Error('offline')
    return { status: 'succeeded' }
  })
  await new Promise(setImmediate)
  page.unmount()
  assert.equal(n, 3)
  assert.equal(page.state[0].status, 'succeeded')
  assert.equal(page.state[1], '')
  assert.equal(page.state[3], '')
})

test('permission errors stop polling with a recoverable page error', async () => {
  let n = 0
  const page = mount(async () => { n++; throw Object.assign(new Error('denied'), { status: 403 }) })
  await new Promise(setImmediate)
  page.unmount()
  assert.equal(n, 1)
  assert.equal(page.state[1], 'denied')
})

test('changing cases clears old content before the new response arrives', async () => {
  let resolve
  const page = mount(() => new Promise((r) => { resolve = r }), { status: 'running', conclusion: '旧案例' })
  assert.equal(page.state[0], null)
  page.unmount()
  resolve({ status: 'succeeded' })
  await new Promise(setImmediate)
  assert.equal(page.state[0], null)
})
