const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path'), ts = require('typescript')
const rules = { '1:A': { revenue_pct: 12, margin_pp: -2, expense_pct: 3, cash_pct: -4, share_pp: 1 } }
const reasoning = { '1:A': { basis: '教学假设', is_assumption: true, caveat: '待核对' } }
const detail = { case: { id: 1, version: 1 }, nodes: [1,2,3].map(id => ({ id, idx: id, options: ['A','B','C'].map(key => ({ key, label: key })) })) }
function mount({ draft = null, delayed = false } = {}) {
  const hooks = [], pending = [], calls = []
  let cursor = 0, tree, resolve, current = detail
  const output = delayed ? new Promise(r => { resolve = r }) : Promise.resolve({ data: { rules, reasoning } })
  const api = {
    async get(url) { calls.push(['get', url]); return url.endsWith('/output') ? output : { data: { draft, published: false } } },
    async post(url, config) { calls.push(['post', url, config]); return { data: { paths: [], task: { task_id: 'x', status: 'succeeded' } } } },
    async put(url, config) { calls.push(['put', url, config]); return { data: {} } },
  }
  const mocks = {
    react: {
      useState(x) { const i = cursor++; if (!(i in hooks)) hooks[i] = x; return [hooks[i], v => { hooks[i] = typeof v === 'function' ? v(hooks[i]) : v }] },
      useRef(x) { const i = cursor++; return hooks[i] ||= { current:x } },
      useEffect(fn,deps) { const i = cursor++, old=hooks[i]; if (!old || deps.some((v,j)=>v!==old.deps[j])) pending.push(()=>{old?.cleanup?.(); hooks[i]={deps,cleanup:fn()} }) }
    },
    'react/jsx-runtime': { jsx:(type,props)=>({type,props}), jsxs:(type,props)=>({type,props}) },
    antd: { Alert:'Alert', Button:'Button', Card:'Card', Checkbox:'Checkbox', InputNumber:'InputNumber', Space:'Space', Table:'Table', Typography:{Text:'Text'}, App:{useApp:()=>({message:{warning(){},success(){}}})} },
    '../../../api/client': { api }
  }
  const exports = {}
  const code = ts.transpileModule(fs.readFileSync(path.join(__dirname,'../src/pages/teacher/components/ProgressivePanel.tsx'),'utf8'), { compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022} }).outputText
  vm.runInNewContext(code,{exports,require:n=>mocks[n],setTimeout,clearTimeout})
  function render() { cursor=0; tree=exports.default({detail:current,token:'teacher'}); while(pending.length)pending.shift()() }
  function all(type,n=tree) { if(!n||typeof n!=='object')return []; if(Array.isArray(n))return n.flatMap(v=>all(type,v ?? null)); return [...(n.type===type?[n.props]:[]),...all(type,n.props?.children ?? null)] }
  render()
  return {all,calls,resolve:()=>resolve({data:{rules,reasoning}}), close(){hooks.forEach(h=>h?.cleanup?.())}, async flush(){for(let i=0;i<5;i++){await new Promise(setImmediate);render()}}, saveNode(){current={...current,case:{...current.case,version:current.case.version+1}};render()}, navigate(){current={...detail,case:{id:2,version:1}};render()} }
}
test('AI auto-fills rule draft and does not invent cash or publish',async()=>{
  const p=mount(); await p.flush()
  const table=p.all('Table')[0]
  assert.equal(table.columns[1].render(null,{id:'1:A'}).props.value,12)
  assert.equal(p.all('InputNumber')[0].value,null)
  assert.equal(p.calls.filter(c=>c[0]==='post').length,1)
  assert.ok(p.all('Alert').some(a=>a.message.includes('AI草稿已填入')))
  p.close()
})
test('saved teacher draft is authoritative and never calls AI',async()=>{
  const p=mount({draft:{rules,reasoning,cash_flow_amount:7}});await p.flush()
  assert.equal(p.calls.filter(c=>c[0]==='post').length,0)
  assert.equal(p.all('InputNumber')[0].value,7);p.close()
})
test('late AI answer cannot overwrite manual rule edit',async()=>{
  const p=mount({delayed:true});await p.flush()
  p.all('Table')[0].columns[1].render(null,{id:'1:A'}).props.onChange(99)
  p.resolve();await p.flush()
  assert.equal(p.all('Table')[0].columns[1].render(null,{id:'1:A'}).props.value,99)
  assert.ok(p.all('Alert').some(a=>a.message.includes('未覆盖')));p.close()
})

test('saving a node preserves edited impacts and cash, and rule save sends current values', async()=>{
  const p=mount({draft:{rules,reasoning,cash_flow_amount:7}}); await p.flush()
  const columns=p.all('Table')[0].columns
  const values=[19,2,-5,0,0.8]
  values.forEach((value,i)=>columns[i+1].render(null,{id:'1:A'}).props.onChange(value))
  p.all('InputNumber')[0].onChange(-30)
  await p.flush()
  p.all('Checkbox')[0].onChange({target:{checked:true}})
  await p.flush()
  const gets=p.calls.filter(c=>c[0]==='get').length
  p.saveNode(); await p.flush()
  values.forEach((value,i)=>assert.equal(p.all('Table')[0].columns[i+1].render(null,{id:'1:A'}).props.value,value))
  assert.equal(p.all('InputNumber')[0].value,-30)
  assert.equal(p.all('Checkbox')[0].checked,false)
  assert.equal(p.calls.filter(c=>c[0]==='get').length,gets)
  p.all('Checkbox')[0].onChange({target:{checked:true}}); await p.flush()
  p.all('Button').find(b=>b.children==='保存递进规则（待发布）').onClick(); await p.flush()
  const saved=p.calls.find(c=>c[0]==='put')[2]
  assert.equal(saved.cash_flow_amount,-30)
  assert.deepEqual(Object.values(saved.rules['1:A']),values)
  assert.equal(saved.reasoning['1:A'].teacher_edited,true)
  p.navigate(); await p.flush()
  assert.equal(p.all('InputNumber')[0].value,7)
  assert.equal(p.all('Table')[0].columns[1].render(null,{id:'1:A'}).props.value,12)
  p.close()
})

test('node save preserves AI draft and blocks late suggestions based on old node text',async()=>{
  const p=mount(); await p.flush(); p.saveNode(); await p.flush()
  assert.equal(p.all('Table')[0].columns[1].render(null,{id:'1:A'}).props.value,12)
  assert.equal(p.calls.filter(c=>c[0]==='post').length,1); p.close()
  const late=mount({delayed:true}); await late.flush()
  late.saveNode(); await late.flush(); late.resolve(); await late.flush()
  assert.equal(late.all('Table')[0].columns[1].render(null,{id:'1:A'}).props.value,0)
  late.close()
})
