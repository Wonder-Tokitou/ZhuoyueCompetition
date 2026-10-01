import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Alert, App, Button, Card, Checkbox, InputNumber, Select, Space, Table, Typography } from 'antd'
import { api } from '../../../api/client'
import type { GetCaseResponse } from '../../../api/types'

type Impact = { revenue_pct: number; margin_pp: number; expense_pct: number; cash_pct: number; share_pp: number }
type Basis = { basis: string; is_assumption: boolean; caveat: string; teacher_edited?: boolean }
type ReferencePath = { path: string; kind: 'recommended' | 'historical' }
type Configuration = { enabled: boolean; cash_flow_amount: number; rules: Record<string, Impact>; reasoning?: Record<string, Basis>; reference_path?: ReferencePath | null }
type Task = { task_id: string; status: string; error?: string }
type Path = { path: string; final: Record<string, number | string>; steps: Record<string, number | string>[] }
const fields: [keyof Impact, string][] = [['revenue_pct', '营收变化 %'], ['margin_pp', '毛利率变化 百分点'], ['expense_pct', '运营费用变化 %'], ['cash_pct', '现金流变化 %'], ['share_pp', '市场份额变化 百分点']]

export default function ProgressivePanel({ detail, token, children, onSummariesSaved }: { detail: GetCaseResponse; token: string; children?: (renderImpact: (id: string) => ReactNode) => ReactNode; onSummariesSaved?: (summaries: Record<string, string>) => void }) {
  const { message } = App.useApp()
  const [rules, setRules] = useState<Record<string, Impact>>({})
  const [reference, setReference] = useState<ReferencePath | null>(null)
  const [referenceKind, setReferenceKind] = useState<'recommended' | 'historical'>('recommended')
  const [cash, setCash] = useState<number | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [published, setPublished] = useState(false)
  const [paths, setPaths] = useState<Path[]>([])
  const [reasoning, setReasoning] = useState<Record<string, Basis>>({})
  const [aiStatus, setAiStatus] = useState('')
  const [retryNo, setRetryNo] = useState(0)
  const [generating, setGenerating] = useState(false)
  const touched = useRef(false)
  const url = `/cases/${detail.case.id}/simulation`
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    touched.current = false
    setLoaded(false); setPaths([]); setConfirmed(false)
    setAiStatus(''); setGenerating(false); setReasoning({})
    async function poll(task: Task) {
      if (!active) return
      if (task.status === 'succeeded') {
        const { data } = await api.get<{ rules: Record<string, Impact>; reasoning: Record<string, Basis> }>(`/ai-tasks/${task.task_id}/output`, { params: { token } })
        const saved = await api.get<{ draft: Configuration | null }>(url, { params: { token } })
        if (!active) return
        setGenerating(false)
        if (saved.data.draft) { setAiStatus('教师规则已保存，AI未覆盖。请刷新载入已保存版本。'); return }
        if (touched.current) { setAiStatus('你已修改表格，AI建议未覆盖当前输入。'); return }
        setRules(data.rules); setReasoning(data.reasoning); setConfirmed(false)
        setAiStatus('AI草稿已填入。请核对教学假设、填写现金金额，再预览确认；尚未保存或发布。')
        return
      }
      if (['failed', 'cancelled'].includes(task.status)) {
        setGenerating(false); setAiStatus(`AI草稿生成失败：${task.error || '请重试'}`); return
      }
      setGenerating(true); setAiStatus('AI正在生成9条影响规则并校验路径；刷新页面可继续查看进度。')
      timer = setTimeout(async () => {
        try {
          const { data } = await api.get<Task>(`/ai-tasks/${task.task_id}`, { params: { token } })
          await poll(data)
        } catch {
          if (active) { setGenerating(false); setAiStatus('进度读取失败，请点击恢复查看；后台任务不会因此重复创建。') }
        }
      }, 2500)
    }
    api.get<{ draft: Configuration | null; published: boolean }>(url, { params: { token } }).then(({ data }) => {
      if (!active) return
      setRules(data.draft?.rules || {}); setCash(data.draft?.cash_flow_amount ?? null)
      setReference(data.draft?.reference_path ?? null)
      setReferenceKind(data.draft?.reference_path?.kind ?? 'recommended')
      setReasoning(data.draft?.reasoning || {})
      setPublished(data.published); setLoaded(true)
      if (data.draft) { setAiStatus('已载入教师保存的规则，AI不会自动覆盖。'); return }
      setGenerating(true)
      api.post<{ protected: boolean; task: Task | null }>(`${url}/suggest`, null, { params: { token, retry: retryNo > 0 } }).then(async ({ data: result }) => {
        if (!active) return
        if (result.protected || !result.task) { setGenerating(false); setAiStatus('教师规则已在其他页面保存，请刷新载入。'); return }
        await poll(result.task)
      }).catch(() => { if (active) { setGenerating(false); setAiStatus('AI草稿任务暂时无法读取，可重试或手动填写。') } })
    }).catch(() => { if (active) setLoaded(false) })
    return () => { active = false; if (timer) clearTimeout(timer) }
  }, [url, token, retryNo])
  // Saving node text/baseline increments the case version, but must not reload
  // persisted rules over the teacher's current unsaved input.
  const previousVersion = useRef({ url, token, version: detail.case.version })
  useEffect(() => {
    const previous = previousVersion.current
    previousVersion.current = { url, token, version: detail.case.version }
    if (previous.url === url && previous.token === token && previous.version !== detail.case.version) {
      touched.current = true // A late AI result may have used the old node text.
      setPaths([])
      setConfirmed(false)
    }
  }, [url, token, detail.case.version])
  const rows = detail.nodes.flatMap(n => n.options.map(o => ({ id: `${n.id}:${o.key}`, title: `第${n.idx}轮 ${o.key}：${o.label}` })))
  function change(id: string, key: keyof Impact, value: number | null) {
    touched.current = true
    setRules(prev => ({ ...prev, [id]: { ...(prev[id] || { revenue_pct: 0, margin_pp: 0, expense_pct: 0, cash_pct: 0, share_pp: 0 }), [key]: value ?? 0 } }))
    setPaths([]); setConfirmed(false)
    setReasoning(prev => prev[id] ? { ...prev, [id]: { ...prev[id], teacher_edited: true } } : prev)
  }
  async function submit(save: boolean) {
    if (cash === null) { message.warning('请填写有依据的本期现金净流量金额，不从描述自动推断'); return }
    if (save && !confirmed) { message.warning('请先确认9个选项规则'); return }
    if (generating) { message.warning('请等待AI草稿完成，避免把占位0保存为规则'); return }
    const config: Configuration = { enabled: true, cash_flow_amount: cash, reasoning, reference_path: reference, rules: Object.fromEntries(rows.map(r => [r.id, rules[r.id] || { revenue_pct: 0, margin_pp: 0, expense_pct: 0, cash_pct: 0, share_pp: 0 }])) }
    setBusy(true)
    try {
      const { data } = await api.post<{ paths: Path[] }>(`${url}/preview`, config, { params: { token } })
      setPaths(data.paths)
      if (save) {
        const saved = await api.put<{ summaries?: Record<string, string> }>(url, config, { params: { token } })
        onSummariesSaved?.(saved.data.summaries || {})
        message.success('规则已保存。请点击下方“发布案例”，发布后新推演才使用新规则。')
      }
    } finally { setBusy(false) }
  }
  function renderImpact(id: string) {
    return <Space direction="vertical" style={{ width: '100%' }}>
      {fields.map(([key, label]) => <InputNumber key={key} aria-label={`${id} ${label}`} addonBefore={label} style={{ width: '100%' }} value={rules[id]?.[key] ?? 0} precision={key.endsWith('_pp') ? 1 : 2} min={-100} max={key.endsWith('_pp') ? 100 : 1000} disabled={!loaded || busy} onChange={value => change(id, key, value)} />)}
      <details><summary>影响规则依据</summary>{reasoning[id] ? <><p>{reasoning[id].teacher_edited ? '教师已修改；以下为原AI依据。' : ''}{reasoning[id].is_assumption ? '教学假设：' : '材料/规范依据：'}{reasoning[id].basis}</p><p>限制：{reasoning[id].caveat}</p></> : '暂无AI依据，请教师核对。'}</details>
    </Space>
  }
  async function saveReference(next: ReferencePath | null) {
    if (busy || !loaded) return
    setBusy(true)
    try {
      await api.put(`${url}/reference`, { reference_path: next, rules, cash_flow_amount: cash }, { params: { token } })
      setReference(next)
      if (next) setReferenceKind(next.kind)
      message.success(next ? '标准路径及推演结果已自动保存；请重新发布案例，新推演即可使用。' : '标准路径已取消，请重新发布生效。')
    } catch { /* transport displays the error; keep last saved selection */ }
    finally { setBusy(false) }
  }
  return <Card title="决策校准：选项与递进影响规则" size="small">
    <Space direction="vertical" style={{ width: '100%' }}>
      <Alert type="info" showIcon message={published ? '已发布递进模式；修改保存后需重新发布。' : '当前尚未发布递进模式；旧推演保持原结果。'} description="先保存上方基准数据。每轮以此前结果为基数：营收、费用、现金流按变化比例计算；毛利率及市场份额按百分点调整；成本=营收×(1−毛利率)，净利润=营收−成本−费用。请先保存节点文案，再调整并保存递进规则，最后发布案例。现金流说明仅作文字解释，不代替现金金额与变化比例。" />
      <Typography.Text>本期现金净流量（万元，与营收使用同一期间及企业范围）</Typography.Text>
      {aiStatus && <Alert type={aiStatus.includes('失败') ? 'warning' : 'info'} message={aiStatus} />}
      {!generating && !touched.current && (aiStatus.includes('失败') || aiStatus.includes('重试') || aiStatus.includes('恢复')) && <Button onClick={() => setRetryNo(n => n + 1)}>重试 / 恢复AI草稿</Button>}
      <InputNumber value={cash} precision={2} onChange={v => { setCash(v); setConfirmed(false); setPaths([]) }} disabled={!loaded || busy} />
      <Typography.Text type="secondary">现金流允许负值；例如 −10 × (1+20%) = −12，代表净流出扩大；0按比例变化仍为0。AI建议不是经营预测，缺乏依据的数值标为教学假设。尚未生成时的0仅为占位。净利润不设置独立比例。</Typography.Text>
      {children ? children(renderImpact) : <Table rowKey="id" dataSource={rows} pagination={false} expandable={{ expandedRowRender: row => reasoning[row.id] ? <Space direction="vertical"><Typography.Text>{reasoning[row.id].is_assumption ? '教学假设' : '材料/规范依据'}{reasoning[row.id].teacher_edited ? '（教师已修改系数，以下为原AI依据）' : ''}</Typography.Text><Typography.Text>{reasoning[row.id].basis}</Typography.Text><Typography.Text type="secondary">限制：{reasoning[row.id].caveat}</Typography.Text></Space> : <Typography.Text>暂无AI依据，请教师自行核对。</Typography.Text> }} scroll={{ x: 1100 }} columns={[
        { title: '决策选项', dataIndex: 'title', width: 330 },
        ...fields.map(([key, title]) => ({ title, key, render: (_: unknown, row: typeof rows[number]) => <InputNumber aria-label={`${row.title} ${title}`} value={rules[row.id]?.[key] ?? 0} precision={key.endsWith('_pp') ? 1 : 2} min={-100} max={key.endsWith('_pp') ? 100 : 1000} disabled={!loaded || busy} onChange={v => change(row.id, key, v)} /> }))
      ]} />}
      <Checkbox checked={confirmed} disabled={!loaded || busy || generating} onChange={e => setConfirmed(e.target.checked)}>已核对基准及9个选项影响规则（含无影响的0值）</Checkbox>
      <Space wrap>
        <Typography.Text>参考路径类型</Typography.Text>
        <Select aria-label="参考路径类型" disabled={!loaded || busy || generating} value={referenceKind} options={[{value:'recommended',label:'教师推荐方案'},{value:'historical',label:'案例实际采用路径'}]} onChange={kind => { if (reference) void saveReference({...reference,kind}); else setReferenceKind(kind) }} />
        <Typography.Text>{reference ? `已选：${reference.path}` : '未设置参考路径（可选）'}</Typography.Text>
        <Button disabled={!reference || busy} onClick={() => void saveReference(null)}>取消参考路径</Button>
      </Space>
      <Typography.Text type="secondary">先保存递进规则，再选择标准路径。选中后自动保存路径及推演结果，提示成功后重新发布，新推演完成三轮即可查看。参考方案不是唯一正确答案，金额为教学模拟。</Typography.Text>
      <Space><Button disabled={!loaded || generating || rows.length !== 9} loading={busy} onClick={() => void submit(false)}>预览27条路径</Button><Button type="primary" disabled={!loaded || generating || rows.length !== 9 || !confirmed} loading={busy} onClick={() => void submit(true)}>保存递进规则（待发布）</Button></Space>
      {paths.length > 0 && <Table size="small" rowKey="path" dataSource={paths} pagination={{ pageSize: 9 }} expandable={{ expandedRowRender: row => <div>{row.steps.map((s, i) => <p key={i}>第{i + 1}轮：营收 {s.revenue}，毛利率 {s.gross_margin}，成本 {s.operating_cost}，费用 {s.operating_expense}，净利润 {Number(s.net_profit).toFixed(2)}，现金净流量 {Number(s.cash_flow_amount).toFixed(2)}（金额单位：万元）</p>)}</div> }} columns={[
        { title: '设为参考', key: 'reference', render: (_: unknown, row: Path) => <Checkbox aria-label={`选择参考路径 ${row.path}`} checked={reference?.path === row.path} disabled={busy || !loaded} onChange={e => void saveReference(e.target.checked ? {path:row.path,kind:referenceKind} : null)} /> },
        { title: '路径（教师选项编号）', dataIndex: 'path', render: (value: string) => <div>{value}<div>{value.split('→').map((key,i) => { const node=[...detail.nodes].sort((a,b)=>a.idx-b.idx)[i]; return <p key={i}>第{i+1}轮：{node?.options.find(o=>o.key===key)?.label}</p> })}</div></div> },
        ...[['revenue', '末轮营收'], ['gross_margin', '末轮毛利率'], ['net_profit', '末轮净利润'], ['cash_flow_amount', '末轮现金净流量']].map(([key, title]) => ({ title, key, render: (_: unknown, row: Path) => typeof row.final[key] === 'number' ? Number(row.final[key]).toFixed(2) : row.final[key] }))
      ]} />}
    </Space>
  </Card>
}
