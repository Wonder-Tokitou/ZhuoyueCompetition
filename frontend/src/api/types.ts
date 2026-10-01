/**
 * 前端接口类型定义，与 SPEC.md §2 接口清单、§3 接口详情一一对应。
 * 字段名以 SPEC.md 为准，不得在这里改写（改接口的顺序：先改 SPEC.md 再改代码）。
 */

export type CaseStatus = 'draft' | 'generating' | 'ready' | 'published'
export type CaseType = '战略决策类' | '市场营销类' | '财务管理类'
export type SourceKind = 'material' | 'topic'
export type OwnerType = 'teacher' | 'student_ephemeral'
export type RiskLevel = '保守' | '稳健' | '激进'
export type FrameworkType = 'SWOT分析模型' | '4P营销理论' | '盈亏平衡分析'
/** 回合结果来源：预设选项 / 学生自定义输入 */
export type ResultSource = 'preset' | 'progressive' | 'custom_llm' | 'fallback_balanced'

/** §0.2 固定四指标 */
export interface Metrics {
  /** 营收，单位万元 */
  revenue: number
  /** 毛利率，如 "62.0%" */
  gross_margin: string
  /** 市场份额，如 "20.0%" */
  market_share: string
  /** 现金流方向描述，如 "短期收紧" */
  cash_flow: string
}

export interface FinancialAssumptions { operating_cost: number; operating_expense: number }
export interface FinancialIndicatorBasis { label: string; before?: number | string | null; after?: number | string | null; formula?: string; inputs?: Record<string, number>; method: string }
export interface FinancialBasis { source: string; indicators: Record<string, FinancialIndicatorBasis> }

/** S2/T9 差值：百分比为百分点差，现金流保留前后文字方向。 */
export interface MetricDelta {
  revenue: number
  gross_margin: number
  market_share: number
  cash_flow: {
    from: string
    to: string
  }
}

/** §0.2 选项后果对象（S1 不提前下发；S2 可返回已选结果） */
export interface OptionResult {
  metrics: Metrics
  summary: string
}

/** 学生端选项：只有 key 与 label */
export interface OptionBrief {
  key: string
  label: string
}

/** 教师端选项：多出风险等级与后果 */
export interface OptionDetail extends OptionBrief {
  risk_level: RiskLevel
  metrics: Metrics
  net_profit?: number | null
  calculated_net_profit?: number | null
  financial_assumptions?: FinancialAssumptions | null
  financial_basis?: FinancialBasis | null
  summary: string
}

interface NodeBase {
  id: number
  idx: number
  scenario: string
  node_role: string
  title: string
  background: string
}

export interface StudentNode extends NodeBase {
  options: OptionBrief[]
}

export interface TeacherNode extends NodeBase {
  options: OptionDetail[]
}

/* ---------- 老师端 ---------- */

/** T1 */
export interface LoginResponse {
  teacher_token: string
}

/** T2 请求（multipart：file 与 text 二选一；case_type 必填，缺失后端返 400） */
export interface CreateCasePayload {
  title: string
  case_type: CaseType
  text?: string
  file?: File
  framework_id?: number
}

/** T2 响应；生成失败时降级为 200 + warning + errors */
export interface CreateCaseResponse {
  case_id: number
  status: CaseStatus
  warning?: string
  /** 模型或商科校验给出的失败原因，仅在 warning 出现时返回 */
  errors?: string[]
  task_id?: string
}

export interface AiTaskResponse {
  task_id: string
  case_id: number
  kind: string
  status: string
  stage: string
  attempts: number
  idempotency_key?: string | null
  error?: string | null
  output_url?: string | null
}

/** T3 */
export interface CaseListItem {
  id: number
  title: string
  status: CaseStatus
  created_at: string
}

/** T4 的 case 字段（case 表 16 列，含本次新增的 version） */
export interface CaseDetail {
  id: number
  title: string
  source_text: string
  status: CaseStatus
  framework_id: number | null
  teacher_token: string
  student_token: string
  created_at: string
  published_at: string | null
  case_type: CaseType
  source_kind: SourceKind
  base_metrics: Metrics | null
  base_net_profit?: number | null
  financial_assumptions?: FinancialAssumptions | null
  owner_type: OwnerType
  /** 企业背景（题面；T5 不支持编辑，仅展示） */
  background: string
  /** 核心经营困境（题面；T5 不支持编辑，仅展示） */
  dilemma: string
  /** 题面版本号，初始 1；教师每次改动（T5 / T6）成功落库后 +1 */
  version: number
}

/** T4 响应 */
export interface GetCaseResponse {
  case: CaseDetail
  nodes: TeacherNode[]
}

/** T5 */
export interface PatchCasePayload {
  title?: string
  base_metrics?: Metrics
  background?: string
  dilemma?: string
  net_profit?: number
  financial_assumptions?: FinancialAssumptions
}

export interface GenerateNodesPayload { base_metrics: Metrics; net_profit?: number; financial_assumptions: FinancialAssumptions; idempotency_key: string }
/** T6 */
export interface PatchNodePayload {
  /** 与 background 同源，传任意一个后端都会同时写两列 */
  scenario?: string
  title?: string
  background?: string
  options?: OptionDetail[]
}

/** T7 */
export interface AiFixPayload {
  node_id?: number
  message: string
}

export interface AiFixResponse {
  reply: string
}

export interface OkResponse {
  ok: boolean
}

