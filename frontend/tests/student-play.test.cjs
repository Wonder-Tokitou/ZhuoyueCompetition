const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
function play(token) { return { case_title: token, background: '', dilemma: '', base_metrics: null, nodes: [{ id: 1, idx: 1, title: token, background: '', node_role: '', options: [{ key: 'C', label: '渠道策略' }, { key: 'A', label: '定价策略' }, { key: 'B', label: '营销策略' }] }] } }
function session(token) { return { session_id: token === 'a' ? 11 : 22, student_name: '学生', play: play(token), next_node_id: 1, case_version: 1, turns: [] } }

function mount(overrides = {}, saved = false) {
  const hooks = [], pending = [], storage = new Map(saved ? [['student_session:a', '11']] : [])
  let cursor = 0, token = 'a', tree
  const mocks = {
    react: {
      useState(initial) { const i = cursor++; if (!(i in hooks)) hooks[i] = initial; return [hooks[i], (value) => { hooks[i] = typeof value === 'function' ? value(hooks[i]) : value }] },
      useRef(initial) { const i = cursor++; return hooks[i] ||= { current: initial } },
      useEffect(effect, deps) { const i = cursor++, prev = hooks[i]; if (!prev || deps.some((v, j) => !Object.is(v, prev.deps[j]))) pending.push(() => { prev?.cleanup?.(); hooks[i] = { deps, cleanup: effect() } }) },
    },
    'react/jsx-runtime': { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) },
    antd: { Alert: 'Alert', Button: 'Button', Card: 'Card', Input: { TextArea: 'TextArea' }, Progress: 'Progress', Spin: 'Spin', Space: 'Space', Typography: { Paragraph: 'Paragraph', Text: 'Text', Title: 'Title' } },
    'react-router-dom': { useNavigate: () => () => {} },
    '../../context/StudentTokenContext': { useStudentToken: () => token },
    '../../context/StudentAccountContext': { useStudentAccount: () => ({ id: 1, real_name: '学生' }) },
    '../../api/client': { playGet: async (t) => play(t), startStudent: async (t) => session(t), restoreStudent: async (t) => session(t), decide: async () => ({}), ...overrides },
  }
  const code = ts.transpileModule(fs.readFileSync(path.resolve(__dirname, '../src/pages/student/StudentPlayPage.tsx'), 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022,
  } }).outputText
  const exports = {}
  vm.runInNewContext(code, { exports, require: (name) => { if (name in mocks) return mocks[name]; throw new Error(name) },
    Promise, Error, Event, Date, sessionStorage: { getItem: (k) => storage.get(k) || null, setItem: (k, v) => storage.set(k, v) },
    window: { dispatchEvent() {} } })
  function render() { cursor = 0; tree = exports.default(); while (pending.length) pending.shift()() }
  function all(type, node = tree) {
    if (!node || typeof node !== 'object') return []
    if (Array.isArray(node)) return node.flatMap((n) => all(type, n ?? null))
    return [...(node.type === type ? [node.props] : []), ...all(type, node.props?.children ?? null)]
  }
  render()
  return { all, navigate(next) { token = next; render() }, close() { for (const h of hooks) h?.cleanup?.() },
    async flush() { for (let i = 0; i < 4; i++) { await new Promise(setImmediate); render() } } }
}

test('late start response cannot replace the newly selected case', async () => {
  const start = deferred(), page = mount({ startStudent: () => start.promise })
  await page.flush()
  page.all('Button').find((b) => b.children === '开始推演').onClick()
  page.navigate('b'); await page.flush()
  start.resolve(session('a')); await page.flush()
  assert.equal(page.all('Card')[0].title, 'b')
  assert.ok(page.all('Button').some((b) => b.children === '开始推演'))
  page.close()
})

test('late decision response cannot import previous session into another case', async () => {
  const decision = deferred(), page = mount({ decide: () => decision.promise }, true)
  await page.flush()
  page.all('Button').find((b) => b.className === 'strategy-option').onClick()
  await page.flush()
  page.all('Button').find(b => b.children === '确认提交本回合').onClick()
  page.navigate('b'); await page.flush()
  decision.resolve({}); await page.flush()
  assert.ok(page.all('Button').some((b) => b.children === '开始推演'))
  page.close()
})

function options(page) { return page.all('Button').filter(b => b.className === 'strategy-option') }
function button(page, name) { return page.all('Button').find(b => b.children === name) }
function accepted(key = 'C') {
  const saved = session('a')
  saved.play.nodes.push({ ...play('a').nodes[0], id: 2, idx: 2 })
  saved.next_node_id = 2
  saved.turns = [{ node_id: 1, chosen_option: key, after_metrics: null, summary: '与所选策略对应的结果' }]
  return saved
}

