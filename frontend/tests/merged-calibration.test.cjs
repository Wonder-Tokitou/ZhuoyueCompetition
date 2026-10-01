const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), ts = require('typescript')
test('merged impact editor preserves five editable rules and their option identity', () => {
  const source = fs.readFileSync(path.join(__dirname, '../src/pages/teacher/components/ProgressivePanel.tsx'), 'utf8')
  const exports = {}, setters = [], values = [{ '7:A': { revenue_pct: 12, margin_pp: -4, expense_pct: 3, cash_pct: -2, share_pp: 1 } }]
  let index = 0, editor
  const jsx = (type, props) => ({ type, props })
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText, {
    exports, require: name => name === 'react' ? { useEffect: () => {}, useRef: () => ({ current: false }), useState: initial => { const i = index++; return [i in values ? values[i] : initial, value => setters.push([i, value])] } }
      : name === 'react/jsx-runtime' ? { jsx, jsxs: jsx }
      : name === 'antd' ? { App: { useApp: () => ({ message: {} }) }, InputNumber: 'InputNumber', Typography: {} } : {},
  })
  exports.default({ detail: { case: { id: 1 }, nodes: [] }, token: 'test', children: render => { editor = render('7:A'); return null } })
  const inputs = editor.props.children[0]
  assert.equal(inputs.length, 5)
  assert.deepEqual(Array.from(inputs, i => i.props.value), [12, -4, 3, -2, 1])
  inputs[0].props.onChange(8)
  const updated = setters.find(([i]) => i === 0)[1](values[0])
  assert.equal(updated['7:A'].revenue_pct, 8)
  assert.equal(updated['7:A'].cash_pct, -2)
})
test('option table hides legacy amounts but retains baseline and cash description', () => {
  const source = fs.readFileSync(path.join(__dirname, '../src/pages/teacher/components/CalibratePanel.tsx'), 'utf8')
  const table = source.slice(source.indexOf('function buildColumns'), source.indexOf('const statusMeta'))
  assert.ok(table.includes("METRIC_FIELDS.filter(field => field.key === 'cash_flow')"))
  assert.ok(!table.includes('record.financial_assumptions'))
  assert.ok(table.includes('renderImpact(`${node.id}:${record.key}`)'))
  assert.ok(source.includes('value={metrics.revenue}'))
  assert.ok(source.includes('columns={buildColumns(node, renderImpact)}'))
})
