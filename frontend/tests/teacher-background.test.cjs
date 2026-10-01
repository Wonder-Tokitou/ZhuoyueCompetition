const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), ts = require('typescript')

test('save sync keeps calibration mounted while waiting, preserves draft revision and updates publish data', async () => {
  let reads=0, resolve
  const page=mount({listCases:async()=>[{id:1,status:'ready'}], listCaseAiTasks:async()=>[],
    getCase:async()=> ++reads===1 ? {case:{id:1,status:'ready',version:1},nodes:[]} : new Promise(r=>{resolve=r})})
  await page.flush()
  const before=page.find('CalibratePanel')
  const saving=before.onSaveRefresh(); await page.flush()
  assert.ok(page.find('CalibratePanel'), 'must not unmount editor during save')
  assert.equal(page.find('CalibratePanel').draftRevision,before.draftRevision)
  resolve({case:{id:1,status:'ready',version:2},nodes:[]}); await saving; await page.flush()
  assert.equal(page.find('CalibratePanel').draftRevision,before.draftRevision)
  assert.equal(page.find('PublishPanel').detail.case.version,2)
  page.close()
})

test('late save sync cannot replace a different selected case',async()=>{
  let reads=0,resolve
  const page=mount({listCases:async()=>[{id:1,status:'ready'},{id:2,status:'ready'}],listCaseAiTasks:async()=>[],
    getCase:async(id)=>id===1 && ++reads>1 ? new Promise(r=>{resolve=r}) : {case:{id,status:'ready',version:1},nodes:[]}})
  await page.flush(); const saving=page.find('CalibratePanel').onSaveRefresh(); await page.flush()
  page.find('Select').onChange(2); await page.flush()
  resolve({case:{id:1,status:'ready',version:2},nodes:[]}); await saving; await page.flush()
  assert.equal(page.find('CalibratePanel').detail.case.id,2);page.close()
})

test('save synchronization does not trigger global draft reset; generation retains explicit refresh',()=>{
  const source=fs.readFileSync(path.resolve(__dirname,'../src/pages/teacher/components/CalibratePanel.tsx'),'utf8')
  assert.equal((source.match(/await \(onSaveRefresh \?\? onRefresh\)\(\)/g)||[]).length,2)
  assert.ok(source.includes('draftRevision === undefined ? detail : draftRevision'))
  assert.ok(source.includes('await refreshRef.current()'))
})

