import type { CaseStatus, CaseType, FrameworkType, Metrics, RiskLevel } from '../api/types'

/** 案例类型下拉（取值与 docs/商科规则.json 的 case_types 一致） */
export const CASE_TYPE_OPTIONS: { value: CaseType; label: string }[] = [
  { value: '战略决策类', label: '战略决策类' },
  { value: '市场营销类', label: '市场营销类' },
  { value: '财务管理类', label: '财务管理类' },
]

/** 商科规则 §4.5：案例类型与核心分析框架一一对应，用于界面提示 */
export const FRAMEWORK_BY_CASE_TYPE: Record<CaseType, FrameworkType> = {
  战略决策类: 'SWOT分析模型',
  市场营销类: '4P营销理论',
  财务管理类: '盈亏平衡分析',
}

/** 风险等级 Tag 配色：保守=灰、稳健=蓝、激进=红 */
export const RISK_META: Record<RiskLevel, { color: string }> = {
  保守: { color: 'default' },
  稳健: { color: 'blue' },
  激进: { color: 'red' },
}

export const RISK_LEVELS: RiskLevel[] = ['保守', '稳健', '激进']

export const RISK_OPTIONS = RISK_LEVELS.map((level) => ({ value: level, label: level }))

/** case.status 四态展示 */
export const STATUS_META: Record<CaseStatus, { text: string; color: string }> = {
  draft: { text: '草稿（生成失败）', color: 'default' },
  generating: { text: 'AI 生成中', color: 'gold' },
  ready: { text: '已就绪（可校准）', color: 'blue' },
  published: { text: '已发布', color: 'green' },
}

/** 四项指标的键与中文名（顺序固定） */
export const METRIC_FIELDS: { key: keyof Metrics; label: string; numeric: boolean }[] = [
  { key: 'revenue', label: '营收（万元）', numeric: true },
  { key: 'gross_margin', label: '毛利率', numeric: false },
  { key: 'market_share', label: '市场份额', numeric: false },
  { key: 'cash_flow', label: '现金流', numeric: false },
]

export const CASH_FLOW_TEXTAREA_STYLE = { width: '100%', minHeight: 140, resize: 'vertical' as const }

/** 时间显示：后端给 ISO 8601，界面只到分钟 */
export function formatTime(iso: string): string {
  if (!iso) return '-'
  return iso.replace('T', ' ').slice(0, 16)
}

/** 决策耗时（毫秒 → 秒） */
export function formatDuration(ms: number): string {
  if (typeof ms !== 'number' || Number.isNaN(ms)) return '-'
  return `${(ms / 1000).toFixed(1)} 秒`
}
