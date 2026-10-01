export function marginValue(value: string): number | null {
  const text = value.trim().split(/[%％]/)[0]
  if (!text) return null
  const n = Number(text)
  return Number.isFinite(n) && n >= 0 && n <= 100 ? n : null
}
export function linkedBaseline(revenue: number | null, margin: string, cost: number | null, mode: 'margin' | 'cost') {
  if (revenue === null || !Number.isFinite(revenue) || revenue < 0) return null
  if (mode === 'margin') {
    const rate = marginValue(margin)
    return rate === null ? null : { margin, cost: Math.round((revenue * (1 - rate / 100) + Number.EPSILON) * 100) / 100 }
  }
  if (cost === null || !Number.isFinite(cost) || cost < 0 || cost > revenue || revenue === 0) return null
  return { cost, margin: `${(1 - cost / revenue) * 100}%` }
}
export function baselineError(revenue: number | null, margin: string, cost: number | null): string | null {
  if (revenue === null || !Number.isFinite(revenue) || revenue < 0) return '请输入有效的非负营收'
  if (marginValue(margin) === null) return '毛利率须为 0–100%'
  if (cost === null || !Number.isFinite(cost) || cost < 0) return '请输入有效的非负营业成本'
  const expected = linkedBaseline(revenue, margin, cost, 'margin')!.cost
  if (Math.abs(expected - cost) > 0.02) return `基准不一致：按营收和毛利率计算，营业成本应为 ${expected.toFixed(2)} 万元，当前为 ${cost.toFixed(2)} 万元，差额 ${(cost - expected).toFixed(2)} 万元。请调整成本或毛利率后保存。`
  return null
}
