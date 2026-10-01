import { createContext, useContext } from 'react'
import type { ReactNode } from 'react'
import { useParams } from 'react-router-dom'
import { Navigate } from 'react-router-dom'

/**
 * 老师端令牌上下文：只从 URL 的 :teacherToken 读取，不做登录、不落 localStorage。
 * 与学生端各自独立，互不共用。
 */
const TeacherTokenContext = createContext<string | null>(null)

export function TeacherTokenProvider({ children }: { children: ReactNode }) {
  const { teacherToken: routeToken } = useParams<{ teacherToken: string }>()
  let storedToken: string | null = null
  if (typeof window !== 'undefined') {
    try {
      storedToken = window.sessionStorage.getItem('teacher_token')
    } catch {
      // 浏览器禁用存储时按未登录处理，不让异常打穿整个页面。
      storedToken = null
    }
  }
  const teacherToken = routeToken || storedToken

  if (!teacherToken) {
    return <Navigate to="/teacher/login" replace />
  }

  return (
    <TeacherTokenContext.Provider value={teacherToken}>
      {children}
    </TeacherTokenContext.Provider>
  )
}

export function useTeacherToken(): string {
  const token = useContext(TeacherTokenContext)

  if (!token) {
    throw new Error('缺少教师端令牌：请从 /teacher/{teacherToken} 链接进入')
  }

  return token
}
