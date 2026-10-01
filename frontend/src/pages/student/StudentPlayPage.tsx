import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Input, Spin, Space, Typography } from 'antd'
import { useNavigate } from 'react-router-dom'
import { decide, playGet, restoreStudent, rollbackStudentSubmission, startStudent } from '../../api/client'
import type { Metrics, PlayResponse, SessionState } from '../../api/types'
import { useStudentToken } from '../../context/StudentTokenContext'
import { useStudentAccount } from '../../context/StudentAccountContext'

export function MetricsBar({ metrics }: { metrics: (Metrics & { net_profit?: number; cash_flow_amount?: number }) | null }) {
  return metrics ? <div className="student-metrics"><span>营收 {metrics.revenue} 万元</span><span>毛利率 {metrics.gross_margin}</span><span>市场份额 {metrics.market_share}</span><span>现金流 {metrics.cash_flow}</span>{typeof metrics.net_profit === 'number' && <span>净利润 {metrics.net_profit.toFixed(2)} 万元</span>}{typeof metrics.cash_flow_amount === 'number' && <span>本期现金净流量 {metrics.cash_flow_amount.toFixed(2)} 万元</span>}</div> : null
}

export default function StudentPlayPage() {
  const token = useStudentToken(), navigate = useNavigate()
  const account = useStudentAccount()
  const [data, setData] = useState<PlayResponse | null>(null)
  const [state, setState] = useState<SessionState | null>(null)
  const [name, setName] = useState(account.real_name)
  const [reason, setReason] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [at, setAt] = useState(Date.now())
  const [loadedToken, setLoadedToken] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [showResult, setShowResult] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const page = useRef({ token, active: false, busy: false, nodeId: null as number | null, uncertain: false })
  const node = data?.nodes.find((n) => n.id === state?.next_node_id)
  const last = state?.turns.at(-1)

  useEffect(() => {
    let cancelled = false
    const current = { token, active: true, busy: false, nodeId: null as number | null, uncertain: false }
    page.current = current
    setLoading(true); setError(''); setState(null); setData(null)
    setReason(''); setBusy(false); setName(account.real_name)
    setSelected(null); setShowResult(false); setUncertain(false)
    async function load() {
      try {
        const session = Number(sessionStorage.getItem('student_session:' + token))
        if (session) {
          const saved = await restoreStudent(token, session)
          if (!cancelled) { setState(saved); setData(saved.play); setName(saved.student_name); current.nodeId = saved.next_node_id }
        } else {
          const play = await playGet(token)
          if (!cancelled) setData(play)
        }
      } catch (e) { if (!cancelled) setError(e instanceof Error ? e.message : '案例暂不可用') }
      finally { if (!cancelled) { setLoadedToken(token); setLoading(false); setAt(Date.now()) } }
    }
    void load()
    return () => { cancelled = true; current.active = false }
  }, [token])

  async function begin() {
    const current = page.current
    if (!name.trim() || !current.active || current.token !== token || current.busy) return
    current.busy = true
    setBusy(true); setError('')
    try {
      const next = await startStudent(token, name.trim())
      if (!current.active) return
      setState(next); setData(next.play); setAt(Date.now())
      current.nodeId = next.next_node_id
      sessionStorage.setItem('student_session:' + token, String(next.session_id))
      sessionStorage.setItem('student_name:' + token, next.student_name)
      window.dispatchEvent(new Event('student-session-updated'))
    } catch (e) { if (current.active) setError(e instanceof Error ? e.message : '开始失败') }
    finally { current.busy = false; if (current.active) setBusy(false) }
  }

  function acceptSaved(saved: SessionState, submittedNode: number, key: string) {
    const accepted = saved.turns.find(turn => turn.node_id === submittedNode)
    setState(saved); setData(saved.play)
    page.current.nodeId = saved.next_node_id
    page.current.uncertain = false; setUncertain(false)
    if (accepted) {
      setSelected(null); setReason(''); setShowResult(true)
      setError(accepted.chosen_option === key ? '' : '此回合已由另一页面提交，下面显示服务器实际保存的策略，请核对。')
      window.dispatchEvent(new Event('student-session-updated'))
    } else {
      setError('本回合尚未保存，请核对所选策略后重新确认提交。')
    }
  }

  async function reconcile() {
    const current = page.current
    if (!state || !node || !selected || current.busy || !current.active || current.token !== token) return
    current.busy = true; setBusy(true)
    try {
      const saved = await restoreStudent(token, state.session_id)
      if (current.active) acceptSaved(saved, node.id, selected)
    } catch { if (current.active) setError('仍无法核对服务器进度，请稍后重试；不会重复提交或自动选择其他策略。') }
    finally { current.busy = false; if (current.active) setBusy(false) }
  }

  async function submit() {
    const current = page.current
    if (!node || !state || !selected || !node.options.some(o => o.key === selected) || showResult ||
        !current.active || current.token !== token || current.busy || current.uncertain || current.nodeId !== node.id) return
    const key = selected
    current.busy = true
    setBusy(true); setError('')
    try {
      await decide(token, { student_name: state.student_name, session_id: state.session_id,
        node_id: node.id, option_key: key, input_text: reason.trim() || undefined,
        duration_ms: Math.max(0, Date.now() - at) })
      if (!current.active) return
      const saved = await restoreStudent(token, state.session_id)
      if (!current.active) return
      acceptSaved(saved, node.id, key)
    } catch {
      if (!current.active) return
      // A lost response does not prove that the POST failed. Reconcile first;
      // never blindly retry a decision which may already have been committed.
      try {
        const saved = await restoreStudent(token, state.session_id)
        if (current.active) acceptSaved(saved, node.id, key)
      } catch {
        if (current.active) {
          current.uncertain = true; setUncertain(true)
          setError('提交状态待确认，请点击“核对已保存进度”。核对前不会再次提交，以免重复作答。')
        }
      }
    }
    finally { current.busy = false; if (current.active) setBusy(false) }
  }

  if (loading || loadedToken !== token) return <Spin />
  const selectedIndex = node?.options.findIndex(o => o.key === selected) ?? -1
  const selectedOption = node?.options[selectedIndex]
  const lastNode = data?.nodes.find(n => n.id === last?.node_id)
  const lastIndex = lastNode?.options.findIndex(o => o.key === last?.chosen_option) ?? -1
  const lastLabel = last?.chosen_label || lastNode?.options[lastIndex]?.label || '历史策略内容不可用'
  const lastDisplay = last?.display_option || (lastIndex >= 0 ? String.fromCharCode(65 + lastIndex) : '')
  return <Space direction="vertical" size={16} style={{ width: '100%' }}>
    {error && <Alert type="warning" showIcon message={error} />}
    {!data && <Button block onClick={() => { sessionStorage.removeItem('student_session:' + token); window.location.reload() }}>重新载入案例</Button>}
    {data && <Card title={data.case_title} size="small">
      <Typography.Paragraph>{data.background}</Typography.Paragraph>
      <Typography.Paragraph>{data.dilemma}</Typography.Paragraph>
      <MetricsBar metrics={state?.current_metrics || data.base_metrics} />
      {state?.financial_state && <p>净利润 {state.financial_state.net_profit.toFixed(2)} 万元 · 本期现金净流量 {state.financial_state.cash_flow_amount.toFixed(2)} 万元（按前轮结果递进计算）</p>}
      {state && <Typography.Text type="secondary">教师版本 {state.case_version} · 进度已保存，刷新可继续</Typography.Text>}
    </Card>}
    {data && !state && <Card title="开始推演" size="small">
      <Typography.Paragraph>实名作答：{account.real_name}</Typography.Paragraph>
      <Button block type="primary" loading={busy} disabled={!name.trim()} onClick={() => void begin()}>开始推演</Button>
    </Card>}
    {last && <Alert type="success" message="已保存的决策结果" description={<><p>你提交的策略 {lastDisplay}：{lastLabel}</p><p>{last.summary}</p><MetricsBar metrics={last.after_metrics} /></>} />}
    {state && state.turns.map((turn, index) => <Card key={`history-${turn.node_id}`} size="small" title={`此前决策 ${index + 1}：${data?.nodes.find(n => n.id === turn.node_id)?.title || ''}`}>
      <Typography.Paragraph>{data?.nodes.find(n => n.id === turn.node_id)?.background}</Typography.Paragraph>
      <Typography.Paragraph>已选 {turn.display_option || ''}：{turn.chosen_label || turn.chosen_option}</Typography.Paragraph>
      <Typography.Paragraph>决策理由：{turn.input_text || '未填写'}</Typography.Paragraph>
      {data && <Button disabled={busy || uncertain} onClick={async () => {
        try {
          const updated = await rollbackStudentSubmission(token, state.session_id, data?.nodes.find(n => n.id === turn.node_id)?.idx || index + 1)
          setState(updated); setData(updated.play); setSelected(null); setReason(''); setShowResult(false); setError('已回退到该决策点，后续节点记录已移除，可以重新选择。')
          page.current.nodeId = updated.next_node_id; setAt(Date.now())
        } catch (e) { setError(e instanceof Error ? e.message : '回退失败') }
      }}>修改此决策及后续节点</Button>}
    </Card>)}
    {showResult && node && <Button block type="primary" onClick={() => { setShowResult(false); setSelected(null); setAt(Date.now()) }}>继续下一回合</Button>}
    {node && !showResult && <Card size="small" title={'第 ' + node.idx + ' 回合 · ' + node.node_role}>
      <Typography.Title level={4}>{node.title}</Typography.Title>
      <Typography.Paragraph>{node.background}</Typography.Paragraph>
      <Input.TextArea rows={3} maxLength={4000} disabled={busy || uncertain} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="可填写决策理由，选择策略后点击确认提交" />
      <Space direction="vertical" style={{ width: '100%', marginTop: 12 }}>
        {node.options.map((o, index) => <Button className="strategy-option" key={`${node.id}:${o.key}`} block
          type={selected === o.key ? 'primary' : 'default'} aria-pressed={selected === o.key}
          disabled={busy || uncertain} onClick={() => {
            if (!page.current.busy && !page.current.uncertain && page.current.nodeId === node.id) setSelected(o.key)
          }}>{String.fromCharCode(65 + index)}：{o.label}{selected === o.key ? '（已选择）' : ''}</Button>)}
      </Space>
      {selectedOption && <Typography.Paragraph role="status" style={{ marginTop: 12 }}>
        {busy ? '正在保存' : '已选择'} {String.fromCharCode(65 + selectedIndex)}：{selectedOption.label}
      </Typography.Paragraph>}
      {uncertain ? <Button block loading={busy} onClick={() => void reconcile()}>核对已保存进度</Button> :
        <Button block type="primary" loading={busy} disabled={!selectedOption || busy} onClick={() => void submit()}>确认提交本回合</Button>}
      <Typography.Paragraph type="secondary">先选择一项，再确认提交；提交前可以修改选择。A/B/C 仅为本次展示顺序，记录以策略内容为准。</Typography.Paragraph>
      <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>策略及结果来自教师确认版本；本阶段不调用 AI。</Typography.Paragraph>
    </Card>}
    {state?.finished && <Button type="primary" block onClick={() => navigate('/student/' + token + '/review?session=' + state.session_id)}>推演完成 · 查看 AI 复盘</Button>}
  </Space>
}
