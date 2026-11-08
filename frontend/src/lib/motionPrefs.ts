/**
 * 装饰动效偏好（U08）：3D 倾斜默认开启，可持久关闭。
 * 使用 localStorage + 自定义事件，跨组件/跨标签页同步。
 */
const TILT_KEY = 'gfna-tilt-enabled'
const TILT_EVENT = 'gfna-tilt-change'

export function isTiltEnabled(): boolean {
  try {
    return window.localStorage.getItem(TILT_KEY) !== '0'
  } catch {
    return true
  }
}

export function setTiltEnabled(enabled: boolean): void {
  try {
    window.localStorage.setItem(TILT_KEY, enabled ? '1' : '0')
  } catch {
    // 隐私模式：仅本次会话生效
  }
  window.dispatchEvent(new Event(TILT_EVENT))
}

export function subscribeTilt(listener: () => void): () => void {
  window.addEventListener(TILT_EVENT, listener)
  window.addEventListener('storage', listener)
  return () => {
    window.removeEventListener(TILT_EVENT, listener)
    window.removeEventListener('storage', listener)
  }
}
