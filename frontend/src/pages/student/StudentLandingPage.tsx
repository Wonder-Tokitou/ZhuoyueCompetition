import { useEffect, useState } from 'react'
import { Alert, Button, Card, Input, Space, Typography } from 'antd'
import { useNavigate } from 'react-router-dom'
import StudentScanner from '../../components/StudentScanner'
import { useStudentAccount } from '../../context/StudentAccountContext'
import { StudentCasesPage } from './StudentSpacePages'

function toStudentPath(value: string): string | null {
  const input = value.trim()
  if (!input) return null
  try {
    const url = new URL(input)
    const match = url.pathname.match(/^\/student\/([A-Za-z0-9_-]+)\/?$/)
    return match ? `/student/${match[1]}` : null
  } catch {
    const token = input.replace(/^\/student\//, '')
    return /^[A-Za-z0-9_-]+$/.test(token) ? `/student/${token}` : null
  }
}

/** 长期可访问的学生端入口；扫码链接仍会落到同一套学生端布局。 */
export default function StudentLandingPage() {
  const navigate = useNavigate()
  const account = useStudentAccount()
  const [value, setValue] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    const queryToken = new URLSearchParams(window.location.search).get('case')
    if (queryToken?.trim()) navigate(`/student/${encodeURIComponent(queryToken.trim())}`, { replace: true })
  }, [navigate])

  function enter() {
    const path = toStudentPath(value)
    if (!path) {
      setError('请输入教师发放的学生端链接或案例令牌')
      return
    }
    setError('')
    navigate(path)
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card title={`${account.real_name}的学习空间`}>
        <Space wrap><Button type="primary" onClick={() => navigate('/student/cases')}>案例推演</Button><Button onClick={() => navigate('/student/records')}>我的提交记录</Button><Button onClick={() => navigate('/student/profile')}>个人与账号信息</Button></Space>
      </Card>
      <StudentCasesPage />
      <Card>
        <details>
        <summary style={{ cursor: 'pointer' }}>其他进入方式：案例令牌、链接或二维码</summary>
        <Typography.Paragraph type="secondary">
          上方列表可直接进入推演，无需输入令牌。教师单独发放链接或二维码时，也可以在这里使用。
        </Typography.Paragraph>
        <Input
          size="large"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onPressEnter={enter}
          placeholder="例如 /student/案例令牌"
          aria-label="学生端案例链接或令牌"
        />
        <Button type="primary" size="large" block style={{ marginTop: 12 }} onClick={enter}>
          进入案例
        </Button>
        {error && <Alert type="warning" showIcon message={error} style={{ marginTop: 12 }} />}
        <div style={{ marginTop: 16 }}><StudentScanner onScan={(value) => { const path = toStudentPath(value); if (path) navigate(path); else setError('二维码不是有效的学生案例链接') }} /></div>
        </details>
      </Card>
      <Typography.Text type="secondary" className="student-hint">
        作答关联当前实名账号，仅你本人和教师可以查看。扫码链接用于选择案例，不代替账号登录。
      </Typography.Text>
    </Space>
  )
}
