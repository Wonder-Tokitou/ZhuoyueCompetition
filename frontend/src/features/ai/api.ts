import { api } from '../../api/transport'
import type { AiTaskResponse, AiFixPayload, AiFixResponse, ReviewResponse, PatchReviewPayload, TryCasePayload, TryCaseResponse, OkResponse } from '../../api/types'

export async function getAiTask(taskId: string, token: string): Promise<AiTaskResponse> {
  const { data } = await api.get<AiTaskResponse>(`/ai-tasks/${taskId}`, { params: { token } })
  return data
}


export async function listCaseAiTasks(caseId: number, token: string): Promise<AiTaskResponse[]> {
  const { data } = await api.get<AiTaskResponse[]>(`/cases/${caseId}/ai-tasks`, { params: { token } })
  return data
}


export async function aiFix(
  caseId: number,
  token: string,
  payload: AiFixPayload,
): Promise<AiFixResponse> {
  const { data } = await api.post<AiFixResponse>(
    `/cases/${caseId}/ai-fix`,
    payload,
    { params: { token }, timeout: 180000 },
  )
  return data
}


export async function retryStudentReview(token: string, session: number): Promise<void> {
  await api.post(`/play/${encodeURIComponent(token)}/review/retry`, null, { params: { session_id: session } })
}


export async function review(studentToken: string, sessionId: number, signal?: AbortSignal): Promise<ReviewResponse> {
  const { data } = await api.get<ReviewResponse>(`/play/${studentToken}/review`, {
    params: { session_id: sessionId },
    signal,
    timeout: 15000,
  })
  return data
}


export async function chatStudent(
  studentToken: string,
  sessionId: number,
  message: string,
  onDelta: (text: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/play/${encodeURIComponent(studentToken)}/chat`, {
    method: 'POST',
    signal,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${sessionStorage.getItem('student_access_token') || ''}` },
    body: JSON.stringify({ session_id: sessionId, message }),
  })
  if (!response.ok) {
    let detail = `答疑请求失败（HTTP ${response.status}）`
    try {
      const body = await response.json() as { detail?: string }
      detail = body.detail || detail
    } catch { /* 保留状态码提示 */ }
    throw new Error(detail)
  }
  if (!response.body) throw new Error('答疑服务未返回流')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const events = buffer.split('\n\n')
    buffer = events.pop() || ''
    for (const event of events) {
      const line = event.split('\n').find((item) => item.startsWith('data: '))
      if (!line) continue
      const payload = JSON.parse(line.slice(6)) as { delta?: string; error?: string }
      if (payload.error) throw new Error(payload.error)
      if (payload.delta) onDelta(payload.delta)
    }
    if (done) break
  }
}


export async function patchReview(reviewId: number, token: string, payload: PatchReviewPayload): Promise<OkResponse> {
  const { data } = await api.patch<OkResponse>(`/reviews/${reviewId}`, payload, { params: { token } })
  return data
}


export async function tryCase(
  studentToken: string,
  payload: TryCasePayload,
): Promise<TryCaseResponse> {
  const { data } = await api.post<TryCaseResponse>(
    `/play/${studentToken}/try-case`,
    payload,
  )
  return data
}
