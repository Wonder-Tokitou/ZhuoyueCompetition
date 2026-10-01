import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Input, Select, Space, Typography } from 'antd'
import { useLocation } from 'react-router-dom'
import { api } from '../../api/transport'
import { chatStudent } from './api'

interface SavedSession { session_id: number; student_token: string; case_title: string; attempt_no: number; available: boolean }
interface Message { role: string; content: string }

export function TutorMessage({ content, assistant = true }: { content: string; assistant?: boolean }) {
  // Hide presentation-only citation tokens and the generated evidence dump.
  // Apply to both streamed and saved replies; never alter the student's text.
  if (assistant) {
    content = content.replace(/\r\n/g, '\n')
    const appendix = /(?:^|\n)[ \t]*(?:依据|资料)[：:][ \t]*(?=\n|$)/m.exec(content)
    if (appendix) content = content.slice(0, appendix.index)
    content = content
      .replace(/\[((?:read|web)(?:\\?_)[A-Za-z0-9_\\]+)\]/g, '')
      .replace(/【(?:案例资料|当前节点|已提交结果|教学规则|资料依据|外部资料(?:\s+[^】\n]+)?)】/g, '')
      .replace(/[ \t]+$/gm, '')
      .trim()
  }
  return <div style={{ overflowWrap: 'anywhere' }}>
    <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{content}</Typography.Paragraph>
  </div>
}

export default function StudentTutorSidebar() {
  const location = useLocation()
  const [rows, setRows] = useState<SavedSession[]>([]), [selected, setSelected] = useState<number | null>(null)
  const [refresh, setRefresh] = useState(0), [question, setQuestion] = useState(''), [answer, setAnswer] = useState('')
  const [messages, setMessages] = useState<Message[]>([]), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const scope = location.pathname + location.search
  const [loadedScope, setLoadedScope] = useState('')
  const [messageScope, setMessageScope] = useState('')
  const request = useRef<AbortController | null>(null)
  const ready = loadedScope === scope
  const row = ready ? rows.find((r) => r.session_id === selected) : undefined
  const conversation = scope + ':' + (row?.session_id || '')
  const visibleMessages = messageScope === conversation
  useEffect(() => { const update = () => setRefresh((v) => v + 1); window.addEventListener('student-session-updated', update); return () => window.removeEventListener('student-session-updated', update) }, [])
  useEffect(() => {
    let active = true
    api.get<SavedSession[]>('/student/records').then(({ data }) => {
      if (!active) return
      const token = location.pathname.split('/')[2]
      const isCase = !!token && !['cases', 'records', 'profile', 'login'].includes(token)
      const list = data.filter((r) => r.available && (!isCase || r.student_token === token))
      setRows(list)
      const current = Number(new URLSearchParams(location.search).get('session') || sessionStorage.getItem('student_session:' + token))
      setSelected((old) => list.some((r) => r.session_id === current) ? current : list.some((r) => r.session_id === old) ? old : list[0]?.session_id || null)
      setLoadedScope(scope)
    }).catch((e) => { if (active) setError(e.message) })
    return () => { active = false }
  }, [scope, refresh])
  useEffect(() => {
    let active = true
    request.current?.abort(); setBusy(false); setAnswer(''); setQuestion(''); setError(''); setMessages([])
    setMessageScope(conversation)
    if (row) api.get<Message[]>(`/play/${row.student_token}/sessions/${row.session_id}/messages`).then(({ data }) => { if (active) setMessages(data) }).catch((e) => { if (active) setError(e.message) })
    return () => { active = false; request.current?.abort() }
  }, [conversation])
  async function ask() {
    if (!row || !question.trim() || busy) return
    const controller = new AbortController(); request.current = controller
    const asked = question.trim()
    setBusy(true); setError(''); setAnswer('')
    let response = ''
    try {
      await chatStudent(row.student_token, row.session_id, asked, (text) => { response += text; if (!controller.signal.aborted) setAnswer(response) }, controller.signal)
      if (!controller.signal.aborted) { setMessages((old) => [...old, { role: 'user', content: asked }, { role: 'assistant', content: response }]); setQuestion(''); setAnswer('') }
    } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : '答疑暂不可用') }
    finally { if (!controller.signal.aborted) setBusy(false) }
  }
  return <aside className="student-tutor"><Card title="AI 商科助教">
    <Space direction="vertical" style={{ width: '100%' }}>
      <Typography.Paragraph type="secondary">基于你选择的案例与本人作答进行答疑；必要时查询公开商科资料，不改变推演结果。</Typography.Paragraph>
      <Select aria-label="答疑依据" style={{ width: '100%' }} loading={!ready} disabled={!ready} value={ready ? selected : null} onChange={setSelected} options={(ready ? rows : []).map((r) => ({ value: r.session_id, label: `${r.case_title} · 第 ${r.attempt_no} 次` }))} placeholder="请先开始一次案例推演" />
      <div className="tutor-messages" aria-live="polite">{visibleMessages && messages.map((m, i) => <div key={i}><b>{m.role === 'user' ? '我' : '助教'}</b><TutorMessage content={m.content} assistant={m.role === 'assistant'} /></div>)}{visibleMessages && answer && <TutorMessage content={answer} />}</div>
      {error && <Alert type="warning" message={error} />}
      <Input.TextArea aria-label="商科问题" rows={3} maxLength={4000} value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="例如：我的选择如何影响现金流？" />
      <Button type="primary" block loading={busy} disabled={!row || !question.trim()} onClick={() => void ask()}>提问</Button>
    </Space>
  </Card></aside>
}
