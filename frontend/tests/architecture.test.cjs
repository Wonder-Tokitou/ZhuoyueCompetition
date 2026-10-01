const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs'), path = require('node:path')

test('AI feature module has no provider secrets or business page dependency', () => {
  const root = path.resolve(__dirname, '../src/features/ai')
  for (const filename of fs.readdirSync(root).filter((name) => /\.tsx?$/.test(name))) {
    const text = fs.readFileSync(path.join(root, filename), 'utf8')
    assert.ok(!/LLM_API_KEY|DEEPSEEK_API_KEY|api\.deepseek\.com/.test(text), filename)
    assert.ok(!/from ['"][^'"]*pages\//.test(text), filename)
  }
  const business = fs.readFileSync(path.resolve(__dirname, '../src/api/client.ts'), 'utf8')
  assert.ok(!/export async function (chatStudent|review|aiFix|getAiTask)\(/.test(business))
  const page = fs.readFileSync(path.resolve(__dirname, '../src/pages/student/StudentReviewPage.tsx'), 'utf8')
  assert.ok(page.includes('features/ai/StudentReview'))
  assert.ok(!page.includes('setTimeout'))
})