function mount(api, storage = new Map()) {
  let index = 0, tree, timerId = 0
  const hooks = [], pending = [], timers = new Map(), messages = []
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]))
  const react = {
    useState(initial) { const n = index++; if (!(n in hooks)) hooks[n] = typeof initial === 'function' ? initial() : initial; return [hooks[n], (v) => { hooks[n] = typeof v === 'function' ? v(hooks[n]) : v }] },
    useRef(initial) { return hooks[index++] ||= { current: initial } },
    useCallback(fn, deps) { const n = index++; if (!same(hooks[n]?.deps, deps)) hooks[n] = { deps, fn }; return hooks[n].fn },
    useEffect(effect, deps) { const n = index++, old = hooks[n]; if (!same(old?.deps, deps)) pending.push(() => { old?.cleanup?.(); hooks[n] = { deps, cleanup: effect() } }) },
  }
  const jsx = (type, props) => typeof type === 'function' ? type(props) : ({ type, props })
  const message = { success: (s) => messages.push(s), warning: (s) => messages.push(s), error: (s) => messages.push(s) }
  const mocks = { react, 'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'Fragment' },
    antd: { App: { useApp: () => ({ message }) }, Card: 'Card', Button: 'Button', Alert: 'Alert', Select: 'Select', Space: 'Space', Spin: 'Spin', Tag: 'Tag', Typography: { Title: 'Title', Text: 'Text' } },
    '../../api/client': api, '../../features/ai/api': api, '../../context/TeacherTokenContext': { useTeacherToken: () => 'test' },
    './teacherConstants': { STATUS_META: Object.fromEntries(['draft', 'generating', 'ready', 'published'].map((s) => [s, { text: s }])) },
    './teacher.css': {},
  }
  for (const c of ['AiFixDrawer', 'CalibratePanel', 'GeneratePanel', 'PublishPanel']) mocks[(['AiFixDrawer', 'GeneratePanel'].includes(c) ? '../../features/ai/' : './components/') + c] = { __esModule: true, default: c }
  const statusModule = { exports: {} }
  const statusCode = ts.transpileModule(fs.readFileSync(path.resolve(__dirname, '../src/features/ai/TaskStatus.tsx'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText
  vm.runInNewContext(statusCode, { exports: statusModule.exports, require: (id) => mocks[id] })
  mocks['../../features/ai/TaskStatus'] = statusModule.exports
  const exports = {}
  const file = path.resolve(__dirname, '../src/pages/teacher/TeacherWorkbenchPage.tsx')
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText
  vm.runInNewContext(code, { exports, require: (name) => { if (!(name in mocks)) throw Error(name); return mocks[name] },
    sessionStorage: { getItem: (k) => storage.get(k) || null, setItem: (k, v) => storage.set(k, v) },
    window: { setTimeout: (fn) => { timers.set(++timerId, fn); return timerId }, clearTimeout: (id) => timers.delete(id) },
  })
  function render() { index = 0; tree = exports.default(); while (pending.length) pending.shift()() }
  function find(type, node = tree) { if (!node || typeof node !== 'object') return null; if (Array.isArray(node)) { for (const item of node) { const r = find(type, item ?? null); if (r) return r } return null } return node.type === type ? node.props : find(type, node.props?.children ?? null) }
  render()
  return { find, messages, storage,
    async flush() { for (let i = 0; i < 5; i++) { await new Promise(setImmediate); render() } },
    async tick() { const list = [...timers.values()]; timers.clear(); list.forEach((fn) => fn()); await this.flush() },
    close() { hooks.forEach((h) => h?.cleanup?.()) },
  }
}

test('remount discovers persisted job and keeps monitoring beyond 60 polls without resubmitting', async () => {
  let reads = 0, creates = 0
  const api = { listCases: async () => [{ id: 1, title: '案例', status: 'generating' }],
    createCase: async () => { creates++; return { case_id: 1 } },
    getCase: async () => ({ case: { id: 1, status: ++reads > 65 ? 'ready' : 'generating' }, nodes: [] }),
    listCaseAiTasks: async () => [{ task_id: 'same-job', status: reads > 65 ? 'succeeded' : 'running', stage: 'generate_case', attempts: 1 }],
  }
  const first = mount(api); await first.flush()
  assert.equal(first.find('Select').value, 1)
  first.close()
  const second = mount(api, first.storage); await second.flush()
  for (let n = 0; n < 70; n++) await second.tick()
  assert.ok(reads > 65)
  assert.equal(creates, 0)
  assert.equal(second.find('CalibratePanel').detail.case.status, 'ready')
  assert.ok(!second.messages.some((m) => m.includes('超时')))
  second.close()
})

test('old selected case response cannot overwrite the newly selected case', async () => {
  let resolve
  const page = mount({ listCases: async () => [{ id: 1, status: 'generating' }, { id: 2, status: 'ready' }],
    getCase: (id) => id === 1 ? new Promise((r) => { resolve = r }) : Promise.resolve({ case: { id: 2, status: 'ready' }, nodes: [] }),
    listCaseAiTasks: async () => [],
  })
  await page.flush()
  page.find('Select').onChange(2); await page.flush()
  resolve?.({ case: { id: 1, status: 'generating' }, nodes: [] }); await page.flush()
  assert.equal(page.find('Select').value, 2)
  assert.equal(page.find('CalibratePanel').detail.case.id, 2)
  page.close()
})

test('temporary progress errors reconnect without creating another job', async () => {
  let reads = 0
  const page = mount({ listCases: async () => [{ id: 1, status: 'generating' }],
    getCase: async () => { if (++reads < 3) throw Error('offline'); return { case: { id: 1, status: 'ready' }, nodes: [] } },
    listCaseAiTasks: async () => [{ status: 'succeeded', stage: 'completed', attempts: 1 }],
    createCase: () => { throw Error('must not resubmit') },
  })
  await page.flush(); await page.tick(); await page.tick()
  assert.equal(page.find('CalibratePanel').detail.case.id, 1)
  assert.equal(reads, 3)
  page.close()
})

test('confirmed upload returns immediately and starts independent progress monitoring', async () => {
  let resolve, creates = 0
  const page = mount({ listCases: async () => [],
    createCase: () => { creates++; return new Promise((r) => { resolve = r }) },
    getCase: async () => ({ case: { id: 8, status: 'generating' }, nodes: [] }),
    listCaseAiTasks: async () => [{ status: 'running', stage: 'generate_case', attempts: 1 }],
  })
  await page.flush()
  const submit = page.find('GeneratePanel').onGenerate
  const accepted = submit({ title: 'test' })
  assert.equal(await submit({ title: 'duplicate' }), false)
  resolve({ case_id: 8, task_id: 'persistent' })
  assert.equal(await accepted, true)
  await page.flush()
  assert.equal(page.find('GeneratePanel').generating, false)
  assert.equal(page.find('Select').value, 8)
  assert.equal(creates, 1)
  page.close()
})

test('failed persisted task shows reason and stops progress polling', async () => {
  let reads = 0
  const page = mount({ listCases: async () => [{ id: 1, status: 'draft' }],
    getCase: async () => { reads++; return { case: { id: 1, status: 'draft' }, nodes: [] } },
    listCaseAiTasks: async () => [{ status: 'failed', stage: 'failed', attempts: 1, error: 'V2: 节点不完整' }],
  })
  await page.flush(); await page.tick(); await page.tick()
  assert.equal(reads, 1)
  assert.equal(page.find('Alert').description, 'V2: 节点不完整')
  page.close()
})

test('list connection warning clears after recovery even when selected job is finished', async () => {
  let lists = 0
  const page = mount({ listCases: async () => { if (++lists === 2) throw Error('offline'); return [{ id: 1, status: 'ready' }] },
    getCase: async () => ({ case: { id: 1, status: 'ready' }, nodes: [] }),
    listCaseAiTasks: async () => [{ status: 'succeeded', stage: 'completed', attempts: 1 }],
  })
  await page.flush(); await page.tick()
  assert.equal(page.find('Alert').type, 'warning')
  await page.tick()
  assert.equal(page.find('Alert').type, 'success')
  page.close()
})
