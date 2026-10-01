import { createContext, useContext } from 'react'

export interface StudentAccount { id: number; username: string; real_name: string; status: 'active' | 'disabled'; created_at: string }
export const StudentAccountContext = createContext<StudentAccount | null>(null)
export function useStudentAccount() {
  const account = useContext(StudentAccountContext)
  if (!account) throw new Error('请先登录学生账号')
  return account
}
export function clearStudentLogin() {
  for (const key of Object.keys(sessionStorage)) {
    if (key.startsWith('student_')) sessionStorage.removeItem(key)
  }
}
