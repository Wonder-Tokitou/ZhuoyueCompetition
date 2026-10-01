import { createContext, useContext } from 'react'
import type { ReactNode } from 'react'
import { useParams } from 'react-router-dom'

/**
 * 学生端令牌上下文：只从 URL 的 :studentToken 读取，不做登录、不落 localStorage。
 * 与老师端各自独立，互不共用。
 */
const StudentTokenContext = createContext<string | null>(null)

export function StudentTokenProvider({ children }: { children: ReactNode }) {
  const { studentToken } = useParams<{ studentToken: string }>()

  return (
    <StudentTokenContext.Provider value={studentToken ?? null}>
      {children}
    </StudentTokenContext.Provider>
  )
}

export function useStudentToken(): string {
  const token = useContext(StudentTokenContext)

  if (!token) {
    throw new Error('缺少学生端令牌：请从 /student/{studentToken} 链接进入')
  }

  return token
}

/** 常驻学生端入口使用可空令牌；扫码/案例链接页面仍使用 useStudentToken。 */
export function useOptionalStudentToken(): string | null {
  return useContext(StudentTokenContext)
}
