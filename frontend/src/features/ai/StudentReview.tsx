import { useEffect, useState } from 'react'
import { Alert, Button, Card, Spin, Space, Typography } from 'antd'
import { review, retryStudentReview } from './api'
import type { ReviewResponse } from '../../api/types'

const POLL_MS = 2000

export function comparisonParagraphs(content: string) {
  // Insert whitespace only: keep every claim, number and punctuation unchanged.
  return content.replace(/(第\s*[一二三123]\s*轮|累计|建议[：:])/g, '\n\n$1')
    .replace(/([，。；;])\s*(?=(?:学生(?:营收|收入|毛利率|现金)|参考(?:营收|收入|毛利率|现金)|差额))/g, '$1\n')
    .split(/\n\s*\n/).map(text => text.trim()).filter(Boolean)
}

export default function StudentReview({ token, sid, onRestart, onBack }: { token: string; sid: number; onRestart: () => void; onBack: () => void }) {
  const [data, setData] = useState<ReviewResponse | null>(null)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [connection, setConnection] = useState('')

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined
    let wake: (() => void) | undefined
    const controller = new AbortController()
    const wait = (ms: number) => new Promise<void>((resolve) => {
      wake = resolve
      timer = window.setTimeout(resolve, ms)
    })
    setError('')
    setConnection('')
    setData(null)
    async function load() {
      if (!sid) {
        setError('缺少推演会话')
        return
      }
      let failures = 0
      while (!cancelled) {
        try {
          const result = await review(token, sid, controller.signal)
          if (cancelled) return
          failures = 0
          setConnection('')
          setData(result)
          if (result.status !== 'pending' && result.status !== 'running') return
          await wait(POLL_MS)
        } catch (cause) {
          if (cancelled) return
          const status = (cause as { status?: number })?.status
          if (status && status >= 400 && status < 500 && status !== 408 && status !== 429) {
            setError(cause instanceof Error ? cause.message : '复盘暂不可用')
            return
          }
          failures += 1
          setConnection('暂时无法查询进度，正在自动重连；这不代表 AI 生成失败。')
          await wait(Math.min(30000, POLL_MS * 2 ** Math.min(failures, 4)))
        }
      }
    }
    void load()
    return () => { cancelled = true; controller.abort(); window.clearTimeout(timer); wake?.() }
  }, [token, sid, refresh])

  if (error) return <Space direction="vertical"><Alert type="warning" message="复盘暂不可用" description={error} /><Button onClick={() => setRefresh((x) => x + 1)}>重新查看</Button></Space>
  if (!data) {
    return <Space direction="vertical"><Spin /><Typography.Text>正在加载复盘，请稍候。</Typography.Text>{connection && <Alert type="warning" message={connection} />}</Space>
  }
  if (data.status === 'failed') return <Space direction="vertical"><Alert type="warning" message="AI 复盘暂时失败，完成后再展示方案对比。" /><Button onClick={async () => { try { await retryStudentReview(token, sid); setData(null); setRefresh(x => x + 1) } catch(e) { setError(e instanceof Error ? e.message : '重试失败') } }}>重试分析</Button></Space>
  if (data.status !== 'succeeded') return <Space direction="vertical"><Spin /><Typography.Text>正在生成复盘，请稍候。</Typography.Text>{connection && <Alert type="warning" message={connection} />}</Space>
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Typography.Title level={3}>推演复盘</Typography.Title>
      <Typography.Text type="secondary">{data.framework_type}</Typography.Text>
      {<>
      <Card title="分析维度">
        {data.dimensions.map((dimension, index) => (
          <Card size="small" key={`${dimension.name}-${index}`} title={dimension.name}>
            {dimension.name === '参考路径差异与影响' ? comparisonParagraphs(dimension.content).map((paragraph, i) => <Typography.Paragraph key={i} style={{ whiteSpace: 'pre-wrap', lineHeight: 1.8, overflowWrap: 'anywhere', marginBottom: 16 }}>{paragraph}</Typography.Paragraph>) : dimension.content}
          </Card>
        ))}
      </Card>
      <Card title="综合结论">{data.conclusion}</Card>
      </>}
      {data.status === 'succeeded' && data.path_comparison && <Card title={`参考方案（标准路径）· ${data.path_comparison.kind === 'historical' ? '案例实际采用路径' : '教师推荐方案'}`}>
        <Typography.Paragraph><strong>我的方案：</strong>{data.path_comparison.rounds.map(row => row.student_strategy).join(' → ')}</Typography.Paragraph>
        <Typography.Paragraph><strong>教师/推荐方案：</strong>{data.path_comparison.rounds.map(row => row.reference_strategy).join(' → ')}</Typography.Paragraph>
        <Typography.Paragraph type="secondary">{data.path_comparison.note}</Typography.Paragraph>
        {data.path_comparison.rounds.map(row => <Card size="small" key={row.round} title={`第 ${row.round} 轮 · ${row.same ? '策略一致' : '策略不同'}`}>
          <p>我的策略：{row.student_strategy}</p><p>参考策略：{row.reference_strategy}</p>
          {([['revenue','营收'],['operating_cost','营业成本'],['operating_expense','运营费用'],['net_profit','净利润'],['cash_flow_amount','现金净流量']] as const).map(([key,label]) => <div key={key} style={{ borderTop: '1px solid #f0f0f0', padding: '10px 0', lineHeight: 1.8, overflowWrap: 'anywhere' }}><strong>{label}（万元）</strong><div>我的方案：{row.student_metrics[key] ?? '未记录'}</div><div>教师/推荐方案：{row.reference_metrics[key]}</div><div>差额：{row.difference[key] ?? '未记录'}</div></div>)}
          <p>毛利率：我 {row.student_metrics.gross_margin} / 参考 {row.reference_metrics.gross_margin}；市场份额：我 {row.student_metrics.market_share} / 参考 {row.reference_metrics.market_share}</p>
        </Card>)}
      </Card>}
      <Space direction="vertical" style={{ width: '100%' }}>
        <Button block onClick={onBack}>返回推演并修改决策</Button>
        <Button size="large" type="primary" block onClick={onRestart}>再试一次（覆盖原记录）</Button>
      </Space>
    </Space>
  )
}
