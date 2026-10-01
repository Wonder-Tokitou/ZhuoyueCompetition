const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), ts = require('typescript')
const React = require('react'), { renderToStaticMarkup } = require('react-dom/server')

function load(file, mocks = {}) {
  const exports = {}
  const code = ts.transpileModule(fs.readFileSync(path.resolve(__dirname, file), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText
  vm.runInNewContext(code, { exports, require: (id) => id in mocks ? mocks[id] : require(id) })
  return exports
}

test('metrics render as labelled before/after rows with zero preserved and missing values explicit', () => {
  const Component = load('../src/components/MetricComparison.tsx').default
  const html = renderToStaticMarkup(React.createElement(Component, { before: { revenue: 0, gross_margin: '0.0%', cash_flow: '收紧' }, after: { revenue: 12.2, gross_margin: '60.5%', cash_flow: '改善' } }))
  for (const text of ['<table', '决策前', '决策后', '营收（万元）', '毛利率', '现金流', '12.2', '>0<', '未记录']) assert.ok(html.includes(text), text)
  assert.ok(!html.includes('revenue') && !html.includes('gross_margin'))
})

test('assistant hides citation tokens and generated evidence without changing user text', () => {
  const { TutorMessage } = load('../src/features/ai/StudentTutorSidebar.tsx', { '../../api/transport': {}, './api': {}, 'react-router-dom': {} })
  const content = '现金流改善。[read_results]\n\n依据：\n[read_results] 教师版本 1\n营收：12 万元\n<script>alert(1)</script>'
  const html = renderToStaticMarkup(React.createElement(TutorMessage, { content }))
  assert.ok(html.includes('现金流改善。') && !html.includes('<details'))
  for (const removed of ['营收：12 万元', 'read_results', '【已提交结果】', '依据：', '<script>']) assert.ok(!html.includes(removed))
  const userHtml = renderToStaticMarkup(React.createElement(TutorMessage, { content, assistant: false }))
  assert.ok(!userHtml.includes('<details>'))
  assert.ok(userHtml.includes('[read_results]'))
  const legacy = renderToStaticMarkup(React.createElement(TutorMessage, { content: '依据[read\\_result][read_node][read_rules][web_1]' }))
  assert.ok(!legacy.includes('read') && !legacy.includes('web_'))
  for (const label of ['已提交结果', '当前节点', '教学规则', '外部资料 1']) assert.ok(!legacy.includes(label))
  for (const content of [
    '利润改善。【案例资料】【当前节点】【已提交结果】【教学规则】【外部资料 2】【资料依据】\n\n资料：\n  资料：10\n  资料：0.6',
    '利润改善。\r\n\r\n依据：\r\n资料：10',
    '利润改善。\n\n资料：',
  ]) {
    const cleaned = renderToStaticMarkup(React.createElement(TutorMessage, { content }))
    assert.ok(cleaned.includes('利润改善。'))
    for (const removed of ['【', '资料：', '依据：', '0.6']) assert.ok(!cleaned.includes(removed))
  }
  const safe = renderToStaticMarkup(React.createElement(TutorMessage, { content: '资料：行业分析很重要。\n利润为 -2.5 万元，参考 https://example.com [普通文字] <script>alert(1)</script>' }))
  for (const kept of ['资料：行业分析很重要。', '-2.5', 'https://example.com', '[普通文字]', '&lt;script&gt;']) assert.ok(safe.includes(kept))
  assert.ok(!safe.includes('<script>'))
})
