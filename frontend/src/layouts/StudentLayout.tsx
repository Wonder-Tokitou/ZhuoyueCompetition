import { useEffect, useState } from 'react'
import { Alert, Button, Layout, Spin } from 'antd'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import './StudentLayout.css'
import { api } from '../api/client'
import { clearStudentLogin, StudentAccountContext } from '../context/StudentAccountContext'
import type { StudentAccount } from '../context/StudentAccountContext'
import StudentTutorSidebar from '../features/ai/StudentTutorSidebar'

const { Content } = Layout

/**
 * 学生端布局（移动优先）：无侧边栏，顶部只显示案例标题。
 * body 限宽 480px 并居中，宽度 768px 以下不出现横向滚动。
 */
export default function StudentLayout() {
  const location = useLocation(), navigate = useNavigate()
  const routeMatch = location.pathname.match(/^\/student\/([^/]+)(?:\/(review))?\/?$/)
  const isCaseRoute = routeMatch && !['cases', 'records', 'profile'].includes(routeMatch[1])
  const previousPath = isCaseRoute
    ? routeMatch[2] === 'review' ? `/student/${routeMatch[1]}` : '/student/cases'
    : location.pathname !== '/student' ? '/student' : null
  const [title, setTitle] = useState('案例')
  const [account, setAccount] = useState<StudentAccount | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let cancelled = false
    if (!sessionStorage.getItem('student_access_token')) {
      navigate('/student/login?next=' + encodeURIComponent(location.pathname + location.search), { replace: true })
      return
    }
    api.get<StudentAccount>('/student/me').then(({ data }) => { if (!cancelled) setAccount(data) })
      .catch((e) => { if (!cancelled) setError(e.message) })
    return () => { cancelled = true }
  }, [navigate])
  useEffect(() => {
    const token = window.location.pathname.split('/')[2]
    setTitle(token ? (sessionStorage.getItem('student_title:' + token) || '案例') : '学生端')
  }, [location.pathname])
  // body 的限宽只在学生端路由挂载期间生效，卸载立刻移除，
  // 避免影响老师端 1000px 的内容区。
  useEffect(() => {
    document.body.classList.add('student-body')
    return () => document.body.classList.remove('student-body')
  }, [])

  if (error) return <Alert type="error" message={error} />
  if (!account) return <Spin />
  return (
    <StudentAccountContext.Provider value={account}>
    <Layout className="student-shell">
      <header className="student-header">
        {previousPath && <Button type="link" onClick={() => navigate(previousPath)}>返回上一级</Button>}
        {location.pathname !== '/student' && <Button type="link" onClick={() => navigate('/student')}>学生首页</Button>}
        {/* 占位标题，接入 S1 后替换为响应里的 case_title */}
        <h1 className="student-title">{title}</h1>
        <span>{account.real_name}</span>
        <Button onClick={async () => { try { await api.post('/student/logout') } finally { clearStudentLogin(); navigate('/student/login', { replace: true }) } }}>退出</Button>
      </header>

      <Content className="student-main">
        <div className="student-page"><Outlet /></div>
        <StudentTutorSidebar />
      </Content>
    </Layout>
    </StudentAccountContext.Provider>
  )
}
