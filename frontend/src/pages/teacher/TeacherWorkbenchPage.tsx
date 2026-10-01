import { listCaseAiTasks } from '../../features/ai/api'
import TaskStatus, { pendingTask } from '../../features/ai/TaskStatus'
import type { AiTaskResponse } from '../../api/types'
import { useEffect, useRef, useState } from 'react'
import { Alert, App as AntdApp, Button, Card, Popconfirm, Select, Space, Spin, Tag, Typography } from 'antd'
import { createCase, deleteCase, getCase, listCases } from '../../api/client'
import type { CaseListItem, CreateCasePayload, GetCaseResponse, PublishResponse } from '../../api/types'
import { useTeacherToken } from '../../context/TeacherTokenContext'
import AiFixDrawer from '../../features/ai/AiFixDrawer'
import CalibratePanel from './components/CalibratePanel'
import GeneratePanel from '../../features/ai/GeneratePanel'
import PublishPanel from './components/PublishPanel'
import { STATUS_META } from './teacherConstants'
import './teacher.css'

const SELECTION_KEY = 'teacher_selected_case'
export default function TeacherWorkbenchPage() {
  const teacherToken = useTeacherToken()
  const { message } = AntdApp.useApp()
  const [caseList, setCaseList] = useState<CaseListItem[]>([])
  const [listLoading, setListLoading] = useState(true)
  const [caseId, setCaseId] = useState<number | null>(null)
  const [detail, setDetail] = useState<GetCaseResponse | null>(null)
  const [task, setTask] = useState<AiTaskResponse | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [connectionError, setConnectionError] = useState('')
  const [listError, setListError] = useState('')
  const [genErrors, setGenErrors] = useState<string[]>([])
  const [publishResult, setPublishResult] = useState<PublishResponse | null>(null)
  const [refresh, setRefresh] = useState(0), [listRefresh, setListRefresh] = useState(0)
  const [draftRevision, setDraftRevision] = useState(0)
  const saveRead = useRef(0)
  const currentScope = useRef({ caseId, teacherToken })
  currentScope.current = { caseId, teacherToken }
  const mounted = useRef(false), uploadLock = useRef(false)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])

  useEffect(() => {
    let active = true, timer: number | undefined
    async function pollList() {
      let retry = true
      try {
        const rows = await listCases(teacherToken)
        if (!active) return
        setCaseList(rows)
        setListError('')
        setCaseId((current) => {
          if (current !== null) return current
          let saved = 0
          try { saved = Number(sessionStorage.getItem(SELECTION_KEY)) } catch { /* optional */ }
          return rows.find((row) => row.id === saved)?.id ?? rows.find((row) => row.status === 'generating')?.id ?? rows[0]?.id ?? null
        })
      } catch (e) {
        if (!active) return
        const status = (e as { status?: number }).status
        retry = status !== 401 && status !== 403
        setListError('案例列表暂不可用；已受理的任务仍由后台执行。')
      } finally {
        if (active) { setListLoading(false); if (retry) timer = window.setTimeout(() => void pollList(), 5000) }
      }
    }
    void pollList()
    return () => { active = false; window.clearTimeout(timer) }
  }, [teacherToken, listRefresh])

  useEffect(() => {
    let active = true, timer: number | undefined, failures = 0
    setDetail(null); setTask(null); setPublishResult(null); setConnectionError('')
    if (caseId === null) return
    try { sessionStorage.setItem(SELECTION_KEY, String(caseId)) } catch { /* optional */ }
    async function poll() {
      let again = false
      try {
        const [response, tasks] = await Promise.all([getCase(caseId!, teacherToken), listCaseAiTasks(caseId!, teacherToken)])
        if (!active) return
        const latest = tasks[0] ?? null
        setDetail(response); setDraftRevision(n => n + 1); setTask(latest); setConnectionError(''); failures = 0
        setCaseList((rows) => rows.map((row) => row.id === response.case.id ? { ...row, status: response.case.status } : row))
        again = response.case.status === 'generating' || pendingTask(latest)
      } catch (e) {
        if (!active) return
        failures++
        const status = (e as { status?: number }).status
        again = ![401, 403, 404].includes(status ?? 0)
        setConnectionError(again ? '进度连接暂时中断，正在自动重连；不会取消后台任务。' : '无法读取案例，请重新登录或选择其他案例。')
      } finally {
        if (active && again) timer = window.setTimeout(() => void poll(), Math.min(15000, 2000 * (1 + failures)))
      }
    }
    void poll()
    return () => { active = false; window.clearTimeout(timer) }
  }, [caseId, teacherToken, refresh])

  // Saving is a metadata synchronization, not a navigation/generation refresh.
  // Keep the editor mounted and leave all unsaved drafts untouched.
  async function syncAfterSave() {
    if (caseId === null) return
    const request = ++saveRead.current
    try {
      const response = await getCase(caseId, teacherToken)
      if (!mounted.current || request !== saveRead.current || currentScope.current.caseId !== caseId || currentScope.current.teacherToken !== teacherToken) return
      setDetail(response)
      setPublishResult(null)
      setCaseList(rows => rows.map(row => row.id === response.case.id ? { ...row, title: response.case.title, status: response.case.status } : row))
    } catch {
      if (mounted.current && currentScope.current.caseId === caseId && currentScope.current.teacherToken === teacherToken) message.warning('保存已完成，但最新状态同步失败；当前编辑内容已保留。')
    }
  }

  async function handleGenerate(payload: CreateCasePayload): Promise<boolean> {
    if (uploadLock.current) return false
    uploadLock.current = true; setSubmitting(true); setGenErrors([])
    try {
      const created = await createCase(payload)
      try { sessionStorage.setItem(SELECTION_KEY, String(created.case_id)) } catch { /* optional */ }
      if (mounted.current) {
        setCaseId(created.case_id); setRefresh((n) => n + 1); setListRefresh((n) => n + 1)
        if (created.warning) setGenErrors(created.errors ?? [created.warning])
        else message.success('素材已受理，任务已保存到后台；可以切换页面，稍后回来查看。')
      }
      return true
    } catch {
      if (mounted.current) setGenErrors(['上传或受理未确认，请刷新案例列表确认是否已创建，再决定是否重试。'])
      return false
    } finally {
      uploadLock.current = false
      if (mounted.current) setSubmitting(false)
    }
  }

  async function handleDeleteSelectedCase() {
    if (caseId === null) return
    const deletingId = caseId
    try {
      await deleteCase(deletingId, teacherToken)
      try { sessionStorage.removeItem(SELECTION_KEY) } catch { /* optional */ }
      setCaseId(null)
      setDetail(null)
      setCaseList((rows) => rows.filter((row) => row.id !== deletingId))
      setListRefresh((n) => n + 1)
      message.success('案例及其关联记录已删除。')
    } catch (e) {
      const detail = (e as { detail?: string }).detail
      message.error(detail || '删除失败；若案例仍有后台任务，请等待任务结束后重试。')
    }
  }

  const selected = detail?.case.id === caseId ? detail : null
  const selectedTask = selected ? task : null
  const busy = selected?.case.status === 'generating' || pendingTask(selectedTask)
  const statusMeta = selected ? STATUS_META[selected.case.status] : null
  const running = caseList.filter((row) => row.status === 'generating')
  return <div className="teacher-workspace">
    <div className="teacher-page-head"><div><Typography.Title level={3} style={{ margin: 0 }}>教师工作台</Typography.Title><Typography.Text type="secondary">上传素材 → 后台生成 → 教师校准 → 发布。任务受理后，切换或关闭页面不会取消任务。</Typography.Text></div>
      <Space wrap>{statusMeta && <Tag color={statusMeta.color}>{statusMeta.text}</Tag>}<Select placeholder="选择已有案例" style={{ minWidth: 280 }} loading={listLoading} value={caseId ?? undefined} options={caseList.map((row) => ({ value: row.id, label: `#${row.id} ${row.title}（${STATUS_META[row.status].text}）` }))} onChange={(id: number) => { setCaseId(id); setGenErrors([]) }} /><Popconfirm title="确定删除当前案例？" description="案例、学生提交记录及关联 AI 产物将一并删除，且无法撤销。" okText="删除" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={() => void handleDeleteSelectedCase()} disabled={caseId === null || !!busy}><Button danger disabled={caseId === null || !!busy}>删除案例</Button></Popconfirm><Button onClick={() => { setRefresh((n) => n + 1); setListRefresh((n) => n + 1) }}>刷新进度</Button></Space>
    </div>
    {running.length > 0 && <Card size="small" title="后台生成任务"><Space wrap>{running.map((row) => <Button key={row.id} onClick={() => setCaseId(row.id)}>查看 #{row.id} · {row.title}</Button>)}</Space></Card>}
    <GeneratePanel generating={submitting} statusText="正在上传并保存任务，请等待受理确认" errors={genErrors} onGenerate={handleGenerate} />
    {listError && <Alert showIcon type="warning" message={listError} />}
    {connectionError && <Alert showIcon type="warning" message={connectionError} />}
    {selectedTask && <TaskStatus task={selectedTask} busy={!!busy} />}
    {busy ? <Card><Space><Spin /><Typography.Text>正在后台生成与校验，通过后才进入教师校准。</Typography.Text></Space></Card> : selected ? <><CalibratePanel key={selected.case.id} teacherToken={teacherToken} detail={selected} draftRevision={draftRevision} onSaveRefresh={syncAfterSave} onRefresh={async () => { setRefresh((n) => n + 1) }} /><PublishPanel teacherToken={teacherToken} detail={selected} publishResult={publishResult} onPublished={setPublishResult} /><AiFixDrawer teacherToken={teacherToken} caseId={caseId} nodes={selected.nodes} /></> : <Card><Typography.Text type="secondary">请上传素材生成案例，或选择已有案例。</Typography.Text></Card>}
  </div>
}
