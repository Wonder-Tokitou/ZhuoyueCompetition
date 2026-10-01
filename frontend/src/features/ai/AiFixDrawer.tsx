import { useState } from 'react'
import {
  App as AntdApp,
  Button,
  Drawer,
  FloatButton,
  Input,
  Select,
  Space,
  Spin,
  Typography,
} from 'antd'
import { RobotOutlined, SendOutlined } from '@ant-design/icons'
import { aiFix } from './api'
import type { TeacherNode } from '../../api/types'
import './ai.css'

const { TextArea } = Input

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

interface AiFixDrawerProps {
  teacherToken: string
  caseId: number | null
  nodes: TeacherNode[]
}

/**
 * AI 校准对话框：右下角浮动按钮打开 Drawer，回复按气泡渲染。
 * 只出建议、不落库（T7 语义），老师确认后自己改并保存。
 */
export default function AiFixDrawer({ teacherToken, caseId, nodes }: AiFixDrawerProps) {
  const { message } = AntdApp.useApp()
  const [open, setOpen] = useState(false)
  const [nodeId, setNodeId] = useState<number | undefined>(undefined)
  const [input, setInput] = useState('')
  const [thread, setThread] = useState<ChatMessage[]>([])
  const [sending, setSending] = useState(false)

  async function handleSend() {
    const text = input.trim()
    if (!text) {
      message.warning('请输入要问 AI 的问题')
      return
    }
    if (caseId == null) {
      message.warning('请先在页面上选择或生成一个案例')
      return
    }

    setThread((prev) => [...prev, { role: 'user', content: text }])
    setInput('')
    setSending(true)
    try {
      const { reply } = await aiFix(caseId, teacherToken, { node_id: nodeId, message: text })
      setThread((prev) => [
        ...prev,
        { role: 'assistant', content: reply || '（模型没有返回内容，请换个问法重试）' },
      ])
    } catch {
      // 失败提示由 axios 拦截器统一走 message.error；提问气泡保留，方便直接重试
    } finally {
      setSending(false)
    }
  }

  return (
    <>
      <FloatButton
        type="primary"
        icon={<RobotOutlined />}
        tooltip="AI 校准助手"
        onClick={() => {
          if (caseId == null) {
            message.warning('请先在页面上选择或生成一个案例')
            return
          }
          setOpen(true)
        }}
      />
      <Drawer
        title="AI 校准助手"
        placement="right"
        width={440}
        open={open}
        onClose={() => setOpen(false)}
        styles={{ body: { padding: 16 } }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
          <Space direction="vertical" size={4} style={{ width: '100%', marginBottom: 12 }}>
            <Space size={8}>
              <Typography.Text type="secondary">讨论范围</Typography.Text>
              <Select
                value={nodeId}
                allowClear
                placeholder="整个案例"
                style={{ width: 210 }}
                options={nodes.map((node) => ({
                  value: node.id,
                  label: `第 ${node.idx} 个节点（${node.node_role}）`,
                }))}
                onChange={(value) => setNodeId(typeof value === 'number' ? value : undefined)}
              />
            </Space>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              AI 只给修改建议、不会自动写库；采纳后请在节点里手动改并点保存。
            </Typography.Text>
          </Space>

          <div className="ai-thread">
            {thread.length === 0 && (
              <div className="ai-thread-empty">
                可以这样问：「第 2 个节点的选项区分度太低，怎么改？」
                <br />
                AI 会结合该节点的背景、三个选项与财务数据给出具体修改建议。
              </div>
            )}
            {thread.map((item, index) => (
              <div
                // 同一轮问答可能重复内容，用下标参与 key
                key={`${item.role}-${index}`}
                className={`ai-bubble ai-bubble-${item.role}`}
              >
                {item.content}
              </div>
            ))}
            {sending && (
              <div className="ai-bubble ai-bubble-assistant">
                <Spin size="small" /> 正在思考…
              </div>
            )}
          </div>

          <div className="ai-compose">
            <TextArea
              rows={3}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="例如：这个节点的选项区分度太低，怎么改？"
            />
            <Button
              type="primary"
              icon={<SendOutlined />}
              loading={sending}
              onClick={() => void handleSend()}
            >
              发送
            </Button>
          </div>
        </div>
      </Drawer>
    </>
  )
}
