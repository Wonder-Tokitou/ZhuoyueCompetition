import { useEffect, useState } from 'react'

/**
 * 订阅视口宽度是否小于断点（默认 768px，即「手机尺寸」）。
 * 用 matchMedia 而不是 resize 事件，避免滚动时高频回调。
 */
export function useIsNarrow(breakpoint = 768): boolean {
  const [isNarrow, setIsNarrow] = useState<boolean>(() =>
    typeof window === 'undefined' ? false : window.innerWidth < breakpoint,
  )

  useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${breakpoint - 1}px)`)
    const onChange = (event: MediaQueryListEvent) => setIsNarrow(event.matches)

    setIsNarrow(mql.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [breakpoint])

  return isNarrow
}
