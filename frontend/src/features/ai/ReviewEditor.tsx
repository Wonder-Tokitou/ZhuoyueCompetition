import { useState } from 'react'
import { Alert, Button, Input, Space, Typography } from 'antd'
import { patchReview } from './api'
import type { StudentRecord } from '../../api/types'

export default function ReviewEditor({ record, token, onSaved }: { record: StudentRecord; token: string; onSaved: () => void }) {
  const [dimensions, setDimensions] = useState(record.review!.dimensions), [conclusion, setConclusion] = useState(record.review!.conclusion)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  return <Space direction="vertical" style={{ width: '100%' }}>
    <Typography.Text>{record.review!.framework_type} · 教师校准优先于 AI 结果</Typography.Text>
    {dimensions.map((d, i) => <div key={d.name}><b>{d.name}</b><Input.TextArea aria-label={d.name} autoSize value={d.content} onChange={(e) => setDimensions((rows) => rows.map((r, j) => i === j ? { ...r, content: e.target.value } : r))} /></div>)}
    <Input.TextArea aria-label="综合结论" autoSize value={conclusion} onChange={(e) => setConclusion(e.target.value)} />
    {error && <Alert type="error" message={error} />}
    <Button disabled={!record.review_id} loading={busy} onClick={async () => { setBusy(true); setError(''); try { await patchReview(record.review_id!, token, { dimensions, conclusion }); onSaved() } catch (e) { setError(e instanceof Error ? e.message : '保存失败') } finally { setBusy(false) } }}>保存教师校准</Button>
  </Space>
}
