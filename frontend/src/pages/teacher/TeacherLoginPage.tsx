import { useState } from 'react'
import { Button, Card, Form, Input, Typography } from 'antd'
import { useNavigate } from 'react-router-dom'
import { loginTeacher } from '../../api/client'

export default function TeacherLoginPage() {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  async function submit(values: { password: string }) {
    setLoading(true); setError('')
    try {
      const result = await loginTeacher(values.password)
      sessionStorage.setItem('teacher_token', result.teacher_token)
      navigate('/teacher', { replace: true })
    } catch (e) { setError(e instanceof Error ? e.message : '登录失败') }
    finally { setLoading(false) }
  }
  return <Card title="教师登录" style={{ maxWidth: 420, margin: '80px auto' }}>
    <Form onFinish={submit} layout="vertical">
      <Form.Item label="访问密码" name="password" rules={[{ required: true, message: '请输入密码' }]}>
        <Input.Password autoFocus />
      </Form.Item>
      {error && <Typography.Text type="danger">{error}</Typography.Text>}
      <Button type="primary" htmlType="submit" loading={loading} block>登录</Button>
    </Form>
  </Card>
}
