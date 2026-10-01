const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), ts = require('typescript')
test('saved green result renders actual progressive amounts including zero and negatives', () => {
  const source = fs.readFileSync(path.join(__dirname,'../src/pages/student/StudentPlayPage.tsx'),'utf8')
  const code = ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText
  const exports = {}
  vm.runInNewContext(code,{exports,require:n=>n==='react/jsx-runtime'?{jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props})}:{}})
  function text(n) { if(n == null || n === false)return ''; if(Array.isArray(n))return n.map(text).join(''); return typeof n==='object'?text(n.props?.children):String(n) }
  const actual = text(exports.MetricsBar({metrics:{revenue:133.1,gross_margin:'54.0%',market_share:'20.0%',cash_flow:'本期流出',net_profit:-12.345,cash_flow_amount:0}}))
  assert.ok(actual.includes('133.1'))
  assert.ok(actual.includes('净利润 -12.35 万元'))
  assert.ok(actual.includes('本期现金净流量 0.00 万元'))
  const old = text(exports.MetricsBar({metrics:{revenue:60,gross_margin:'62%',market_share:'20%',cash_flow:'方向'}}))
  assert.ok(!old.includes('NaN'))
  assert.ok(!old.includes('净利润'))
  assert.ok(source.includes('<MetricsBar metrics={last.after_metrics} />'))
})
test('teacher fields labelled and publication checks saved progressive config', () => {
  const panel = fs.readFileSync(path.join(__dirname,'../src/pages/teacher/components/CalibratePanel.tsx'),'utf8')
  assert.ok(panel.includes('addonBefore={field.label}'))
  assert.ok(panel.includes('value={metricAnnotation(String(metrics[field.key]'))
  assert.ok(panel.includes('<Typography.Text type="secondary"'))
  const publish = fs.readFileSync(path.join(__dirname,'../src/pages/teacher/components/PublishPanel.tsx'),'utf8')
  assert.ok(publish.indexOf('if (!simulation.draft?.enabled)') < publish.indexOf('await publishCase('))
})
