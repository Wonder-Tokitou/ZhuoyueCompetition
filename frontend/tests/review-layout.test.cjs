const {test}=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm'),ts=require('typescript'),path=require('node:path')
const src=fs.readFileSync(path.join(__dirname,'../src/features/ai/StudentReview.tsx'),'utf8')
const out={};vm.runInNewContext(ts.transpileModule(src,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,{exports:out,require:()=>({})})
test('dense comparison is separated with all original text preserved',()=>{
 const text='第1轮学生选B，学生营收123.00，参考营收100.00，差额23.00。第2轮相同。第3轮不同。累计影响。建议：保持现金。'
 const parts=out.comparisonParagraphs(text)
 assert.equal(parts.length,5)
 assert.equal(parts.join('').replace(/\s/g,''),text.replace(/\s/g,''))
 assert.ok(parts[0].includes('\n参考营收'))
})
test('both comparison states wait for succeeded and remain after conclusion',()=>{
 assert.ok(src.includes("data.status === 'succeeded' && data.path_comparison"))
 assert.ok(!src.includes("本次推演开始时未设置参考方案"))
 assert.ok(!src.includes("标准路径结果已就绪"))
 assert.ok(src.includes("正在生成复盘，请稍候。"))
 assert.ok(src.indexOf('title="综合结论"')<src.indexOf('我的方案：</strong>'))
})
