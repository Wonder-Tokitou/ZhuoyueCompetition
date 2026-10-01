import { Alert } from 'antd'
import type { AiTaskResponse } from '../../api/types'

const STAGES: Record<string, string> = { queued: '等待执行', extract_source: '素材已解析', generate_case: '生成与校验案例', case_baseline: '生成案例基准', generate_nodes: '生成决策节点', persist_nodes: '保存决策节点', validate_case: '校验通过', persist_draft: '保存待校准版本', completed: '生成完成', failed: '生成失败', retrying: '暂时失败，后台重试中' }
export const pendingTask = (task: AiTaskResponse | null) => !!task && ['queued', 'running'].includes(task.status)


export default function TaskStatus({ task, busy }: { task: AiTaskResponse; busy: boolean }) {
  return <Alert showIcon type={task.status === 'failed' ? 'error' : task.status === 'succeeded' ? 'success' : 'info'} message={`后台任务：${STAGES[task.stage] || '处理中'} · 第 ${task.attempts || 0} 次执行`} description={task.error || (busy ? '可切换页面；返回后会自动恢复进度。后端服务需保持运行。' : undefined)} />
}
