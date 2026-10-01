import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import ProgressivePanel from './ProgressivePanel'
import { baselineError, linkedBaseline } from './baselineFinance'
import { metricAnnotation } from './metricAnnotation'
import {
  App as AntdApp,
  Alert,
  Button,
  Card,
  Collapse,
  Input,
  InputNumber,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { TableColumnsType } from 'antd'
import { generateCaseNodes, patchCase, patchNode } from '../../../api/client'
import { getAiTask, listCaseAiTasks } from '../../../features/ai/api'
import TaskStatus, { pendingTask } from '../../../features/ai/TaskStatus'
import { stableIdempotencyKey } from '../../../domain/idempotency.js'
import { calculateNetProfit } from '../../../domain/financials.js'
import type { AiTaskResponse } from '../../../api/types'
import type {
  GetCaseResponse,
  Metrics,
  OptionDetail,
  RiskLevel,
  TeacherNode,
} from '../../../api/types'
import { METRIC_FIELDS, RISK_META, RISK_OPTIONS, STATUS_META, CASH_FLOW_TEXTAREA_STYLE } from '../teacherConstants'

const { TextArea } = Input

/** 指标编辑态：营收允许为空（未填时用 null），其余三项是字符串 */
type MetricsDraft = {
  revenue: number | null
  gross_margin: string
  market_share: string
  cash_flow: string
}

type OptionDraft = Omit<OptionDetail, 'metrics' | 'financial_assumptions'> & {
  metrics: MetricsDraft
  financial_assumptions: { operating_cost: number | null; operating_expense: number | null }
}
type NodeDraft = Omit<TeacherNode, 'options'> & { options: OptionDraft[] }

const EMPTY_METRICS: MetricsDraft = {
  revenue: null,
  gross_margin: '',
  market_share: '',
  cash_flow: '',
}

function toDraftMetrics(metrics: Metrics | null | undefined): MetricsDraft {
  if (!metrics) return { ...EMPTY_METRICS }
  return {
    revenue: typeof metrics.revenue === 'number' ? metrics.revenue : null,
    gross_margin: metrics.gross_margin ?? '',
    market_share: metrics.market_share ?? '',
    cash_flow: metrics.cash_flow ?? '',
  }
}

function toDraftNodes(nodes: TeacherNode[]): NodeDraft[] {
  return nodes.map((node) => ({
    ...node,
            options: node.options.map((option) => ({
              ...option,
              metrics: toDraftMetrics(option.metrics),
              financial_assumptions: {
                operating_cost: option.financial_assumptions?.operating_cost ?? null,
                operating_expense: option.financial_assumptions?.operating_expense ?? null,
              },
    })),
  }))
}

interface CalibratePanelProps {
  teacherToken: string
  /** 未载入案例时为 null，此时只渲染空态 */
  detail: GetCaseResponse | null
  onRefresh: () => Promise<void>
  onSaveRefresh?: () => Promise<void>
  draftRevision?: number
}

/**
 * 校准区：案例级信息（T5）+ 三个节点（T6）。
 * 所有输入都是受控组件，草稿存在本地 state，点保存才写库。
 */
export default function CalibratePanel({ teacherToken, detail, onRefresh, onSaveRefresh, draftRevision }: CalibratePanelProps) {
  const { message } = AntdApp.useApp()
  const [title, setTitle] = useState(detail?.case.title ?? '')
  const [background, setBackground] = useState(detail?.case.background ?? '')
  const [dilemma, setDilemma] = useState(detail?.case.dilemma ?? '')
  const [metrics, setMetrics] = useState<MetricsDraft>(toDraftMetrics(detail?.case.base_metrics))
  const [nodes, setNodes] = useState<NodeDraft[]>(detail ? toDraftNodes(detail.nodes) : [])
  const [savingCase, setSavingCase] = useState(false)
  const [revenueLink, setRevenueLink] = useState<'margin' | 'cost'>('margin')
  const [savingNodeId, setSavingNodeId] = useState<number | null>(null)
  const [netProfit, setNetProfit] = useState<number | null>(detail?.case.base_net_profit ?? null)
  const [operatingCost, setOperatingCost] = useState<number | null>(detail?.case.financial_assumptions?.operating_cost ?? null)
  const [operatingExpense, setOperatingExpense] = useState<number | null>(detail?.case.financial_assumptions?.operating_expense ?? null)
  const [generatingNodes, setGeneratingNodes] = useState(false)
  const [nodeTask, setNodeTask] = useState<AiTaskResponse | null>(null)
  const [nodeError, setNodeError] = useState('')
  const [nodeProgressError, setNodeProgressError] = useState('')
  const [nodeGenerationAttempt, setNodeGenerationAttempt] = useState(0)
  const refreshRef = useRef(onRefresh)
  useEffect(() => { refreshRef.current = onRefresh }, [onRefresh])

  // Only explicit reload/generation/navigation resets drafts, never save sync.
  useEffect(() => {
    setTitle(detail?.case.title ?? '')
    setBackground(detail?.case.background ?? '')
    setDilemma(detail?.case.dilemma ?? '')
    setMetrics(toDraftMetrics(detail?.case.base_metrics))
    setNodes(detail ? toDraftNodes(detail.nodes) : [])
    if (detail?.nodes.length) { setNodeTask(null); setGeneratingNodes(false) }
    setNetProfit(detail?.case.base_net_profit ?? null)
    setOperatingCost(detail?.case.financial_assumptions?.operating_cost ?? null)
    setOperatingExpense(detail?.case.financial_assumptions?.operating_expense ?? null)
  }, [detail?.case.id, draftRevision === undefined ? detail : draftRevision])

  // A page refresh or navigation back to this case must rediscover its persisted
  // node-generation task instead of showing a fresh button over a running job.
  useEffect(() => {
    if (!detail) return
    let active = true
    void listCaseAiTasks(detail.case.id, teacherToken).then((tasks) => {
      if (!active || nodeTask) return
      const latest = tasks.find((task) => task.kind === 'case_nodes')
      if (latest) {
        setNodeTask(latest)
        setGeneratingNodes(pendingTask(latest))
      }
    }).catch(() => {
      if (active) setNodeProgressError('无法恢复决策节点任务状态；可刷新页面重试，后台任务不会因此取消。')
    })
    return () => { active = false }
  }, [detail?.case.id, teacherToken])

  // Poll the database-backed task. Errors reconnect without resubmitting it;
  // terminal task errors are surfaced, and successful tasks refresh case data.
  useEffect(() => {
    if (!nodeTask || !pendingTask(nodeTask)) return
    let active = true
    let timer: number | undefined
    const poll = async () => {
      try {
        const latest = await getAiTask(nodeTask.task_id, teacherToken)
        if (!active) return
        setNodeTask(latest)
        setNodeProgressError('')
        if (pendingTask(latest)) {
          timer = window.setTimeout(() => void poll(), 2000)
        } else {
          setGeneratingNodes(false)
          if (latest.status === 'failed') {
            const failure = latest.error || '决策节点生成失败，请查看任务详情后重试。'
            setNodeError(failure)
            message.error(failure)
            setNodeGenerationAttempt((attempt) => attempt + 1)
          } else if (latest.status === 'succeeded') {
            setNodeError('')
            message.success('决策节点生成完成，已加载最新结果。')
            await refreshRef.current()
          }
        }
      } catch {
        if (active) {
          setNodeProgressError('任务进度暂不可用，正在重连；不会重新提交或取消后台任务。')
          timer = window.setTimeout(() => void poll(), 5000)
        }
      }
    }
    timer = window.setTimeout(() => void poll(), 1000)
    return () => { active = false; if (timer !== undefined) window.clearTimeout(timer) }
  }, [nodeTask?.task_id, nodeTask?.status, teacherToken])

  function updateNode(nodeId: number, patch: Partial<NodeDraft>) {
    setNodes((prev) => prev.map((node) => (node.id === nodeId ? { ...node, ...patch } : node)))
  }

  function updateOption(nodeId: number, optionKey: string, patch: Partial<OptionDraft>) {
    setNodes((prev) =>
      prev.map((node) =>
        node.id === nodeId
          ? {
              ...node,
              options: node.options.map((option) =>
                option.key === optionKey ? { ...option, ...patch } : option,
              ),
            }
          : node,
      ),
    )
  }

  function updateOptionMetric(
    nodeId: number,
    optionKey: string,
    field: keyof MetricsDraft,
    value: string | number | null,
  ) {
    setNodes((prev) =>
      prev.map((node) =>
        node.id === nodeId
          ? {
              ...node,
              options: node.options.map((option) =>
                option.key === optionKey
                  ? // 计算键写入，字段名与类型由 MetricsDraft 保证
                    ({ ...option, metrics: { ...option.metrics, [field]: value } as MetricsDraft })
                  : option,
              ),
            }
          : node,
      ),
    )
  }

  function validateMetrics(draft: MetricsDraft, prefix: string): Metrics | null {
    if (typeof draft.revenue !== 'number' || Number.isNaN(draft.revenue)) {
      message.warning(`${prefix}：营收要填数字`)
      return null
    }
    const missing = METRIC_FIELDS.filter(
      (field) => !field.numeric && !String(draft[field.key] ?? '').trim(),
    )
    if (missing.length > 0) {
      message.warning(`${prefix}：${missing.map((field) => field.label).join('、')}不能为空`)
      return null
    }
    return {
      revenue: draft.revenue,
      gross_margin: draft.gross_margin.trim(),
      market_share: draft.market_share.trim(),
      cash_flow: draft.cash_flow.trim(),
    }
  }

  function validateOptions(node: NodeDraft): OptionDetail[] | null {
    const prefix = `第 ${node.idx} 个节点`
    if (node.options.length !== 3 || new Set(node.options.map((o) => o.risk_level)).size !== 3) {
      message.warning(`${prefix}：三个选项的风险等级必须是保守 / 稳健 / 激进各一个`)
      return null
    }
    const options: OptionDetail[] = []
    for (const option of node.options) {
      if (!option.label.trim() || !option.summary.trim()) {
        message.warning(`${prefix}：选项 ${option.key} 的策略内容与结果摘要都要填写`)
        return null
      }
      const optionMetrics = validateMetrics(option.metrics, `${prefix} 选项 ${option.key}`)
      if (!optionMetrics) return null
      const assumptions = option.financial_assumptions
      if (assumptions.operating_cost === null || assumptions.operating_expense === null) {
        message.warning(`${prefix} 选项 ${option.key}：请填写营业成本和运营费用，系统才能可靠计算净利润`)
        return null
      }
      const completeAssumptions = {
        operating_cost: assumptions.operating_cost,
        operating_expense: assumptions.operating_expense,
      }
      options.push({
        key: option.key,
        label: option.label.trim(),
        risk_level: option.risk_level,
        metrics: optionMetrics,
        financial_assumptions: completeAssumptions,
        financial_basis: option.financial_basis,
        net_profit: Number(option.metrics.revenue || 0) - completeAssumptions.operating_cost - completeAssumptions.operating_expense,
        summary: option.summary.trim(),
      })
    }
    return options
  }

  async function handleSaveCase() {
    if (!detail) {
      message.warning('请先选择或生成一个案例')
      return
    }
    if (!title.trim()) {
      message.warning('案例标题不能为空')
      return
    }
    const caseMetrics = validateMetrics(metrics, '案例基准数据')
    if (!caseMetrics) return
    const inconsistency = baselineError(metrics.revenue, metrics.gross_margin, operatingCost)
    if (inconsistency) { message.warning(inconsistency); return }

    setSavingCase(true)
    try {
      if (operatingCost === null || operatingExpense === null) { message.warning('请填写营业成本和运营费用教学假设，以便确定性计算净利润'); return }
      const computedProfit = calculateNetProfit(caseMetrics.revenue, operatingCost, operatingExpense)
      if (computedProfit === null) { message.warning('请填写有效的营收、营业成本和运营费用'); return }
      if (netProfit !== null && Math.abs(netProfit - computedProfit) > 0.01) { message.warning('净利润与公式计算不一致，请核对成本、费用或营收'); return }
      await patchCase(detail.case.id, teacherToken, {
        title: title.trim(),
        background: background.trim(),
        dilemma: dilemma.trim(),
        base_metrics: caseMetrics,
        net_profit: computedProfit,
        financial_assumptions: { operating_cost: operatingCost, operating_expense: operatingExpense },
      })
      message.success('案例级信息已保存')
      await (onSaveRefresh ?? onRefresh)()
    } catch {
      // 失败提示由 axios 拦截器统一走 message.error
    } finally {
      setSavingCase(false)
    }
  }

  async function handleSaveNode(node: NodeDraft) {
    if (!detail) {
      message.warning('请先选择或生成一个案例')
      return
    }
    if (!node.title.trim()) {
      message.warning(`第 ${node.idx} 个节点：节点标题不能为空`)
      return
    }
    const options = validateOptions(node)
    if (!options) return

    setSavingNodeId(node.id)
    try {
      await patchNode(detail.case.id, node.id, teacherToken, {
        title: node.title.trim(),
        background: node.background,
        options,
      })
      message.success(`第 ${node.idx} 个节点已保存`)
      await (onSaveRefresh ?? onRefresh)()
    } catch {
      // 失败提示由 axios 拦截器统一走 message.error
    } finally {
      setSavingNodeId(null)
    }
  }

  function buildColumns(node: NodeDraft, renderImpact: (id: string) => ReactNode): TableColumnsType<OptionDraft> {
    return [
      {
        title: '选项键',
        dataIndex: 'key',
        width: '6%',
        render: (value: string) => <Tag>{value}</Tag>,
      },
      {
        title: '风险等级',
        dataIndex: 'risk_level',
        width: '10%',
        render: (_: RiskLevel, record: OptionDraft) => (
          <Select
            value={record.risk_level}
            options={RISK_OPTIONS}
            size="small"
            style={{ width: '100%', minWidth: 0 }}
            labelRender={() => (
              <Tag color={RISK_META[record.risk_level].color}>{record.risk_level}</Tag>
            )}
            onChange={(value: RiskLevel) => updateOption(node.id, record.key, { risk_level: value })}
          />
        ),
      },
      {
        title: '策略内容',
        dataIndex: 'label',
        width: '18%',
        render: (_: string, record: OptionDraft) => (
          <TextArea
            value={record.label}
            autoSize={{ minRows: 8, maxRows: 16 }}
            onChange={(event) => updateOption(node.id, record.key, { label: event.target.value })}
          />
        ),
      },
      {
        title: '结果摘要',
        dataIndex: 'summary',
        width: '18%',
        render: (_: string, record: OptionDraft) => (
          <TextArea
            value={record.summary}
            autoSize={{ minRows: 8, maxRows: 16 }}
            onChange={(event) => updateOption(node.id, record.key, { summary: event.target.value })}
          />
        ),
      },
      {
        title: '现金流说明',
        dataIndex: 'metrics',
        width: '18%',
        render: (_: MetricsDraft, record: OptionDraft) => (
          <div className="metrics-grid">
            {METRIC_FIELDS.filter(field => field.key === 'cash_flow').map((field) =>
              field.numeric ? (
                <InputNumber
                  key={field.key}
                  size="small"
                  min={0}
                  step={0.1}
                  value={record.metrics.revenue}
                  placeholder={field.label}
                  addonBefore={field.label}
                  onChange={(value) =>
                    updateOptionMetric(node.id, record.key, 'revenue', value ?? null)
                  }
                />
              ) : field.key === 'cash_flow' ? (
                <TextArea key={field.key} aria-label="现金流说明" autoSize={{ minRows: 8, maxRows: 16 }} style={{ width: '100%', minWidth: 0 }}
                  value={String(record.metrics.cash_flow ?? '')} placeholder={field.label}
                  onChange={(event) => updateOptionMetric(node.id, record.key, field.key, event.target.value)} />
              ) : (
                <Input
                  key={field.key}
                  size="small"
                  value={String(record.metrics[field.key] ?? '')}
                  placeholder={field.label}
                  addonBefore={field.label}
                  onChange={(event) =>
                    updateOptionMetric(node.id, record.key, field.key, event.target.value)
                  }
                />
              ),
            )}
          </div>
        ),
      },
      { title: '递进影响（每轮基于上一轮结果）', key: 'impact', width: '30%', render: (_: unknown, record: OptionDraft) => renderImpact(`${node.id}:${record.key}`) },
    ]
  }

  const statusMeta = detail ? STATUS_META[detail.case.status] : null

  return (
    <Card
      title="② 校准"
      extra={statusMeta ? <Tag color={statusMeta.color}>{statusMeta.text}</Tag> : undefined}
    >
      {!detail ? (
        <Typography.Text type="secondary">
          还没有载入案例：生成或选择一个案例后，这里会列出三个决策节点，可逐项修改节点标题、背景说明、选项文案与四项财务指标。
        </Typography.Text>
      ) : (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card size="small" type="inner" title="案例级信息（T5：标题 + 基准数据）">
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Input
              addonBefore="案例标题"
              value={title}
              maxLength={200}
              onChange={(event) => setTitle(event.target.value)}
            />
            <TextArea rows={3} value={background} onChange={(event) => setBackground(event.target.value)} placeholder="企业背景" />
            <TextArea rows={3} value={dilemma} onChange={(event) => setDilemma(event.target.value)} placeholder="核心经营困境" />
          <div className="metrics-grid">
            {METRIC_FIELDS.map((field) =>
                field.numeric ? (
                  <InputNumber
                    key={field.key}
                    min={0}
                    step={0.1}
                    value={metrics.revenue}
                    placeholder={field.label}
                    addonBefore={field.label}
                    style={{ width: 240 }}
                    onChange={(value) => {
                      const revenue = value ?? null
                      const linked = linkedBaseline(revenue, metrics.gross_margin, operatingCost, revenueLink)
                      setMetrics((prev) => ({ ...prev, revenue, gross_margin: linked?.margin ?? prev.gross_margin }))
                      if (linked) setOperatingCost(linked.cost)
                      setNetProfit(calculateNetProfit(revenue, linked?.cost ?? operatingCost, operatingExpense))
                    }}
                  />
                ) : field.key === 'cash_flow' ? (
                  <TextArea key={field.key} rows={5} autoSize={{ minRows: 5, maxRows: 12 }} style={CASH_FLOW_TEXTAREA_STYLE}
                    value={String(metrics.cash_flow ?? '')} placeholder={field.label}
                    onChange={(event) => setMetrics((prev) => ({ ...prev, cash_flow: event.target.value }))} />
                ) : (
                  <div key={field.key} style={{ width: 240 }}>
                  <Input
                    value={metricAnnotation(String(metrics[field.key] ?? '')).value}
                    placeholder={field.label}
                    addonBefore={field.label}
                    style={{ width: 240 }}
                    onChange={(event) => {
                      const note = metricAnnotation(String(metrics[field.key] ?? '')).note || metricAnnotation(String(detail.case.base_metrics?.[field.key] ?? '')).note
                      const value = event.target.value + (note ? ` ${note}` : '')
                      setMetrics((prev) => ({ ...prev, [field.key]: value }))
                      if (field.key === 'gross_margin') {
                        const linked = linkedBaseline(metrics.revenue, value, operatingCost, 'margin')
                        if (linked) { setOperatingCost(linked.cost); setNetProfit(calculateNetProfit(metrics.revenue, linked.cost, operatingExpense)) }
                      }
                    }}
                  />
                  <Typography.Text type="secondary" style={{ display: 'block', fontSize: 12, lineHeight: 1.5, marginTop: 4 }}>{(metricAnnotation(String(metrics[field.key] ?? '')).note || metricAnnotation(String(detail.case.base_metrics?.[field.key] ?? '')).note) && `注：${metricAnnotation(String(metrics[field.key] ?? '')).note || metricAnnotation(String(detail.case.base_metrics?.[field.key] ?? '')).note}`}</Typography.Text>
                  </div>
              ),
            )}
            <InputNumber min={0} step={0.1} value={operatingCost} placeholder="营业成本（万元）" addonBefore="营业成本"
              style={{ width: 240 }} onChange={(value) => {
                setOperatingCost(value)
                const linked = linkedBaseline(metrics.revenue, metrics.gross_margin, value, 'cost')
                if (linked) setMetrics(prev => ({ ...prev, gross_margin: linked.margin }))
                setNetProfit(calculateNetProfit(metrics.revenue, value, operatingExpense))
              }} />
            <InputNumber min={0} step={0.1} value={operatingExpense} placeholder="运营费用（万元）" addonBefore="运营费用"
              style={{ width: 240 }} onChange={(value) => { setOperatingExpense(value); setNetProfit(calculateNetProfit(metrics.revenue, operatingCost, value)) }} />
            <InputNumber precision={2} value={netProfit} readOnly placeholder="净利润（万元）" addonBefore="净利润 · 自动计算" style={{ width: 260 }} />
          </div>
            <div className="node-actions">
              <Typography.Text type="secondary">
              修改营收时：<Select aria-label="营收联动方式" value={revenueLink} style={{ width: 180 }} options={[{ value: 'margin', label: '保持毛利率，计算成本' }, { value: 'cost', label: '保持成本，计算毛利率' }]} onChange={setRevenueLink} />。直接修改毛利率会计算成本；直接修改成本会反算毛利率。净利润同步计算。营收为零时无法反算毛利率。
              </Typography.Text>
              <Button type="primary" loading={savingCase} onClick={() => void handleSaveCase()}>
                保存案例信息
              </Button>
            </div>
            <Typography.Paragraph type="secondary" style={{ margin: 0, fontSize: 12 }}>
              企业背景：{detail.case.background || '（空）'}
              <br />
              核心经营困境：{detail.case.dilemma || '（空）'}
              <br />
              这两项是学生端首屏题面，当前接口（T5）只接受标题与基准数据，暂不可编辑。
            </Typography.Paragraph>
          </Space>
        </Card>

        {nodes.length === 0 ? (
          <Typography.Text type="secondary">
            基准指标校准并确认后，系统才生成决策节点。请先完成基准数据、营业成本与运营费用，然后启动节点生成。
          </Typography.Text>
        ) : (
          <ProgressivePanel detail={detail} token={teacherToken} onSummariesSaved={summaries => {
            setNodes(current => current.map(node => ({ ...node, options: node.options.map(option => ({ ...option, summary: summaries[`${node.id}:${option.key}`] ?? option.summary })) })))
          }}>{renderImpact => <Collapse
            key={detail.case.id}
            defaultActiveKey={nodes.map((node) => String(node.id))}
            items={nodes.map((node) => ({
              key: String(node.id),
              label: (
                <Space size={8} wrap>
                  <Tag color="blue">{node.node_role}</Tag>
                  <Typography.Text strong>第 {node.idx} 个节点</Typography.Text>
                  <Typography.Text type="secondary">{node.title}</Typography.Text>
                </Space>
              ),
              children: (
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  <Input
                    addonBefore="节点标题"
                    value={node.title}
                    onChange={(event) => updateNode(node.id, { title: event.target.value })}
                  />
                  <TextArea
                    rows={3}
                    value={node.background}
                    placeholder="节点背景说明（学生端在节点内展示）"
                    onChange={(event) => updateNode(node.id, { background: event.target.value })}
                  />
                  <Table<OptionDraft>
                    size="small"
                    rowKey="key"
                    columns={buildColumns(node, renderImpact)}
                    dataSource={node.options}
                    pagination={false}
                    tableLayout="fixed"
                    onRow={() => ({ style: { verticalAlign: 'top' } })}
                    scroll={{ x: 1000 }}
                  />
                  <Collapse size="small" items={[{ key: 'basis', label: '指标计算 / 推测依据（教师核对）', children: <Space direction="vertical" style={{ width: '100%' }}>
                    <Typography.Text type="secondary">营收、毛利率、市场份额、现金流：{node.options[0]?.financial_basis?.source || '由AI按案例素材和行业/传导规则提出，属于情境推测；非确定性财报事实。'}</Typography.Text>
                    {node.options.map((option) => <Card key={option.key} size="small" title={`选项 ${option.key} · ${option.risk_level}`}>
                      {Object.values(option.financial_basis?.indicators || {}).map((indicator, i) => <Typography.Paragraph key={`${option.key}-${i}`} style={{ marginBottom: 4 }}>
                        <b>{indicator.label}：</b>{indicator.formula ? `${indicator.formula}；` : ''}{indicator.before !== undefined ? `基准 ${indicator.before} → ` : ''}{indicator.after ?? '未计算'}；{indicator.method}
                        {indicator.inputs && <>；输入 {JSON.stringify(indicator.inputs)}</>}
                      </Typography.Paragraph>)}
                      <Typography.Text type="secondary">净利润暂按教师校准的教学估算假设计算：营收 − 营业成本 − 运营费用 = {(option.calculated_net_profit ?? calculateNetProfit(Number(option.metrics.revenue || 0), Number(option.financial_assumptions?.operating_cost || 0), Number(option.financial_assumptions?.operating_expense || 0)))?.toFixed(2)} 万元。请核对输入假设是否符合案例材料。</Typography.Text>
                    </Card>)}
                  </Space> }]}/>
                  <div className="node-actions">
                    <Typography.Text type="secondary">
                      保存后直接覆盖该节点的标题、背景与三个选项，学生端同步为最新版
                    </Typography.Text>
                    <Button
                      type="primary"
                      loading={savingNodeId === node.id}
                      onClick={() => void handleSaveNode(node)}
                    >
                      保存节点
                    </Button>
                  </div>
                </Space>
              ),
            }))}
          />}</ProgressivePanel>
        )}
        {nodeError && <Alert showIcon type="error" message="决策节点生成失败" description={nodeError} closable onClose={() => setNodeError('')} />}
        {nodeProgressError && <Alert showIcon type="warning" message={nodeProgressError} closable onClose={() => setNodeProgressError('')} />}
        {nodes.length === 0 && <Button type="primary" loading={generatingNodes} disabled={savingCase || operatingCost === null || operatingExpense === null || !detail.case.base_metrics}
          onClick={async () => {
            setGeneratingNodes(true)
            setNodeError('')
            setNodeProgressError('')
            let taskAccepted = false
            try {
              const base = validateMetrics(metrics, '确认基准指标')
              if (!base || operatingCost === null || operatingExpense === null) return
              const profit = base.revenue - operatingCost - operatingExpense
              const payload = { base_metrics: base, net_profit: profit,
                financial_assumptions: { operating_cost: operatingCost, operating_expense: operatingExpense } }
              const task = await generateCaseNodes(detail.case.id, teacherToken, { base_metrics: base, net_profit: profit,
                financial_assumptions: { operating_cost: operatingCost, operating_expense: operatingExpense },
                idempotency_key: stableIdempotencyKey(`nodes-case-${detail.case.id}-v${detail.case.version}-a${nodeGenerationAttempt}`, payload) })
              setNodeTask(task)
              taskAccepted = true
              setGeneratingNodes(pendingTask(task))
              message.success('已确认基准指标，决策节点开始后台生成')
              await onRefresh()
            } catch (error) {
              const detail = error instanceof Error ? error.message : '请求未能提交，请检查网络与服务日志后重试。'
              setNodeError(detail)
              message.error(detail)
            } finally {
              if (!taskAccepted) setGeneratingNodes(false)
            }
          }}>确认财务基准并生成决策节点</Button>}
        {nodeTask && <TaskStatus task={nodeTask} busy={generatingNodes} />}
      </Space>
      )}
    </Card>
  )
}
