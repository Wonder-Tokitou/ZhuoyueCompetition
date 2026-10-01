import { api } from './transport'
export { api } from './transport'
export type { ApiError } from './transport'
import type {
  CaseListItem,
  CreateCasePayload,
  CreateCaseResponse,
  DecidePayload,
  DecideResponse,
  GetCaseResponse,
  GenerateNodesPayload,
  AiTaskResponse,
  LoginResponse,
  OkResponse,
  PatchCasePayload,
  PatchNodePayload,
  PlayResponse,
  PublishResponse,
  StudentRecord,
  SessionState,
} from './types'

/* ---------- 老师端（T1–T9） ---------- */

/** T1 教师登录 */
export async function loginTeacher(password: string): Promise<LoginResponse> {
  const { data } = await api.post<LoginResponse>('/teacher/login', { password })
  return data
}

/** 教师登出：服务端吊销令牌（数据库 + 缓存双清），幂等 */
export async function logoutTeacher(token: string): Promise<OkResponse> {
  const { data } = await api.post<OkResponse>('/teacher/logout', undefined, { params: { token } })
  return data
}

/** T2 创建案例（multipart：file 与 text 必须提供其一；case_type 必填；本接口不需要 token） */
export async function createCase(payload: CreateCasePayload): Promise<CreateCaseResponse> {
  const form = new FormData()
  form.append('title', payload.title)
  form.append('case_type', payload.case_type)
  if (payload.file) {
    form.append('file', payload.file)
  }
  if (payload.text) {
    form.append('text', payload.text)
  }
  if (payload.framework_id !== undefined) {
    form.append('framework_id', String(payload.framework_id))
  }

  // 这里只等待上传/解析与任务入库；模型在后台执行，不随请求结束取消。
  const { data } = await api.post<CreateCaseResponse>('/cases', form, { timeout: 300000 })
  return data
}

/** 持久化 AI 任务状态；教师工作台通常通过案例详情轮询，这个接口供诊断页使用。 */


/** T3 案例列表（响应是裸数组） */
export async function listCases(token: string): Promise<CaseListItem[]> {
  const { data } = await api.get<CaseListItem[]>('/cases', { params: { token } })
  return data
}

/** T4 案例详情（教师端节点的选项带风险等级与后果，供校准） */
export async function getCase(caseId: number, token: string): Promise<GetCaseResponse> {
  const { data } = await api.get<GetCaseResponse>(`/cases/${caseId}`, { params: { token } })
  return data
}

/** T5 修改案例级信息（标题 / 基准数据） */
export async function patchCase(
  caseId: number,
  token: string,
  payload: PatchCasePayload,
): Promise<OkResponse> {
  const { data } = await api.patch<OkResponse>(
    `/cases/${caseId}`,
    payload,
    { params: { token } },
  )
  return data
}

/** T6 修改节点级信息（情境 / 选项） */
export async function patchNode(
  caseId: number,
  nodeId: number,
  token: string,
  payload: PatchNodePayload,
): Promise<OkResponse> {
  const { data } = await api.patch<OkResponse>(
    `/cases/${caseId}/nodes/${nodeId}`,
    payload,
    { params: { token } },
  )
  return data
}

export async function generateCaseNodes(caseId: number, token: string, payload: GenerateNodesPayload): Promise<AiTaskResponse> {
  return (await api.post<AiTaskResponse>(`/cases/${caseId}/generate-nodes`, payload, { params: { token } })).data
}

export async function deleteCase(caseId: number, token: string) {
  return (await api.delete(`/cases/${caseId}`, { params: { token } })).data
}

export async function deleteStudentSubmission(token: string, sessionId: number) {
  return (await api.delete(`/play/${encodeURIComponent(token)}/sessions/${sessionId}`)).data
}

export async function resetStudentSubmission(token: string, sessionId: number) {
  return (await api.post<SessionState>(`/play/${encodeURIComponent(token)}/sessions/${sessionId}/reset`)).data
}

export async function rollbackStudentSubmission(token: string, sessionId: number, nodeIdx: number) {
  return (await api.post<SessionState>(`/play/${encodeURIComponent(token)}/sessions/${sessionId}/rollback/${nodeIdx}`)).data
}

/** T7 请求 AI 修正建议（只出建议，不落库） */

/** T8 发布案例（返回学生端链接与二维码地址） */
export async function publishCase(caseId: number, token: string): Promise<PublishResponse> {
  const { data } = await api.post<PublishResponse>(
    `/cases/${caseId}/publish`,
    undefined,
    { params: { token } },
  )
  return data
}

/** T9 学生推演记录（响应是裸数组，按 session 分组） */
export async function getRecords(caseId: number, token: string): Promise<StudentRecord[]> {
  const { data } = await api.get<StudentRecord[]>(`/cases/${caseId}/records`, {
    params: { token },
  })
  return data
}

/* ---------- 学生端（S1、S2、S4、S5；S3 问答走 SSE 流，另行实现） ---------- */

/** S1 拉取案例标题与全部节点（选项只有 key/label） */
export async function playGet(studentToken: string): Promise<PlayResponse> {
  const { data } = await api.get<PlayResponse>(`/play/${studentToken}`)
  return data
}

export async function startStudent(token: string, name: string): Promise<SessionState> {
  return (await api.post<SessionState>(`/play/${encodeURIComponent(token)}/sessions`, { student_name: name })).data
}

export async function restoreStudent(token: string, session: number): Promise<SessionState> {
  return (await api.get<SessionState>(`/play/${encodeURIComponent(token)}/sessions/${session}`)).data
}


/** S2 提交一次决策 */
export async function decide(
  studentToken: string,
  payload: DecidePayload,
): Promise<DecideResponse> {
  const { data } = await api.post<DecideResponse>(`/play/${studentToken}/decide`, payload)
  return data
}

/** S4 复盘报告 */

/** S3 学生端 AI 助教：读取 SSE，不把完整上下文暴露给前端日志。 */


/** S5 学生自助试算（临时案例，不入案例库） */
