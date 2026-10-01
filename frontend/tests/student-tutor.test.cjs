const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

// Runs the real component and its effects against controlled API promises.
// This is a logic regression harness, not a substitute for browser layout QA.
function mount(records) {
  const hooks = [], pending = [], storage = new Map([['student_session:case-a', '11']])
  let index = 0, location = { pathname: '/student/case-a', search: '' }, tree
  const calls = []
  const react = {
    useState(initial) {
      const n = index++
      if (!(n in hooks)) hooks[n] = typeof initial === 'function' ? initial() : initial
      return [hooks[n], (value) => { hooks[n] = typeof value === 'function' ? value(hooks[n]) : value }]
    },
    useRef(initial) { const n = index++; return hooks[n] ||= { current: initial } },
    useEffect(effect, deps) {
      const n = index++, previous = hooks[n]
      if (!previous || deps.some((v, i) => !Object.is(v, previous.deps[i]))) {
        pending.push(() => { previous?.cleanup?.(); hooks[n] = { deps, cleanup: effect() } })
      }
    },
  }
  const jsx = (type, props) => ({ type, props })
  const input = { TextArea: 'TextArea' }
  const mocks = {
    react, 'react/jsx-runtime': { jsx, jsxs: jsx },
    antd: { Input: input, Alert: 'Alert', Button: 'Button', Card: 'Card', Select: 'Select', Space: 'Space', Typography: { Paragraph: 'Paragraph' } },
    'react-router-dom': { useLocation: () => location },
    '../../api/transport': { api: { get: (url) => { calls.push(url); return url === '/student/records' ? records() : Promise.resolve({ data: [] }) } }, chatStudent: async () => {} },
    './api': { chatStudent: async () => {} },
  }
  const filename = path.resolve(__dirname, '../src/features/ai/StudentTutorSidebar.tsx')
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022,
  } }).outputText
  const exports = {}
  vm.runInNewContext(code, { exports, require: (name) => {
    if (name in mocks) return mocks[name]
    throw new Error(`Unexpected import ${name}`)
  }, AbortController, Promise, Error, URLSearchParams,
  sessionStorage: { getItem: (key) => storage.get(key) || null },
  window: { addEventListener() {}, removeEventListener() {} } })
  function render() { index = 0; tree = exports.default(); while (pending.length) pending.shift()() }
  function find(type, node = tree) {
    if (!node || typeof node !== 'object') return null
    if (Array.isArray(node)) { for (const item of node) { const result = find(type, item); if (result) return result } return null }
    return node.type === type ? node.props : find(type, node.props?.children ?? null)
  }
  render()
  return { calls, find, navigate(pathname, search = '') { location = { pathname, search }; render() },
    async flush() { for (let i = 0; i < 4; i++) { await new Promise(setImmediate); render() } },
    close() { for (const hook of hooks) hook?.cleanup?.() } }
}

const a = { session_id: 11, student_token: 'case-a', case_title: '案例甲', available: true, attempt_no: 1 }
const b = { session_id: 22, student_token: 'case-b', case_title: '案例乙', available: true, attempt_no: 1 }

test('a new case never inherits the prior case as tutor evidence', async () => {
  const page = mount(async () => ({ data: [a] }))
  await page.flush()
  assert.equal(page.find('Select').value, 11)
  page.navigate('/student/case-b')
  await page.flush()
  assert.equal(page.find('Select').value, null)
  assert.equal(page.find('Select').options.length, 0)
  page.close()
})

test('session query cannot select evidence from a different case', async () => {
  const page = mount(async () => ({ data: [a, b] }))
  await page.flush()
  page.navigate('/student/case-b/review', '?session=11')
  await page.flush()
  assert.notEqual(page.find('Select').value, 11)
  assert.equal(page.find('Select').options.length, 1)
  page.close()
})

test('personal space still allows choosing among all own saved cases', async () => {
  const page = mount(async () => ({ data: [a, b] }))
  page.navigate('/student/records')
  await page.flush()
  assert.equal(page.find('Select').options.length, 2)
  page.close()
})

test('navigation disables stale evidence while the new records request is pending', async () => {
  let resolve, calls = 0
  const page = mount(() => ++calls === 1 ? Promise.resolve({ data: [a] }) : new Promise((r) => { resolve = r }))
  await page.flush()
  page.find('TextArea').onChange({ target: { value: '现金流?' } })
  page.navigate('/student/case-b')
  assert.equal(page.find('Select').value, null)
  assert.equal(page.find('Button').disabled, true)
  resolve({ data: [a, b] })
  await page.flush()
  assert.equal(page.find('Select').value, 22)
  page.close()
})
