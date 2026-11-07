/**
 * 开场特效：「开盘」
 * ================================================================
 * 触发时机：整站首次加载（每个标签页会话一次）。路由切换不会重播。
 *   1) 尊重 prefers-reduced-motion：直接跳过，不做任何动画；
 *   2) ?intro=1 强制重播（评审/演示用）；?intro=0 强制关闭；
 *   3) sessionStorage 记录已播放，同一标签页刷新不再打扰。
 *
 * 时间轴（内容段 3.3s + 出场段 0.7s，合计 4.0s）：
 *   0.28s  铜色发丝线从中线向两侧展开（1.70s）
 *   0.75s  字标遮罩上移揭示（2.10s）
 *   1.00s  右下角读数 000 → 100 滚动（100 步 / 23ms，3.30s 走满）
 *   1.20s  副标题淡入（1.40s）
 *   3.30s  幕布上下分离（0.68s，与原版同速），页面同步归位
 *   4.00s  幕布卸载，交还滚动与焦点
 *
 * 内容段约为初版的 2.5 倍，出场保持初版速度 ——
 * 进场从容、离场干脆；读数走满 100 的那一帧正好开幕。
 *
 * 交互：点击 / 任意键 / Esc 立即跳过，直接进入分离阶段。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'

const INTRO_FLAG = 'gfna-intro-seen'

/** 幕布开始分离的时刻（毫秒） */
const SPLIT_AT = 3300
/** 幕布完全卸载的时刻（毫秒） */
const FINISH_AT = 4000
/** 跳过时的收尾时长 */
const SKIP_FINISH = 700

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
  /** 幕布开始分离：页面在此刻开始归位 */
  onReveal: () => void
  /** 幕布卸载：交还滚动 */
  onFinish: () => void
}

export default function IntroCurtain({ onReveal, onFinish }: IntroCurtainProps) {
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

  /** 进入分离阶段：页面同步开始揭示 */
  const beginSplit = useCallback(
    (immediate = false) => {
      if (splittingRef.current) return
      splittingRef.current = true
      setSplitting(true)
      onReveal()
      window.setTimeout(finish, immediate ? SKIP_FINISH : 700)
    },
    [finish, onReveal],
  )

  // 读数滚动：1.00s 起计，100 步 × 23ms，正好在 3.30s 走满，与幕布分离同一帧
  useEffect(() => {
    if (splitting) return
    let value = 0
    let timer = 0
    const startTimer = window.setTimeout(() => {
      timer = window.setInterval(() => {
        value = Math.min(100, value + 1)
        setPercent(value)
        if (value >= 100) window.clearInterval(timer)
      }, 23)
    }, 1000)
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

  // 开场期间锁定滚动，结束后还原
  useEffect(() => {
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [])

  // 键盘任意键跳过
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return
      beginSplit(true)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [beginSplit])

  const skip = () => beginSplit(true)

  return (
    <div
      className="intro-curtain"
      role="presentation"
      onPointerDown={skip}
      aria-hidden="true"
    >
      {/* 幕布上下两片：分离时沿中缝推开 */}
      <motion.div
        className="intro-panel intro-panel--top"
        initial={false}
        animate={splitting ? { y: '-101%' } : { y: '0%' }}
        transition={{ duration: 0.68, ease: EASE }}
      />
      <motion.div
        className="intro-panel intro-panel--bottom"
        initial={false}
        animate={splitting ? { y: '101%' } : { y: '0%' }}
        transition={{ duration: 0.68, ease: EASE }}
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
          transition={{ duration: 1.6, delay: 0.16, ease: EASE }}
        >
          A-SHARE INTELLIGENCE
        </motion.p>

        <span className="intro-wordmark">
          <motion.span
            className="intro-wordmark-inner"
            initial={{ y: '112%' }}
            animate={{ y: '0%' }}
            transition={{ duration: 2.1, delay: 0.75, ease: EASE }}
          >
            GFNA<span className="intro-dot">.</span>
          </motion.span>
        </span>

        <motion.p
          className="intro-subline"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 1.4, delay: 1.2 }}
        >
          A 股盘后情报终端 · 盘后分析 / 量化选股 / 纸面跟踪
        </motion.p>

        {/* 铜色发丝线：从中缝向两侧展开 */}
        <div className="intro-rule-wrap">
          <motion.span
            className="intro-rule"
            initial={{ scaleX: 0 }}
            animate={{ scaleX: 1 }}
            transition={{ duration: 1.7, delay: 0.28, ease: EASE }}
          />
        </div>

        <motion.div
          className="intro-meta"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 1.3, delay: 1 }}
        >
          <span>MARKET CLOSE</span>
          <span className="num">{String(percent).padStart(3, '0')}</span>
        </motion.div>
      </motion.div>

      <motion.span
        className="intro-skip"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 1.6, delay: 1.3 }}
      >
        点击任意处跳过
      </motion.span>
    </div>
  )
}
