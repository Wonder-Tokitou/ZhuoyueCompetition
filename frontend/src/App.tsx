import { Route, Routes } from 'react-router-dom'
import { StudentTokenProvider } from './context/StudentTokenContext'
import { TeacherTokenProvider } from './context/TeacherTokenContext'
import StudentLayout from './layouts/StudentLayout'
import TeacherLayout from './layouts/TeacherLayout'
import EntryHintPage from './pages/EntryHintPage'
import StudentPlayPage from './pages/student/StudentPlayPage'
import StudentReviewPage from './pages/student/StudentReviewPage'
import StudentLandingPage from './pages/student/StudentLandingPage'
import TeacherRecordsPage from './pages/teacher/TeacherRecordsPage'
import TeacherWorkbenchPage from './pages/teacher/TeacherWorkbenchPage'
import TeacherLoginPage from './pages/teacher/TeacherLoginPage'
import StudentLoginPage from './pages/student/StudentLoginPage'
import { StudentCasesPage, StudentRecordsPage, StudentProfilePage } from './pages/student/StudentSpacePages'
import TeacherStudentsPage from './pages/teacher/TeacherStudentsPage'

/**
 * 四条业务路由 + 一个入口提示页。
 * 老师端与学生端各自挂在独立 Layout 下，不共用任何布局组件。
 */
export default function App() {
  return (
    <Routes>
      <Route path="/" element={<EntryHintPage />} />
      <Route path="/teacher/login" element={<TeacherLoginPage />} />
      <Route path="/student/login" element={<StudentLoginPage />} />

      {/* 老师端（桌面优先） */}
      <Route
        path="/teacher/:teacherToken?"
        element={
          <TeacherTokenProvider>
            <TeacherLayout />
          </TeacherTokenProvider>
        }
      >
        <Route index element={<TeacherWorkbenchPage />} />
        <Route path="records" element={<TeacherRecordsPage />} />
        <Route path="students" element={<TeacherStudentsPage />} />
      </Route>

      {/* 学生端（移动优先） */}
      <Route
        path="/student"
        element={
          <StudentTokenProvider>
            <StudentLayout />
          </StudentTokenProvider>
        }
      >
        <Route index element={<StudentLandingPage />} />
        <Route path="cases" element={<StudentCasesPage />} />
        <Route path="records" element={<StudentRecordsPage />} />
        <Route path="profile" element={<StudentProfilePage />} />
      </Route>
      <Route
        path="/student/:studentToken"
        element={
          <StudentTokenProvider>
            <StudentLayout />
          </StudentTokenProvider>
        }
      >
        <Route index element={<StudentPlayPage />} />
        <Route path="review" element={<StudentReviewPage />} />
      </Route>

      <Route path="*" element={<EntryHintPage />} />
    </Routes>
  )
}