test('display A submits its shuffled stable key C exactly once and pauses on the saved strategy', async () => {
  const decision = deferred(), calls = []
  let restores = 0
  const page = mount({ decide: (_token, body) => { calls.push(body); return decision.promise },
    restoreStudent: async () => ++restores === 1 ? session('a') : accepted('C') }, true)
  await page.flush()
  assert.equal(button(page, '确认提交本回合').disabled, true)
  options(page)[0].onClick(); await page.flush()
  const confirm = button(page, '确认提交本回合').onClick
  confirm(); confirm(); await page.flush()
  assert.equal(calls.length, 1)
  assert.equal(calls[0].option_key, 'C')
  assert.equal(options(page).filter(b => b['aria-pressed']).length, 1)
  assert.equal(options(page)[0].type, 'primary')
  decision.resolve({}); await page.flush()
  assert.equal(options(page).length, 0, 'no automatic click-through to the next question')
  const result = page.all('Alert').find(a => a.type === 'success')
  assert.match(JSON.stringify(result), /渠道策略/)
  assert.match(JSON.stringify(result), /你提交的策略/)
  confirm(); await page.flush()
  assert.equal(calls.length, 1, 'stale handlers must not submit again')
  button(page, '继续下一回合').onClick(); await page.flush()
  assert.equal(options(page).filter(b => b['aria-pressed']).length, 0)
  assert.equal(button(page, '确认提交本回合').disabled, true)
  page.close()
})

test('selection can change before confirmation; displayed B posts key A, not array index', async () => {
  const calls = []
  const page = mount({ decide: async (_t, body) => { calls.push(body) } }, true)
  await page.flush()
  options(page)[0].onClick(); await page.flush()
  options(page)[1].onClick(); await page.flush()
  assert.equal(calls.length, 0)
  assert.deepEqual(options(page).map(b => b['aria-pressed']), [false, true, false])
  button(page, '确认提交本回合').onClick(); await page.flush()
  assert.equal(calls[0].option_key, 'A')
  page.close()
})

test('a lost POST response restores the accepted choice without sending a second decision', async () => {
  let restores = 0, posts = 0
  const page = mount({ decide: async () => { posts++; throw new Error('response lost') },
    restoreStudent: async () => ++restores === 1 ? session('a') : accepted() }, true)
  await page.flush(); options(page)[0].onClick(); await page.flush()
  button(page, '确认提交本回合').onClick(); await page.flush()
  assert.ok(button(page, '继续下一回合'))
  assert.equal(posts, 1)
  assert.equal(page.all('Alert').filter(a => a.type === 'warning').length, 0)
  page.close()
})

test('uncertain network failure locks submission until server reconciliation', async () => {
  let restores = 0, posts = 0, online = false
  const page = mount({ decide: async () => { posts++; throw new Error('offline') },
    restoreStudent: async () => { if (++restores === 1) return session('a'); if (!online) throw new Error('offline'); return session('a') } }, true)
  await page.flush(); options(page)[2].onClick(); await page.flush()
  button(page, '确认提交本回合').onClick(); await page.flush()
  assert.ok(button(page, '核对已保存进度'))
  assert.ok(options(page).every(b => b.disabled))
  options(page)[0].onClick(); await page.flush()
  assert.equal(options(page)[2]['aria-pressed'], true)
  online = true
  button(page, '核对已保存进度').onClick(); await page.flush()
  assert.equal(posts, 1)
  assert.equal(button(page, '确认提交本回合').disabled, false)
  assert.equal(options(page)[2]['aria-pressed'], true)
  page.close()
})

test('server conflict shows actual accepted strategy, not the attempted choice', async () => {
  let restores = 0
  const page = mount({ decide: async () => { throw new Error('409') },
    restoreStudent: async () => ++restores === 1 ? session('a') : accepted('A') }, true)
  await page.flush(); options(page)[0].onClick(); await page.flush()
  button(page, '确认提交本回合').onClick(); await page.flush()
  assert.match(page.all('Alert').find(a => a.type === 'warning').message, /另一页面提交/)
  assert.match(JSON.stringify(page.all('Alert').find(a => a.type === 'success')), /定价策略/)
  page.close()
})

test('decision reason is cleared when navigating to a different case', async () => {
  const page = mount({}, true)
  await page.flush()
  page.all('TextArea')[0].onChange({ target: { value: '只属于案例甲的理由' } })
  page.navigate('b'); await page.flush()
  page.all('Button').find((b) => b.children === '开始推演').onClick(); await page.flush()
  assert.equal(page.all('TextArea')[0].value, '')
  page.close()
})

test('double start before rerender only sends one create request', async () => {
  let count = 0
  const start = deferred(), page = mount({ startStudent: () => { count++; return start.promise } })
  await page.flush()
  const click = page.all('Button').find((b) => b.children === '开始推演').onClick
  click(); click()
  start.resolve(session('a')); await page.flush()
  assert.equal(count, 1)
  page.close()
})

test('choosing displayed A highlights only that strategy without committing or greying all options', async () => {
  let count = 0
  const decision = deferred()
  const page = mount({ decide: () => { count++; return decision.promise } }, true)
  await page.flush()
  page.all('Button').find(b => b.className === 'strategy-option').onClick()
  await page.flush()
  const options = page.all('Button').filter(b => b.className === 'strategy-option')
  assert.equal(options.filter(b => b['aria-pressed'] === true).length, 1, 'one visibly selected choice required')
  assert.equal(options.filter(b => b.disabled).length, 0, 'selection must remain changeable until confirmation')
  assert.equal(count, 0, 'touching an option must not silently submit a turn')
  page.close()
})
