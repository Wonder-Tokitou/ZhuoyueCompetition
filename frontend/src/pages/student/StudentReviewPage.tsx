import { useLocation, useNavigate } from 'react-router-dom'
import { useStudentToken } from '../../context/StudentTokenContext'
import StudentReview from '../../features/ai/StudentReview'

export default function StudentReviewPage() {
  const token = useStudentToken()
  const location = useLocation()
  const navigate = useNavigate()
  const sessionId = Number(new URLSearchParams(location.search).get('session') || sessionStorage.getItem(`student_session:${token}`))
  return <StudentReview key={token + ':' + sessionId} token={token} sid={sessionId}
    onBack={() => navigate(`/student/${token}`)} onRestart={() => navigate(`/student/${token}`)} />
}
