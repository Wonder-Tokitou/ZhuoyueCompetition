import { useState } from 'react'
import { Alert, Button, Card, Form, Input, Segmented, Typography } from 'antd'
import { useLocation, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { clearStudentLogin } from '../../context/StudentAccountContext'

export default function StudentLoginPage() {
  const nav = useNavigate(), location = useLocation()
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const [mode, setMode] = useState<'login' | 'register'>('login')
  async function login(values: { username: string; password: string; real_name?: string }) {
    setBusy(true); setError('')
    try {
      const { data } = await api.post('/student/' + mode, values)
      clearStudentLogin()
      sessionStorage.setItem('student_access_token', data.access_token)
      const next = new URLSearchParams(location.search).get('next') || ''
      nav(/^\/student(?:\/|\?|$)/.test(next) && !next.includes('\\') && !next.startsWith('/student/login') ? next : '/student', { replace: true })
    } catch (e) { setError(e instanceof Error ? e.message : '登录失败') }
    finally { setBusy(false) }
  }
  return <Card title="学生入口" style={{ maxWidth: 440, margin: '60px auto' }}>
    <Segmented value={mode} options={[{ label: '登录', value: 'login' }, { label: '首次使用 · 注册', value: 'register' }]} onChange={(value) => { setMode(value as 'login' | 'register'); setError('') }} />
    <Typography.Paragraph style={{ marginTop: 16 }}>首次使用请自行注册并填写姓名；信息会自动显示在教师端，无需等待教师创建账号。</Typography.Paragraph>
    {error && <Alert message={error} type="error" />}
    <Form key={mode} layout="vertical" onFinish={login}>
      {mode === 'register' && <Form.Item name="real_name" label="姓名" rules={[{ required: true, whitespace: true, max: 64 }]}><Input autoComplete="name" placeholder="教师查看作答时显示的姓名" /></Form.Item>}
      <Form.Item name="username" label="账号" rules={[{ required: true, pattern: /^[A-Za-z0-9_.-]{1,64}$/, message: '使用英文、数字、点、短横线或下划线' }]}><Input autoComplete="username" maxLength={64} /></Form.Item>
      <Form.Item name="password" label="密码" rules={[{ required: true, min: mode === 'register' ? 8 : 1 }]}><Input.Password autoComplete={mode === 'register' ? 'new-password' : 'current-password'} maxLength={128} /></Form.Item>
      <Button htmlType="submit" type="primary" block loading={busy}>{mode === 'register' ? '注册并进入个人空间' : '登录个人空间'}</Button>
    </Form>
  </Card>
}
