import type { Metrics } from '../api/types'

const fields: [keyof Metrics, string][] = [
  ['revenue', '营收（万元）'], ['gross_margin', '毛利率'],
  ['market_share', '市场份额'], ['cash_flow', '现金流'],
]

export default function MetricComparison({ before, after }: { before?: Metrics | null; after?: Metrics | null }) {
  const cell = { padding: '8px 12px', borderBottom: '1px solid #eee', textAlign: 'left' as const, verticalAlign: 'top' as const, overflowWrap: 'anywhere' as const }
  const value = (metrics: Metrics | null | undefined, key: keyof Metrics) => metrics?.[key] ?? '未记录'
  return <div style={{ overflowX: 'auto' }}><table aria-label="决策前后指标对比" style={{ width: '100%', tableLayout: 'fixed', borderCollapse: 'collapse' }}>
    <thead><tr><th style={{ ...cell, width: '24%' }}>经营指标</th><th style={cell}>决策前</th><th style={cell}>决策后</th></tr></thead>
    <tbody>{fields.map(([key, label]) => <tr key={key}><th scope="row" style={cell}>{label}</th><td style={cell}>{value(before, key)}</td><td style={cell}>{value(after, key)}</td></tr>)}</tbody>
  </table></div>
}
