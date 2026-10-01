import { AppstoreOutlined, LogoutOutlined, ProfileOutlined } from '@ant-design/icons'
import { Alert, Button, Layout, Menu } from 'antd'
import type { MenuProps } from 'antd'
import { Outlet, useLocation, useNavigate, useParams } from 'react-router-dom'
import { logoutTeacher } from '../api/client'
import { useIsNarrow } from '../hooks/useIsNarrow'
import './TeacherLayout.css'

const { Sider, Content } = Layout

const MENU_ITEMS: MenuProps['items'] = [
  { key: 'workbench', icon: <AppstoreOutlined />, label: '工作台' },
  { key: 'records', icon: <ProfileOutlined />, label: '案例记录' },
  { key: 'students', icon: <ProfileOutlined />, label: '学生管理' },
]

/**
 * 老师端布局（桌面优先）：左侧固定 Menu + 右侧内容区，内容区最小宽度 1000px。
 * 窄于 768px 时顶部出提示条，侧边栏收成图标条，保证记录页在手机上仍可操作。
 */
export default function TeacherLayout() {
  const isNarrow = useIsNarrow(768)
  const { teacherToken = '' } = useParams<{ teacherToken: string }>()
  const navigate = useNavigate()
  const { pathname } = useLocation()

  const basePath = `/teacher/${teacherToken}`
  const activeKey = pathname.replace(/\/+$/, '').endsWith('/students') ? 'students' : pathname.replace(/\/+$/, '').endsWith('/records') ? 'records' : 'workbench'

  const onMenuClick: MenuProps['onClick'] = ({ key }) => {
    navigate(key === 'workbench' ? basePath : `${basePath}/${key}`)
  }

  /** 退出：先请服务端吊销令牌，再清本地副本；网络失败也要完成本地登出。 */
  async function handleLogout() {
    const token = sessionStorage.getItem('teacher_token') || teacherToken
    try {
      if (token) await logoutTeacher(token)
    } catch { /* 服务端不可达时仍允许本地登出 */ }
    finally {
      sessionStorage.removeItem('teacher_token')
      sessionStorage.removeItem('teacher_selected_case')
      navigate('/teacher/login', { replace: true })
    }
  }

  return (
    <Layout className="teacher-shell" hasSider>
      <Sider
        className="teacher-sider"
        theme="light"
        width={200}
        collapsedWidth={64}
        collapsed={isNarrow}
        trigger={null}
      >
        <div className="teacher-brand">{isNarrow ? '教师' : '案例推演 · 教师端'}</div>
        <Menu
          mode="inline"
          selectedKeys={[activeKey]}
          items={MENU_ITEMS}
          onClick={onMenuClick}
        />
        <Button
          className="teacher-logout"
          type="text"
          icon={<LogoutOutlined />}
          block
          onClick={() => void handleLogout()}
        >
          {isNarrow ? null : '退出登录'}
        </Button>
      </Sider>

      <Layout className="teacher-main">
        {isNarrow && (
          <Alert
            className="teacher-narrow-alert"
            type="warning"
            showIcon
            banner
            message="建议在电脑上操作"
            description="当前为手机宽度：侧边栏已收成图标条，案例记录页可左右滑动查看完整表格。"
          />
        )}
        <Content className="teacher-content-scroll">
          <div className="teacher-content">
            <Outlet />
          </div>
        </Content>
      </Layout>
    </Layout>
  )
}
