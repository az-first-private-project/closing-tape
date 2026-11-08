/**
 * 开场特效：「开盘」
 * ================================================================
 * 触发时机：整站首次加载（每个标签页会话一次）。路由切换不会重播。
 *   1) 尊重 prefers-reduced-motion：直接跳过，不做任何动画；
 *   2) ?intro=1 强制重播（评审/演示用）；?intro=0 强制关闭；
 *   3) sessionStorage 记录已播放，同一标签页刷新不再打扰。
 *
 * U08：首屏不再被开场阻塞 —— 幕布 pointer-events:none，页面立即可交互，
 * 也绝不锁定 body 滚动；整体时长压到约 1.6s。
 *
 * 时间轴：
 *   0.05s  铜色发丝线展开（0.90s）
 *   0.10s  字标遮罩上移揭示（0.80s）
 *   0.35s  副标题淡入（0.50s）
 *   0.40s  右下角读数 000 → 100 滚动（7ms/步，1.10s 走满）
 *   1.10s  幕布上下分离（0.42s）
 *   1.55s  幕布卸载
 *
 * 交互：点击任意处 / 任意键立即跳过，直接进入分离阶段。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'

const INTRO_FLAG = 'gfna-intro-seen'

/** 幕布开始分离的时刻（毫秒） */
const SPLIT_AT = 1100
/** 幕布完全卸载的时刻（毫秒） */
const FINISH_AT = 1550
/** 跳过时的收尾时长 */
const SKIP_FINISH = 450

const EASE = [0.16, 1, 0.3, 1] as const

/** 是否播放开场：会话内一次 + 尊重减弱动效 + 支持 URL 强制 */
export function shouldPlayIntro(): boolean {
  if (typeof window === 'undefined') return false

  const params = new URLSearchParams(window.location.search)
  if (params.get('intro') === '0') return false
  if (params.get('intro') === '1') return true

  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return false

  try {
    return window.sessionStorage.getItem(INTRO_FLAG) !== '1'
  } catch {
    // 隐私模式 / 存储被禁用：只播一次也无从判断，选择播放
    return true
  }
}

function markIntroSeen() {
  try {
    window.sessionStorage.setItem(INTRO_FLAG, '1')
  } catch {
    /* 忽略 */
  }
}

interface IntroCurtainProps {
  /** 幕布卸载 */
  onFinish: () => void
}

export default function IntroCurtain({ onFinish }: IntroCurtainProps) {
  const [splitting, setSplitting] = useState(false)
  const [percent, setPercent] = useState(0)
  const splittingRef = useRef(false)
  const finishedRef = useRef(false)

  const finish = useCallback(() => {
    if (finishedRef.current) return
    finishedRef.current = true
    markIntroSeen()
    onFinish()
  }, [onFinish])

  /** 进入分离阶段 */
  const beginSplit = useCallback(
    (immediate = false) => {
      if (splittingRef.current) return
      splittingRef.current = true
      setSplitting(true)
      window.setTimeout(finish, immediate ? SKIP_FINISH : 450)
    },
    [finish],
  )

  // 读数滚动：0.40s 起计，100 步 × 7ms，在 1.10s 走满，与幕布分离同一帧
  useEffect(() => {
    if (splitting) return
    let value = 0
    let timer = 0
    const startTimer = window.setTimeout(() => {
      timer = window.setInterval(() => {
        value = Math.min(100, value + 1)
        setPercent(value)
        if (value >= 100) window.clearInterval(timer)
      }, 7)
    }, 400)
    return () => {
      window.clearTimeout(startTimer)
      window.clearInterval(timer)
    }
  }, [splitting])

  // 主时间轴
  useEffect(() => {
    const splitTimer = window.setTimeout(() => beginSplit(false), SPLIT_AT)
    const finishTimer = window.setTimeout(finish, FINISH_AT)
    return () => {
      window.clearTimeout(splitTimer)
      window.clearTimeout(finishTimer)
    }
  }, [beginSplit, finish])

  // 不锁滚动；点击穿透到页面，同时借窗口捕获阶段跳过开场
  useEffect(() => {
    const onPointerDown = () => beginSplit(true)
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return
      beginSplit(true)
    }
    window.addEventListener('pointerdown', onPointerDown, { capture: true, passive: true })
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('pointerdown', onPointerDown, { capture: true })
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [beginSplit])

  return (
    <div className="intro-curtain" role="presentation" aria-hidden="true">
      {/* 幕布上下两片：分离时沿中缝推开 */}
      <motion.div
        className="intro-panel intro-panel--top"
        initial={false}
        animate={splitting ? { y: '-101%' } : { y: '0%' }}
        transition={{ duration: 0.42, ease: EASE }}
      />
      <motion.div
        className="intro-panel intro-panel--bottom"
        initial={false}
        animate={splitting ? { y: '101%' } : { y: '0%' }}
        transition={{ duration: 0.42, ease: EASE }}
      />

      {/* 中央字标 */}
      <motion.div
        className="intro-center"
        animate={splitting ? { opacity: 0, y: -10 } : { opacity: 1, y: 0 }}
        transition={{ duration: 0.26, ease: 'easeOut' }}
      >
        <motion.p
          className="intro-eyebrow"
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.05, ease: EASE }}
        >
          A-SHARE INTELLIGENCE
        </motion.p>

        <span className="intro-wordmark">
          <motion.span
            className="intro-wordmark-inner"
            initial={{ y: '112%' }}
            animate={{ y: '0%' }}
            transition={{ duration: 0.8, delay: 0.1, ease: EASE }}
          >
            GFNA<span className="intro-dot">.</span>
          </motion.span>
        </span>

        <motion.p
          className="intro-subline"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.5, delay: 0.35 }}
        >
          A 股盘后情报终端 · 盘后分析 / 量化选股 / 纸面跟踪
        </motion.p>

        {/* 铜色发丝线：从中缝向两侧展开 */}
        <div className="intro-rule-wrap">
          <motion.span
            className="intro-rule"
            initial={{ scaleX: 0 }}
            animate={{ scaleX: 1 }}
            transition={{ duration: 0.9, delay: 0.05, ease: EASE }}
          />
        </div>

        <motion.div
          className="intro-meta"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.5, delay: 0.4 }}
        >
          <span>MARKET CLOSE</span>
          <span className="num">{String(percent).padStart(3, '0')}</span>
        </motion.div>
      </motion.div>

      <motion.span
        className="intro-skip"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.5, delay: 0.4 }}
      >
        点击任意处跳过
      </motion.span>
    </div>
  )
}
