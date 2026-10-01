import { useEffect, useState } from 'react'
import { Alert, Button, Card, Form, Input, Modal, Space, Table, Tag } from 'antd'
import { api } from '../../api/client'
import { useTeacherToken } from '../../context/TeacherTokenContext'
import type { StudentAccount } from '../../context/StudentAccountContext'

export default function TeacherStudentsPage() {
  const token = useTeacherToken()
  const [rows, setRows] = useState<StudentAccount[]>([]), [error, setError] = useState('')
  const [editing, setEditing] = useState<StudentAccount | null>(null), [busy, setBusy] = useState(false)
  const [form] = Form.useForm(), [editForm] = Form.useForm()
  async function load() { try { setRows((await api.get('/teacher/students', { params: { token } })).data) } catch (e) { setError(e instanceof Error ? e.message : '加载失败') } }
  useEffect(() => { void load() }, [token])
  async function save(id: number, values: object) {
    await api.patch('/teacher/students/' + id, values, { params: { token } }); await load()
  }
  return <Space direction="vertical" style={{ width: '100%' }}>
    {error && <Alert type="error" message={error} closable onClose={() => setError('')} />}
    <Card title="学生账号（学生可自行注册，也可在此添加）">
      <Form form={form} layout="inline" onFinish={async (values) => { setBusy(true); setError(''); try { await api.post('/teacher/students', values, { params: { token } }); form.resetFields(); await load() } catch (e) { setError(e instanceof Error ? e.message : '创建失败') } finally { setBusy(false) } }}>
        <Form.Item name="username" label="账号" rules={[{ required: true, pattern: /^[A-Za-z0-9_.-]{1,64}$/, message: '使用英文、数字、点、短横线或下划线' }]}><Input autoComplete="off" /></Form.Item>
        <Form.Item name="real_name" label="真实姓名" rules={[{ required: true, whitespace: true, max: 64 }]}><Input /></Form.Item>
        <Form.Item name="password" label="初始密码" rules={[{ required: true, min: 8, max: 128 }]}><Input.Password autoComplete="new-password" /></Form.Item>
        <Button htmlType="submit" type="primary" loading={busy}>创建账号</Button>
      </Form>
      <p>将账号与初始密码单独发给学生。密码不在列表展示；停用或重置密码将撤销现有登录。</p>
    </Card>
    <Table rowKey="id" dataSource={rows} columns={[
      { title: '账号', dataIndex: 'username' }, { title: '真实姓名', dataIndex: 'real_name' },
      { title: '状态', render: (_, r) => <Tag color={r.status === 'active' ? 'green' : 'red'}>{r.status === 'active' ? '正常' : '已停用'}</Tag> },
      { title: '管理', render: (_, r) => <Space><Button onClick={() => { setEditing(r); editForm.setFieldsValue({ real_name: r.real_name, password: '' }) }}>修改 / 重置密码</Button><Button danger={r.status === 'active'} onClick={() => Modal.confirm({ title: `${r.status === 'active' ? '停用' : '启用'} ${r.real_name}？`, onOk: async () => { await save(r.id, { status: r.status === 'active' ? 'disabled' : 'active' }) } })}>{r.status === 'active' ? '停用' : '启用'}</Button></Space> },
    ]} />
    <Modal title={`管理 ${editing?.username || ''}`} open={!!editing} onCancel={() => setEditing(null)} onOk={() => editForm.submit()} confirmLoading={busy}>
      <Form form={editForm} layout="vertical" onFinish={async (values) => { if (!editing) return; setBusy(true); try { await save(editing.id, { real_name: values.real_name, ...(values.password ? { password: values.password } : {}) }); setEditing(null) } catch (e) { setError(e instanceof Error ? e.message : '保存失败') } finally { setBusy(false) } }}>
        <Form.Item name="real_name" label="真实姓名" rules={[{ required: true, whitespace: true, max: 64 }]}><Input /></Form.Item>
        <Form.Item name="password" label="重置密码（留空则不变）" rules={[{ min: 8, max: 128 }]}><Input.Password autoComplete="new-password" /></Form.Item>
      </Form>
    </Modal>
  </Space>
}
