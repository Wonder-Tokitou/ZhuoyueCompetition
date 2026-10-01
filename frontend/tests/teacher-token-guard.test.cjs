const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), ts = require('typescript')

function render(routeToken, storedToken, blocked = false) {
  const source = fs.readFileSync(path.join(__dirname, '../src/context/TeacherTokenContext.tsx'), 'utf8')
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const exports = {}
  const Navigate = 'Navigate'
  const storage = { getItem: () => { if (blocked) throw new Error('blocked'); return storedToken } }
  vm.runInNewContext(code, {
    exports,
    window: { sessionStorage: storage }, sessionStorage: storage,
    require: n => n === 'react/jsx-runtime' ? { jsx: (type, props) => ({ type, props }) }
      : n === 'react' ? { createContext: () => ({ Provider: 'Provider' }) }
      : { Navigate, useParams: () => ({ teacherToken: routeToken }) },
  })
  return exports.TeacherTokenProvider({ children: 'protected-page' })
}

test('missing session after reopening redirects before protected page renders', () => {
  const result = render(undefined, null)
  assert.equal(result.type, 'Navigate')
  assert.equal(result.props.to, '/teacher/login')
  assert.equal(result.props.replace, true)
  assert.equal(result.props.children, undefined)
})
test('storage denial does not crash teacher page', () => {
  assert.equal(render(undefined, null, true).type, 'Navigate')
})
test('existing session and explicit teacher links remain supported', () => {
  assert.equal(render(undefined, 'session').props.value, 'session')
  assert.equal(render('route', 'session').props.value, 'route')
  assert.equal(render('route', null, true).props.value, 'route')
})
