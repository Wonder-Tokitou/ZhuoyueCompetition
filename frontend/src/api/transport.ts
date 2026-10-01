import axios from 'axios'
import type { AxiosError, AxiosInstance } from 'axios'
import { showError } from '../utils/feedback'

/** 统一的失败对象：message 即后端返回的中文说明，status 为 HTTP 状态码 */
export interface ApiError extends Error {
  status?: number
}

function toApiError(message: string, status?: number): ApiError {
  const error = new Error(message) as ApiError
  error.name = 'ApiError'
  error.status = status
  return error
}

export const api: AxiosInstance = axios.create({
  baseURL: '/api',
  // 生成类接口（创建案例、AI 修正）要等模型，超时给足；开发期也经 Vite 代理转发
  timeout: 120000,
})

api.interceptors.request.use((config) => {
  if (/^\/(student(?:\/|$)|play\/)/.test(config.url || '')) {
    const token = sessionStorage.getItem('student_access_token')
    if (token) config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 任何失败都在这里统一转成中文提示并抛出，页面 catch 后渲染错误态，绝不白屏。
api.interceptors.response.use(
  (response) => response,
  (error: AxiosError<{ detail?: string }>) => {
    if (axios.isCancel(error)) return Promise.reject(error)
    const status = error.response?.status
    let detail = error.response?.data?.detail
    if (detail && typeof detail !== 'string') {
      detail = '请求参数不符合要求，请检查输入内容'
    }

    if (!detail) {
      if (error.code === 'ECONNABORTED') {
        detail = '请求超时，请稍后重试'
      } else if (!error.response) {
        detail = '无法连接后端服务，请确认本地后端端口已启动'
      } else {
        detail = `请求失败（HTTP ${status}）`
      }
    }

    // Review polling renders its own reconnect notice rather than repeated toast errors.
    if (!error.config?.signal) showError(detail)
    if ((status === 401 || status === 403) && window.location.pathname.startsWith('/student') && !window.location.pathname.startsWith('/student/login')) {
      sessionStorage.removeItem('student_access_token')
      window.location.href = '/student/login?next=' + encodeURIComponent(window.location.pathname + window.location.search)
    }
    if ((status === 401 || status === 403) && typeof window !== 'undefined' && window.location.pathname.startsWith('/teacher')) {
      sessionStorage.removeItem('teacher_token')
      if (!window.location.pathname.startsWith('/teacher/login')) window.location.href = '/teacher/login'
    }
    return Promise.reject(toApiError(detail, status))
  },
)
