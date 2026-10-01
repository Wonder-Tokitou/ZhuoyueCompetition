import { Button, Card, Space, Typography } from 'antd'
import { useNavigate } from 'react-router-dom'

/**
 * 师生登录入口；扫码链接仅标识案例，不代替账号身份。
 * 落在 / 或未知路径时不白屏，给出明确指引。
 */
export default function EntryHintPage() {
  const navigate = useNavigate()
  return (
    <div className="entry-hint">
      <Card style={{ maxWidth: 560 }}>
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          AI 商科案例推演平台
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
          学生自行注册并填写姓名，登录后选择教师发布的案例，也可以扫描案例二维码。作答保存后，教师可直接查看。
        </Typography.Paragraph>
        <Space direction="vertical" size={4}>
          <Button type="primary" onClick={() => navigate('/student')}>学生个人空间</Button>
          <Button onClick={() => navigate('/teacher/login')}>教师登录</Button>
          <Typography.Text code>/teacher/login · 教师入口</Typography.Text>
          <Typography.Text code>/student · 学生端常驻入口</Typography.Text>
          <Typography.Text code>/student/{'{studentToken}'} · 扫码直达案例</Typography.Text>
        </Space>
      </Card>
    </div>
  )
}
