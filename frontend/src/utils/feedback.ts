/**
 * 全局错误提示出口。
 *
 * axios 拦截器运行在 React 组件树之外，拿不到 antd 的 App 上下文，
 * 因此这里留一个可注入的提示函数：由 FeedbackBridge 在挂载时把 antd 的
 * message 实例注册进来。注册之前（极早期请求失败）退化为控制台输出，
 * 保证任何情况下都不会白屏。
 */
type ErrorNotifier = (content: string) => void

let notify: ErrorNotifier = (content) => {
  console.error('[请求失败]', content)
}

export function setErrorNotifier(next: ErrorNotifier): void {
  notify = next
}

export function showError(content: string): void {
  notify(content)
}
