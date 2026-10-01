import { useEffect } from 'react'
import { App as AntdApp } from 'antd'
import { setErrorNotifier } from '../utils/feedback'

/**
 * 把 antd App 上下文里的 message 实例注册为全局错误提示出口。
 * 渲染 null，只做桥接。
 */
export default function FeedbackBridge() {
  const { message } = AntdApp.useApp()

  useEffect(() => {
    setErrorNotifier((content: string) => {
      void message.error(content)
    })
  }, [message])

  return null
}
