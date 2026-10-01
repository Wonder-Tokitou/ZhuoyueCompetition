import { useEffect, useMemo, useState } from 'react'
import { Alert, App as AntdApp, Button, Card, Space, Typography } from 'antd'
import { CheckCircleTwoTone, CloseCircleTwoTone } from '@ant-design/icons'
import { publishCase, api } from '../../../api/client'
import type { GetCaseResponse, Metrics, PublishResponse } from '../../../api/types'
import { METRIC_FIELDS } from '../teacherConstants'

interface PublishPanelProps {
  teacherToken: string
  detail: GetCaseResponse | null
  publishResult: PublishResponse | null
  onPublished: (result: PublishResponse) => void
}

/**
 * 发布区：先展示校验状态，再发布。
 * 发布返回的二维码地址走 `/api/media/...` 前缀，直接以 <img src> 引用。
 * （用 `/api` 前缀是因为线上 Nginx 只反代 `/api/`，`/media/*` 会被它的
 *   try_files 兜底成 index.html，导致图片打不开。）
 */
export default function PublishPanel({
  teacherToken,
  detail,
  publishResult,
  onPublished,
}: PublishPanelProps) {
  const { message } = AntdApp.useApp()
  const [publishing, setPublishing] = useState(false)
  const [qrFailed, setQrFailed] = useState(false)

  useEffect(() => {
    setQrFailed(false)
  }, [publishResult])

  const checks = useMemo(() => {
    const nodes = detail?.nodes ?? []
    const base: Metrics | null = detail?.case.base_metrics ?? null
    const metricsOk =
      base != null &&
      METRIC_FIELDS.every((field) => {
        const value = base[field.key]
        return value !== undefined && value !== null && String(value).trim() !== ''
      })
    return [
      { ok: nodes.length === 3, text: '三个决策节点齐全（核心战略 / 核心策略 / 落地执行）' },
      { ok: nodes.every((node) => node.options.length === 3), text: '每个节点各有 3 个选项' },
      {
        ok: nodes.length > 0 && nodes.every((node) => new Set(node.options.map((o) => o.risk_level)).size === 3),
        text: '每个节点的风险等级为保守 / 稳健 / 激进各一个',
      },
      {
        ok: nodes.every((node) => node.options.every((o) => o.summary.trim() !== '')),
        text: '每个选项的结果摘要都已填写',
      },
      { ok: metricsOk, text: '四项基准数据齐全' },
      {
        ok: detail?.case.status === 'ready' || detail?.case.status === 'published',
        text: 'AI 生成已完成（status 为 ready 或 published）',
      },
    ]
  }, [detail])

  const failedChecks = checks.filter((item) => !item.ok)

  async function doPublish() {
    if (!detail) return
    setPublishing(true)
    try {
      const { data: simulation } = await api.get<{ draft: { enabled: boolean } | null }>(`/cases/${detail.case.id}/simulation`, { params: { token: teacherToken } })
      if (!simulation.draft?.enabled) {
        message.warning('请先在“递进推演”中补充现金金额、核对并保存9条规则，再发布。当前不会按旧固定结果模式发布。')
        return
      }
      const result = await publishCase(detail.case.id, teacherToken)
      onPublished(result)
      message.success('递进模式已发布。新推演使用新规则；旧记录需主动覆盖重做才切换。')
    } catch {
      // 失败提示由 axios 拦截器统一走 message.error
    } finally {
      setPublishing(false)
    }
  }

  function handlePublish() {
    if (!detail) {
      message.warning('请先选择或生成一个案例')
      return
    }
    if (detail.nodes.length === 0) {
      message.warning('案例还没有节点，无法发布')
      return
    }
    if (failedChecks.length > 0) {
      message.error(`发布前校验未通过：${failedChecks.map((item) => item.text).join('；')}`)
      return
    }
    void doPublish()
  }

  const absoluteUrl = publishResult
    ? `${typeof window === 'undefined' ? '' : window.location.origin}${publishResult.student_url}`
    : ''

  return (
    <Card title="③ 发布">
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        {!detail ? (
          <Typography.Text type="secondary">
            还没有载入案例：生成或选择一个案例后，这里会逐条显示发布前的校验状态。
          </Typography.Text>
        ) : (
          <div className="check-list">
            {checks.map((item) => (
              <div className="check-item" key={item.text}>
                {item.ok ? (
                  <CheckCircleTwoTone twoToneColor="#52c41a" />
                ) : (
                  <CloseCircleTwoTone twoToneColor="#d9d9d9" />
                )}
                <span>{item.text}</span>
              </div>
            ))}
          </div>
        )}

        <div className="node-actions">
          <Typography.Text type="secondary">
            发布后生成 32 位学生端令牌；重复发布会复用原链接，已分发的二维码不失效
          </Typography.Text>
          <Button
            type="primary"
            loading={publishing}
            disabled={!detail || failedChecks.length > 0}
            onClick={() => void handlePublish()}
          >
            发布案例
          </Button>
        </div>

        {publishResult && (
          <div className="publish-result">
            <div className="publish-link">
              <Typography.Text strong>学生端链接</Typography.Text>
              <Typography.Text copyable={{ text: absoluteUrl }}>{absoluteUrl}</Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                公网测试时可远程访问；接收者需使用同一测试空间的密钥登录。本地启动时需在同一网络。
              </Typography.Text>
            </div>
            <div className="qr-box">
              {qrFailed ? (
                <Alert
                  type="warning"
                  showIcon
                  message="二维码图片加载失败"
                  description="请确认后端已启动，且 /media 目录下已生成对应 PNG。"
                />
              ) : (
                <>
                  <img
                    src={publishResult.qr_code_url}
                    alt="学生端二维码"
                    onError={() => setQrFailed(true)}
                  />
                  <Typography.Text type="secondary">扫码进入学生端推演</Typography.Text>
                </>
              )}
            </div>
          </div>
        )}
      </Space>
    </Card>
  )
}
