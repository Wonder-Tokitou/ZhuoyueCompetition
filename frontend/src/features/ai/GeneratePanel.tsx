import './ai.css'
import { useState } from 'react'
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Input,
  Select,
  Space,
  Spin,
  Tabs,
  Typography,
  Upload,
} from 'antd'
import type { UploadFile } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import type { CaseType, CreateCasePayload } from '../../api/types'
import { CASE_TYPE_OPTIONS, FRAMEWORK_BY_CASE_TYPE } from '../../domain/caseConstants'

const { Dragger } = Upload
const { TextArea } = Input

interface GeneratePanelProps {
  generating: boolean
  statusText: string
  errors: string[]
  onGenerate: (payload: CreateCasePayload) => Promise<boolean>
}

/**
 * 上传区：素材文件 / 主题关键词两条入口，都走 POST /api/cases。
 * 生成期间展示 Spin 与「AI 正在拆解案例」。
 */
export default function GeneratePanel({
  generating,
  statusText,
  errors,
  onGenerate,
}: GeneratePanelProps) {
  const { message } = AntdApp.useApp()
  const [title, setTitle] = useState('')
  const [caseType, setCaseType] = useState<CaseType>('战略决策类')
  const [fileList, setFileList] = useState<UploadFile[]>([])
  const [topic, setTopic] = useState('')

  async function submit(payload: CreateCasePayload) {
    if (await onGenerate(payload)) {
      setFileList([])
      setTopic('')
    }
  }

  async function handleFileGenerate() {
    const file = fileList[0]?.originFileObj as File | undefined
    if (!title.trim()) {
      message.warning('请先填写案例标题')
      return
    }
    if (!file) {
      message.warning('请选择素材文件，或切到「主题关键词」页签直接粘贴文本')
      return
    }
    await submit({ title: title.trim(), case_type: caseType, file })
  }

  async function handleTopicGenerate() {
    const value = topic.trim()
    const finalTitle = title.trim() || value.slice(0, 30)
    if (value.length < 2) {
      message.warning('请输入主题关键词，或至少 200 字的素材文本')
      return
    }
    if (value.length >= 30 && value.length < 200) {
      message.warning('30–199 字既不是主题关键词也不算有效素材，请精简到 30 字以内或补足到 200 字以上')
      return
    }
    await submit({ title: finalTitle, case_type: caseType, text: value })
  }

  const sourceTabs = [
    {
      key: 'file',
      label: '上传素材文件',
      children: (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Dragger
          accept=".txt,.md,.pdf,.docx"
            maxCount={1}
            beforeUpload={() => false}
            fileList={fileList}
            onChange={({ fileList: next }) => setFileList(next.slice(-1))}
            onRemove={() => setFileList([])}
            disabled={generating}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽案例素材到此处</p>
            <p className="ant-upload-hint">
              支持 .txt / .md / .docx / .pdf，纯文本不少于 200 字；扫描版 PDF 无文字层会提示改用文本粘贴
            </p>
          </Dragger>
          <Button type="primary" loading={generating} onClick={() => void handleFileGenerate()}>
            生成案例
          </Button>
        </Space>
      ),
    },
    {
      key: 'topic',
      label: '仅输入主题关键词',
      children: (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <TextArea
            rows={4}
            value={topic}
            disabled={generating}
            onChange={(event) => setTopic(event.target.value)}
            placeholder="例如：校园咖啡店定价决策（30 字以内走主题关键词模式；也可以直接粘贴 200 字以上的素材文本）"
          />
          <Typography.Text type="secondary">
            少于 30 字按主题关键词处理，基准数据由模型按行业区间补齐；30 字以上按素材处理，需不少于 200
            字。此页签同样走 POST /api/cases 的 text 字段。
          </Typography.Text>
          <Button type="primary" loading={generating} onClick={() => void handleTopicGenerate()}>
            生成案例
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <Card title="① 生成案例">
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Space size={12} wrap>
          <Input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="案例标题，例如：研咖咖啡·高校市场扩张与定价决策"
            style={{ width: 420 }}
            maxLength={200}
            disabled={generating}
          />
          <Select
            value={caseType}
            onChange={(value: CaseType) => setCaseType(value)}
            options={CASE_TYPE_OPTIONS}
            style={{ width: 180 }}
            disabled={generating}
          />
          <Typography.Text type="secondary">
            将按《{FRAMEWORK_BY_CASE_TYPE[caseType]}》框架生成
          </Typography.Text>
        </Space>

        <Tabs items={sourceTabs} />

        {generating && (
          <div className="gen-loading">
            <Spin />
            <span className="gen-loading-text">{statusText || 'AI 正在拆解案例'}</span>
          </div>
        )}

        {errors.length > 0 && (
          <Alert
            type="error"
            showIcon
            message="生成未通过，可修改素材后重试"
            description={
              <ul style={{ margin: 0, paddingInlineStart: 20 }}>
                {errors.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            }
          />
        )}
      </Space>
    </Card>
  )
}
