const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), ts = require('typescript')

function mount(file, name, api, extra = {}) {
  let index = 0, tree, nextTimer = 0
  const hooks = [], pending = [], timers = new Map(), navigations = []
  const navigate = (...args) => navigations.push(args)
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]))
  const jsx = (type, props) => ({ type, props })
  const mocks = {
    react: {
      useState(initial) { const n = index++; if (!(n in hooks)) hooks[n] = initial; return [hooks[n], v => { hooks[n] = typeof v === 'function' ? v(hooks[n]) : v }] },
      useEffect(effect, deps) { const n = index++, old = hooks[n]; if (!same(old?.deps, deps)) pending.push(() => { old?.cleanup?.(); hooks[n] = { deps, cleanup: effect() } }) },
    },
    'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'Fragment' },
    antd: { Alert: 'Alert', Button: 'Button', Card: 'Card', Input: 'Input', Space: 'Space', Spin: 'Spin', Typography: { Title: 'Title', Paragraph: 'Paragraph', Text: 'Text' } },
    'react-router-dom': { useNavigate: () => navigate },
    '../../api/client': { api },
    '../../context/StudentAccountContext': { useStudentAccount: () => ({ real_name: '同学' }) },
    '../../components/StudentScanner': { __esModule: true, default: 'Scanner' },
    './StudentSpacePages': { StudentCasesPage: 'CaseLibrary' },
    ...extra,
  }
  const exports = {}
  const code = ts.transpileModule(fs.readFileSync(path.resolve(__dirname, file), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText
  vm.runInNewContext(code, { exports, require: id => { if (!(id in mocks)) throw Error(id); return mocks[id] }, AbortController, URL, URLSearchParams,
    window: { location: { search: '' }, setTimeout: fn => { timers.set(++nextTimer, fn); return nextTimer }, clearTimeout: id => timers.delete(id) } })
  function render() { index = 0; tree = exports[name](); while (pending.length) pending.shift()() }
  function find(type, node = tree) {
    if (!node || typeof node !== 'object') return []
    if (Array.isArray(node)) return node.flatMap(item => find(type, item ?? null))
    return [...(node.type === type ? [node.props] : []), ...find(type, node.props?.children ?? null)]
  }
  render()
  return { find, navigations,
    async flush() { for (let i = 0; i < 3; i++) { await new Promise(setImmediate); render() } },
    async tick() { const fns = [...timers.values()]; timers.clear(); fns.forEach(fn => fn()); await this.flush() },
    close() { hooks.forEach(h => h?.cleanup?.()) },
    get timerCount() { return timers.size },
  }
}

test('student home shows case library first and keeps optional token and scanner entry', () => {
  const page = mount('../src/pages/student/StudentLandingPage.tsx', 'default', {})
  assert.equal(page.find('CaseLibrary').length, 1)
  assert.equal(page.find('details').length, 1)
  assert.ok(!page.find('details')[0].open)
  assert.equal(page.find('Input').length, 1)
  assert.equal(page.find('Scanner').length, 1)
  page.find('Scanner')[0].onScan('/student/saved-token')
  assert.equal(page.navigations[0][0], '/student/saved-token')
  page.close()
})

test('library discovers newly published cases and opens a case without manual token input', async () => {
  let requests = 0
  const page = mount('../src/pages/student/StudentSpacePages.tsx', 'StudentCasesPage', { get: async url => {
    assert.equal(url, '/student/cases')
    return { data: ++requests === 1 ? [] : [{ id: 9, title: '教师案例', case_type: '战略决策类', version: 2, background: '背景', student_token: 'from-library' }] }
  } })
  await page.flush()
  assert.equal(page.find('Card').length, 0)
  await page.tick()
  assert.equal(page.find('Card')[0]?.title, '教师案例')
  page.find('Button').find(b => b.children === '进入推演').onClick()
  assert.equal(page.navigations[0][0], '/student/from-library')
  assert.equal(page.find('Input').length, 0)
  page.close()
  assert.equal(page.timerCount, 0)
})

test('library retries transient errors and ignores responses after unmount', async () => {
  let requests = 0, resolve
  const page = mount('../src/pages/student/StudentSpacePages.tsx', 'StudentCasesPage', { get: async () => {
    if (++requests === 1) throw Error('offline')
    return new Promise(r => { resolve = r })
  } })
  await page.flush()
  assert.equal(page.find('Alert')[0].message, 'offline')
  await page.tick()
  assert.equal(requests, 2)
  page.close()
  resolve({ data: [{ id: 1, title: 'late-case' }] })
  await page.flush()
  assert.equal(page.find('Card').length, 0)
  assert.equal(page.timerCount, 0)
})
