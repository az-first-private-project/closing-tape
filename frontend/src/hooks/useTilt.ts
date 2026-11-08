import { useEffect, useState } from 'react'
import { isTiltEnabled, subscribeTilt } from '../lib/motionPrefs'

/** 订阅 3D 倾斜偏好（U08） */
export function useTiltEnabled(): boolean {
  const [enabled, setEnabled] = useState(isTiltEnabled)
  useEffect(() => subscribeTilt(() => setEnabled(isTiltEnabled())), [])
  return enabled
}
