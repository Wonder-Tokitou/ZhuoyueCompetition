const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path')
test('teacher reference is loaded, persisted and single-selected without changing rule inputs',()=>{
 const s=fs.readFileSync(path.join(__dirname,'../src/pages/teacher/components/ProgressivePanel.tsx'),'utf8')
 for(const text of ['reference_path: reference','data.draft?.reference_path','saveReference(e.target.checked ?','checked={reference?.path === row.path}','setConfirmed(false)','案例实际采用路径'])assert.ok(s.includes(text),text)
})
test('student comparison uses strategy labels and explicit simulated differences',()=>{
 const s=fs.readFileSync(path.join(__dirname,'../src/features/ai/StudentReview.tsx'),'utf8')
 for(const text of ['row.student_strategy','row.reference_strategy','row.difference[key]','data.path_comparison.note'])assert.ok(s.includes(text),text)
})
