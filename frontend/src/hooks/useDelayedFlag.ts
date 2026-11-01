import { useEffect, useState } from 'react'

/** active 持续超过 delay 毫秒后才返回 true，避免快请求闪一下骨架 */
export function useDelayedFlag(active: boolean, delay = 150): boolean {
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    if (!active) {
      setVisible(false)
      return
    }
    const timer = window.setTimeout(() => setVisible(true), delay)
    return () => window.clearTimeout(timer)
  }, [active, delay])

  return visible
}
