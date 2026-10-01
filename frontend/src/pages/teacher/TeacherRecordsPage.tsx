import ReviewEditor from '../../features/ai/ReviewEditor'
import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Collapse, Select, Space, Spin, Typography } from 'antd'
import { getRecords, listCases } from '../../api/client'
import type { CaseListItem, StudentRecord } from '../../api/types'
import { useTeacherToken } from '../../context/TeacherTokenContext'
import { formatDuration, formatTime } from './teacherConstants'
import MetricComparison from '../../components/MetricComparison'

export default function TeacherRecordsPage() {
  const token = useTeacherToken()
  const [cases, setCases] = useState<CaseListItem[]>([]), [caseId, setCaseId] = useState<number | null>(null)
  const [records, setRecords] = useState<StudentRecord[]>([]), [error, setError] = useState('')
  const [loading, setLoading] = useState(false), [refresh, setRefresh] = useState(0)
  useEffect(() => { let active = true; listCases(token).then((rows) => { if (active) { setCases(rows); setCaseId((v) => v ?? rows[0]?.id ?? null) } }).catch((e) => { if (active) setError(e.message) }); return () => { active = false } }, [token])
  useEffect(() => {
    let active = true
    setRecords([]); setError('')
    if (caseId === null) return
    setLoading(true)
    getRecords(caseId, token).then((rows) => { if (active) setRecords(rows) }).catch((e) => { if (active) setError(e.message) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [caseId, token, refresh])
  const groups = useMemo(() => {
    const map = new Map<string, StudentRecord[]>()
    for (const record of records) {
      const key = record.student_id ? 'student-' + record.student_id : 'legacy-' + record.student_name
      map.set(key, [...(map.get(key) || []), record])
    }
    return [...map.entries()]
  }, [records])
  return <Space direction="vertical" style={{ width: '100%' }}>
    <Typography.Title level={3}>提交目录：案例 → 学生 → 推演记录</Typography.Title>
    <Space wrap><Select aria-label="选择案例" style={{ minWidth: 280, maxWidth: '100%' }} value={caseId} options={cases.map((r) => ({ value: r.id, label: r.title }))} onChange={setCaseId} /><Button onClick={() => setRefresh((v) => v + 1)} loading={loading}>刷新记录</Button></Space>
    {error && <Alert type="error" message={error} />}
    {loading ? <Spin /> : <Card title={cases.find((c) => c.id === caseId)?.title || '请选择案例'}>
      {!records.length && <Typography.Text>暂无已提交记录</Typography.Text>}
      <Collapse items={groups.map(([key, submissions]) => ({ key, label: `${submissions[0].student_name} · ${submissions[0].student_id ? '账号 #' + submissions[0].student_id : '历史匿名记录'} · ${submissions.length} 次推演`, children:
        <Collapse items={submissions.map((r) => ({ key: String(r.session_id) + '-' + r.attempt_no, label: `第 ${r.attempt_no} 次推演 · 记录 #${r.session_id} · ${r.turns.length}/3 节点`, children: <>
          {r.turns.map((t) => <Card key={t.id} size="small" title={`节点 ${t.idx} · 学生展示选项 ${t.display_option || '未记录'} · 教师内部编号 ${t.chosen_option}`} style={{ marginBottom: 12 }}><p>已选策略：{t.chosen_label || '历史记录未提供策略文字'}</p><p>理由：{t.input_text || '未填写'}</p><p>{t.result_json.summary}</p><MetricComparison before={t.before_metrics} after={t.after_metrics ?? t.result_json.metrics} /><p>{formatTime(t.created_at)} · {formatDuration(t.duration_ms)}</p></Card>)}
          <Typography.Title level={5}>AI 复盘 · {r.review_status === 'succeeded' ? '已生成' : r.review_status === 'failed' ? '生成失败' : r.review_status === 'not_started' ? '尚未完成' : '生成中'}</Typography.Title>
          {r.review && <ReviewEditor key={String(r.review_id) + '-' + refresh} record={r} token={token} onSaved={() => setRefresh((v) => v + 1)} />}
        </> }))} />
      }))} />
    </Card>}
  </Space>
}