/** T8 */
export interface PublishResponse {
  student_url: string
  qr_code_url: string
}

/** T9 turns 元素 */
export interface Turn {
  id: number
  node_id: number
  idx: number
  chosen_option: string
  display_option?: string | null
  chosen_label?: string | null
  input_text: string | null
  /** 选择前的指标状态，等于上一回合的 after_metrics */
  before_metrics: Metrics | null
  /** 选择后的指标状态（目标状态快照） */
  after_metrics: Metrics | null
  /** after − before；百分比为百分点差，cash_flow 为 {from, to} */
  delta_metrics: MetricDelta | null
  result_source: ResultSource | null
  /** 兼容字段：{metrics, summary} 快照，等于 after_metrics + summary */
  result_json: OptionResult
  duration_ms: number
  created_at: string
}

/** T9 响应元素（按 case_id + student_token + student_name + attempt 分组） */
export interface StudentRecord {
  student_name: string
  student_id?: number | null
  /** 第几次尝试：同一 session 刷新沿用；显式“再试一次”时服务端递增 */
  session_id?: number
  case_id?: number
  case_title?: string
  student_token?: string
  case_version?: number
  attempt_no?: number
  created_at?: string
  finished_at?: string | null
  turn_count?: number
  available?: boolean
  turns: Turn[]
  review_id?: number
  review_status?: string
  review?: ReviewResponse | null
}

export interface StudentSubmission {
  session_id: number
  case_id: number
  case_title: string
  student_token: string
  case_version: number
  attempt_no: number
  created_at: string
  finished_at: string | null
  turn_count: number
  available: boolean
  turns: { node_id: number; chosen_option: string; display_option?: string | null; chosen_label?: string | null; input_text: string | null; result: OptionResult }[]
  review: { conclusion: string; dimensions: { name: string; content: string }[] } | null
}

/* ---------- 学生端 ---------- */

/** S1（只有未选择选项的 risk_level / summary / metrics 不得提前下发） */
export interface PlayResponse {
  case_title: string
  case_type: CaseType
  background: string
  dilemma: string
  base_metrics: Metrics | null
  nodes: StudentNode[]
}

/** S2 请求（attempt_no 不由客户端传入，由服务端按案例/令牌/姓名管理） */
export interface DecidePayload {
  student_name: string
  session_id?: number
  node_id: number
  option_key?: string
  input_text?: string
  /** 前端统计的本回合决策耗时（毫秒），必填且 >= 0，写入 turn.duration_ms */
  duration_ms: number
}

export interface SessionState {
  financial_state?: { net_profit: number; cash_flow_amount: number } | null
  session_id: number
  student_name: string
  case_version: number
  current_metrics: Metrics
  next_node_id: number | null
  finished: boolean
  play: PlayResponse
  turns: { node_id: number; chosen_option: string; display_option?: string | null; chosen_label?: string | null; input_text?: string; after_metrics: Metrics; summary: string }[]
}

/** S2 的 result：本次**已选择之后**的结果，不是提前泄露的答案 */
export interface DecideResult {
  /** 选择前状态；首回合为 case.base_metrics */
  before_metrics: Metrics | null
  /** 选择后状态（目标状态快照，不是可直接累加的增量） */
  after_metrics: Metrics | null
  delta_metrics: MetricDelta | null
  summary: string
  source: ResultSource
  /** 仅在需要提示时出现，例如自定义决策「未套用预设测算」 */
  warning?: string | null
}

/** S2 响应 */
export interface DecideResponse {
  session_id: number
  result: DecideResult
  next_node_id: number | null
}

/** T10 PATCH /api/reviews/{review_id}?token=：教师人工修正学生复盘 */
export interface PatchReviewPayload {
  /** 动态分析项：保留有效返回的数量与顺序；综合结论独立存储。 */
  dimensions?: ReviewDimension[]
  conclusion?: string
}

/** S4：动态分析项；必须覆盖框架基础内容，可含有依据的补充项。 */
export interface ReviewDimension {
  name: string
  content: string
}

export interface ReviewResponse {
  path_comparison?: {
    kind: 'recommended' | 'historical'; note: string
    rounds: { round: number; student_strategy: string; reference_strategy: string; same: boolean; student_metrics: Record<string, number | string>; reference_metrics: Record<string, number | string>; difference: Record<string, number> }[]
  } | null
  framework_type: FrameworkType
  /** 模型返回的分析项原样保存，不强制数量或顺序。 */
  dimensions: ReviewDimension[]
  /** 独立综合结论。 */
  conclusion: string
  status?: 'pending' | 'running' | 'succeeded' | 'failed'
  task_id?: string | null
  stage?: string | null
  attempts?: number
}

/** S5 请求 */
export interface TryCasePayload {
  text: string
  title?: string
  case_type: CaseType
}

/** S5 / S6 的试跑选项：学生自建案例可返回 risk_level / summary / metrics */
export interface TryOption {
  key: string
  label: string
  risk_level: RiskLevel
  summary: string
  metrics: Metrics
}

/** S5 / S6 的试跑节点；试跑不落库，`id` 由服务端用 idx 代替 */
export interface TryNode {
  id: number
  idx: number
  node_role: string
  title: string
  background: string
  options: TryOption[]
}

/** S5 / S6 响应（TryCaseEcho）：试跑内容只存内存，重启即失效 */
export interface TryCaseResponse {
  try_id: string
  case_type: CaseType
  background: string
  base_metrics: Metrics | null
  nodes: TryNode[]
}
