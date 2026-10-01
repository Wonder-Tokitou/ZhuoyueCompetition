import { useEffect, useState } from 'react'
import { Alert, Button, Card, Collapse, Form, Input, Popconfirm, Space, Spin, Typography } from 'antd'
import { useNavigate } from 'react-router-dom'
import { api, deleteStudentSubmission, resetStudentSubmission } from '../../api/client'
import type { StudentSubmission } from '../../api/types'
import { clearStudentLogin, useStudentAccount } from '../../context/StudentAccountContext'

interface PublishedCase { id: number; title: string; case_type: string; student_token: string; version: number; background: string }
type Submission = StudentSubmission

export function StudentCasesPage() {
  const [rows, setRows] = useState<PublishedCase[] | null>(null), [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0), [loading, setLoading] = useState(false)
  const nav = useNavigate()
  useEffect(() => {
    let active = true, timer: number | undefined
    const controller = new AbortController()
    async function load() {
      let retry = true
      setLoading(true)
      try {
        const { data } = await api.get<PublishedCase[]>('/student/cases', { signal: controller.signal })
        if (active) { setRows(data); setError('') }
      } catch (e) {
        const failure = e as { message?: string; status?: number; response?: { status?: number } }
        retry = ![401, 403].includes(failure.status || failure.response?.status || 0)
        if (active) setError(failure.message || '案例列表暂时无法加载，请重试')
      } finally {
        if (active) {
          setLoading(false)
          if (retry) timer = window.setTimeout(load, 10000)
        }
      }
    }
    void load()
    return () => { active = false; controller.abort(); window.clearTimeout(timer) }
  }, [refresh])
  return <Space direction="vertical" style={{ width: '100%' }}>
    <Space wrap><Typography.Title level={3}>案例推演 · 教师已发布案例</Typography.Title><Button loading={loading} onClick={() => setRefresh(v => v + 1)}>刷新案例</Button></Space>
    <Typography.Text type="secondary">选择案例即可开始，无需输入令牌。教师上传、校准并发布后将在这里展示；列表每 10 秒自动更新。</Typography.Text>
    {error && <Alert type="error" message={error} />}
    {!rows && !error && <Spin />}
    {rows?.length === 0 && !error && <Alert message="教师尚未发布案例" description="上传后的案例需由教师校准并点击发布；发布后会自动出现在此处。" />}
    {rows?.map((r) => <Card key={r.id} title={r.title}><p>{r.case_type} · 版本 {r.version}</p><p>{r.background}</p><Button type="primary" onClick={() => nav('/student/' + encodeURIComponent(r.student_token))}>进入推演</Button></Card>)}
  </Space>
}

export function StudentRecordsPage() {
  const [rows, setRows] = useState<Submission[] | null>(null), [error, setError] = useState('')
  const nav = useNavigate()
  const load = () => api.get<Submission[]>('/student/records').then(({ data }) => setRows(data)).catch((e) => setError(e.message))
  useEffect(() => { let active = true; api.get('/student/records').then(({ data }) => { if (active) setRows(data) }).catch((e) => { if (active) setError(e.message) }); return () => { active = false } }, [])
  return <Space direction="vertical" style={{ width: '100%' }}>
    <Typography.Title level={3}>我的提交记录</Typography.Title>
    {error && <Alert type="error" message={error} />}{!rows && !error && <Spin />}
    {rows?.length === 0 && <Alert message="还没有推演记录，去案例推演开始学习吧" />}
    {rows?.map((r) => <Card key={r.session_id} title={`${r.case_title} · 第 ${r.attempt_no} 次`}>
      <p>教师版本 {r.case_version} · {r.created_at.replace('T', ' ')} · 已提交 {r.turn_count}/3</p>
      <Collapse items={[{ key: 'answers', label: '查看作答与已保存复盘', children: <>{(r.turns || []).map((t, i) => <div key={t.node_id}><b>第 {i + 1} 回合</b><p>已选策略 {t.display_option || ''}：{t.chosen_label || '历史记录未提供策略文字'}</p><p>理由：{t.input_text || '未填写'}</p><p>{t.result.summary}</p></div>)}{r.review && <>{r.review.dimensions.map((d) => <p key={d.name}><b>{d.name}：</b>{d.content}</p>)}<p>{r.review.conclusion}</p></>}</> }]} />
      <Space style={{ marginTop: 12 }} wrap><Button disabled={!r.available} onClick={() => {
        sessionStorage.setItem('student_session:' + r.student_token, String(r.session_id))
        nav('/student/' + r.student_token)
      }}>{r.finished_at ? '查看 / 修改推演' : '继续推演'}</Button>
      <Popconfirm title="覆盖这条推演记录？" description="重新选择后将替换教师端当前记录，旧作答和复盘将被清除。" okText="覆盖原记录" cancelText="取消" onConfirm={async () => {
        try { const state = await resetStudentSubmission(r.student_token, r.session_id); sessionStorage.setItem('student_session:' + r.student_token, String(state.session_id)); nav('/student/' + r.student_token) }
        catch (e) { setError(e instanceof Error ? e.message : '覆盖失败') }
      }}><Button disabled={!r.available}>重新推演并覆盖</Button></Popconfirm>
      <Popconfirm title="删除这条推演记录？" description="删除后教师端将不再看到该记录，无法恢复。" okText="删除记录" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={async () => {
        try { await deleteStudentSubmission(r.student_token, r.session_id); await load() }
        catch (e) { setError(e instanceof Error ? e.message : '删除失败') }
      }}><Button danger>删除记录</Button></Popconfirm></Space>
      {!r.available && <p>案例已下架；上述历史作答仍可查看。</p>}
    </Card>)}
  </Space>
}

export function StudentProfilePage() {
  const account = useStudentAccount(), nav = useNavigate()
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  return <Card title="个人与账号信息"><p>实名：{account.real_name}（需修改请联系教师）</p><p>账号：{account.username}</p><p>注册时间：{account.created_at.replace('T', ' ')}</p>
    {error && <Alert type="error" message={error} />}
    <Form layout="vertical" onFinish={async (values) => { setBusy(true); setError(''); try { await api.post('/student/password', values); clearStudentLogin(); nav('/student/login') } catch (e) { setError(e instanceof Error ? e.message : '修改失败') } finally { setBusy(false) } }}>
      <Form.Item name="current_password" label="当前密码" rules={[{ required: true }]}><Input.Password autoComplete="current-password" /></Form.Item>
      <Form.Item name="new_password" label="新密码" rules={[{ required: true, min: 8, max: 128 }]}><Input.Password autoComplete="new-password" /></Form.Item>
      <Button htmlType="submit" loading={busy}>修改密码并重新登录</Button>
    </Form>
  </Card>
}
