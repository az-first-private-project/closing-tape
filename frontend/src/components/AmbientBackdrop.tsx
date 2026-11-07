/**
 * 背景：行情底流（Market Undertow）
 * ================================================================
 * 在「暖纸 / 墨色 / 铜色」的基调上，给背景一层与业务同源的动势。
 * 背景不是贴上去的科技纹理，而是把产品自己的数据语言（多周期行情、
 * 采样点阵、数据包）缩小成水印铺在页面之下。
 *
 * 五个层（后 → 前）：
 *   1. base    静态暖光 + 冷侧光，维持纸感 / 墨感
 *   2. drift   两团 34s / 46s 漂移的色光，只动 transform，交给合成器
 *   3. canvas  波场点阵 + 三条行波曲线 + 沿曲线流动的数据包
 *   4. grain   静态胶片颗粒，压住大面积渐变的色带
 *   5. mesh    （已并入 canvas，避免覆盖两套栅格）
 *
 * 曲线用「行波」而非原地起伏：u = x + t·travel，形态整体向左推进，
 * 读起来就是行情在滚动（一条线划过屏幕约 20s）；叠加一个慢 morph 项
 * 让波形缓慢变形，不至于像规整的正弦。点阵由一个斜向穿行的波场调制，
 * 波峰处的点更大、更暖，于是有一条「采样波前」持续扫过数据网格。
 *
 * 可读性策略：
 *   - 全部位于内容之下（z-index:-1），pointer-events 关闭；
 *   - 曲线横向两端最亮、中部收细到一半，纵向 58% 处完全隐去；
 *   - 点阵只铺到 66%，且始终比曲线暗一档；
 *   - 单像素细线，观感是「水印」而不是「图形」。
 *
 * 性能策略：
 *   - 只跑一个 rAF，30fps 节流；标签页隐藏时暂停；
 *   - DPR 上限 1.5，画布尺寸跟随视口；
 *   - 点阵按亮度分 6 档批量成路径，每档一次 fill；
 *   - 光晕用「加宽低透明度描边」模拟，不用 shadowBlur / filter；
 *   - 窄屏降到 24fps；prefers-reduced-motion 只绘制一帧静态画面。
 */

import { useEffect, useRef } from 'react'
import { useTheme } from './ThemeProvider'

type Tone = 'brass' | 'steel' | 'teal'

interface Series {
  /** 基线位置（占视口高度比例） */
  base: number
  /** 振幅（px，基准 900 高） */
  amp: number
  /** 空间频率 */
  freq: number
  /** 行波推进速度（px/s），决定「行情滚动」的快慢 */
  travel: number
  /** 慢变形速度（rad/s），让波形不完全刚性 */
  morph: number
  /** 相位，错开三条线的形态 */
  phase: number
  tone: Tone
  /** 两端的基础透明度 */
  alpha: number
  /** 数据包数量 */
  packets: number
}

const SERIES: Series[] = [
  { base: 0.075, amp: 32, freq: 0.0052, travel: 68, morph: 0.32, phase: 0.6, tone: 'brass', alpha: 0.46, packets: 4 },
  { base: 0.145, amp: 42, freq: 0.0041, travel: 46, morph: 0.22, phase: 2.4, tone: 'steel', alpha: 0.26, packets: 3 },
  { base: 0.26, amp: 24, freq: 0.0068, travel: 30, morph: 0.15, phase: 4.1, tone: 'teal', alpha: 0.19, packets: 3 },
]

/** 点阵栅格间距（px） */
const DOT_GAP = 28
/** 点阵亮度分档数：每档一条路径，一次 fill */
const DOT_LEVELS = 6

/** 按比例混合两个 #rrggbb 颜色 */
function mix(from: string, to: string, amount: number): string {
  const parse = (hex: string) => {
    const raw = hex.replace('#', '')
    const full = raw.length === 3 ? raw.split('').map((char) => char + char).join('') : raw
    return [
      Number.parseInt(full.slice(0, 2), 16) || 0,
      Number.parseInt(full.slice(2, 4), 16) || 0,
      Number.parseInt(full.slice(4, 6), 16) || 0,
    ]
  }
  const a = parse(from)
  const b = parse(to)
  const channel = (index: number) => Math.round(a[index] + (b[index] - a[index]) * amount)
  return `rgb(${channel(0)}, ${channel(1)}, ${channel(2)})`
}

/** #rrggbb → rgba() */
function rgba(hex: string, alpha: number): string {
  const raw = hex.replace('#', '')
  const full = raw.length === 3 ? raw.split('').map((char) => char + char).join('') : raw
  const r = Number.parseInt(full.slice(0, 2), 16) || 0
  const g = Number.parseInt(full.slice(2, 4), 16) || 0
  const b = Number.parseInt(full.slice(4, 6), 16) || 0
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

export default function AmbientBackdrop() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const { theme } = useTheme()

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const narrow = window.matchMedia('(max-width: 767px)').matches
    // 手机同样动画，只是降到 24fps：画布只有几百个点，代价很小
    const animated = !reduced
    const frameBudget = narrow ? 42 : 33

    // 主题切换时重新取值：canvas 无法解析 color-mix()，这里读的是最终色值
    const styles = getComputedStyle(document.documentElement)
    const palette: Record<Tone, string> = {
      // 浅色纸面上用更深的铜，避免亮线被读成污渍
      brass:
        styles.getPropertyValue(theme === 'dark' ? '--color-brass' : '--color-brass-strong').trim() ||
        '#a3762f',
      steel: styles.getPropertyValue('--color-chart-1').trim() || '#3d6b8f',
      teal: styles.getPropertyValue('--color-chart-3').trim() || '#2e7d6b',
    }
    // 点阵在深色里用铜色做「屏幕荧光」，浅色里用墨色做「印刷网点」；
    // 波峰处的点再向暖色偏一档，让扫过的波前有温度
    const inkColor = styles.getPropertyValue('--color-foreground').trim() || '#16181c'
    const dotColor = theme === 'dark' ? palette.brass : inkColor
    const dotHot =
      theme === 'dark'
        ? styles.getPropertyValue('--color-brass-strong').trim() || '#ecc98d'
        : palette.brass
    const dotBase = theme === 'dark' ? 0.32 : 0.2
    const alphaScale = theme === 'dark' ? 1.55 : 1
    const glowScale = theme === 'dark' ? 1 : 0.7

    let width = 0
    let height = 0
    let frame = 0
    let lastPaint = 0
    let visible = !document.hidden

    /** 数据包光点：预渲染成雪碧图，避免每帧创建渐变 */
    const sprites = {} as Record<Tone, HTMLCanvasElement>
    ;(Object.keys(palette) as Tone[]).forEach((tone) => {
      const size = 40
      const sprite = document.createElement('canvas')
      sprite.width = size
      sprite.height = size
      const sctx = sprite.getContext('2d')
      if (!sctx) return
      const gradient = sctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2)
      gradient.addColorStop(0, rgba(palette[tone], 0.95))
      gradient.addColorStop(0.22, rgba(palette[tone], 0.42))
      gradient.addColorStop(0.55, rgba(palette[tone], 0.12))
      gradient.addColorStop(1, rgba(palette[tone], 0))
      sctx.fillStyle = gradient
      sctx.fillRect(0, 0, size, size)
      sprites[tone] = sprite
    })

    /** 点阵分档缓冲区：复用数组，避免每帧分配 */
    const dotBuckets: number[][] = Array.from({ length: DOT_LEVELS }, () => [])

    /** 行波：u 前移使形态整体向左推进，morph 项让波形缓慢变形 */
    const yAt = (x: number, time: number, series: Series, amp: number) => {
      const u = x + time * series.travel
      return (
        height * series.base +
        Math.sin(u * series.freq + series.phase) * amp +
        Math.sin(u * series.freq * 2.7 + series.phase * 1.7) * amp * 0.34 +
        Math.sin(u * series.freq * 0.55 - time * series.morph + series.phase) * amp * 0.44
      )
    }

    /** 点阵：斜向波场调制亮度，亮处连成一条缓慢扫过的波前 */
    const drawDots = (time: number) => {
      for (let level = 0; level < DOT_LEVELS; level += 1) dotBuckets[level].length = 0

      const limit = height * 0.66
      for (let y = DOT_GAP; y < limit; y += DOT_GAP) {
        for (let x = DOT_GAP; x < width; x += DOT_GAP) {
          const wave =
            Math.sin((x * 0.82 + y * 0.5) * 0.0102 - time * 0.95) * 0.66 +
            Math.sin(x * 0.0036 - y * 0.0018 + time * 0.42) * 0.34
          const raw = Math.min(1, Math.max(0, 0.5 + 0.5 * wave))
          // 留一层底：网格任何时刻都成立，波只负责让点「涨大」而不是消失
          const weight = 0.14 + 0.86 * Math.pow(raw, 1.5)
          const level = Math.min(DOT_LEVELS - 1, Math.floor(weight * DOT_LEVELS))
          dotBuckets[level].push(x, y)
        }
      }

      for (let level = 0; level < DOT_LEVELS; level += 1) {
        const points = dotBuckets[level]
        if (!points.length) continue
        const ratio = level / (DOT_LEVELS - 1)
        // 波动主要体现在「点的大小」，亮度只做小幅过渡 —— 网格不会碎成噪点
        const radius = 0.75 + 1.55 * Math.pow(ratio, 1.4)
        // 暖色只在最亮的 3 档出现，避免整片网点发黄
        const warmth = Math.max(0, ratio - 0.55) / 0.45
        ctx.fillStyle = warmth > 0 ? mix(dotColor, dotHot, warmth * 0.85) : dotColor
        ctx.globalAlpha = dotBase * (0.52 + 0.48 * ratio)
        ctx.beginPath()
        for (let index = 0; index < points.length; index += 2) {
          const px = points[index]
          const py = points[index + 1]
          ctx.moveTo(px + radius, py)
          ctx.arc(px, py, radius, 0, Math.PI * 2)
        }
        ctx.fill()
      }
      ctx.globalAlpha = 1
    }

    const draw = (time: number) => {
      ctx.clearRect(0, 0, width, height)

      // ---------- 1. 波场点阵 ----------
      drawDots(time)

      // ---------- 2. 曲线 ----------
      for (let index = 0; index < SERIES.length; index += 1) {
        const series = SERIES[index]
        const amp = series.amp * Math.min(1.25, Math.max(0.72, height / 900))
        // 窄屏的可视宽度小，亮端离正文太近；整体压低一档，保证小屏的阅读区安静
        const alpha = series.alpha * alphaScale * (width < 768 ? 0.72 : 1)

        // 横向渐变：两端最实，中部收细到一半 —— 保证曲线连续，但让开阅读区
        const stroke = ctx.createLinearGradient(0, 0, width, 0)
        stroke.addColorStop(0, rgba(palette[series.tone], alpha))
        stroke.addColorStop(0.24, rgba(palette[series.tone], alpha * 0.78))
        stroke.addColorStop(0.5, rgba(palette[series.tone], alpha * 0.45))
        stroke.addColorStop(0.76, rgba(palette[series.tone], alpha * 0.78))
        stroke.addColorStop(1, rgba(palette[series.tone], alpha))

        // 余晖：主曲线叠三层同心描边，形成一条示波器式的荧光带；
        // 其余两条只留一层薄光，避免整片发光。三次 stroke 复用同一条路径。
        const bloom =
          index === 0
            ? [
                { width: 32, alpha: 0.075 },
                { width: 14, alpha: 0.13 },
                { width: 5.5, alpha: 0.22 },
              ]
            : [{ width: 6, alpha: 0.2 }]

        ctx.beginPath()
        for (let x = 0; x <= width; x += 10) {
          const y = yAt(x, time, series, amp)
          if (x === 0) ctx.moveTo(x, y)
          else ctx.lineTo(x, y)
        }
        ctx.strokeStyle = stroke
        for (const layer of bloom) {
          ctx.globalAlpha = glowScale * layer.alpha
          ctx.lineWidth = layer.width
          ctx.stroke()
        }
        ctx.globalAlpha = 1

        // 主线 + 采样短刺：每 72px 一道 4px 竖刺，读作「被等距采样」
        ctx.beginPath()
        for (let x = 0; x <= width; x += 8) {
          const y = yAt(x, time, series, amp)
          if (x === 0) ctx.moveTo(x, y)
          else ctx.lineTo(x, y)

          if (x > 0 && x % 72 === 0) {
            ctx.lineTo(x, y + 4)
            ctx.moveTo(x + 8, yAt(x + 8, time, series, amp))
          }
        }
        ctx.strokeStyle = stroke
        ctx.lineWidth = index === 0 ? 1.8 : 1.25
        ctx.stroke()

        // 亮芯：只在深色主题给主曲线，近白的 1px 让它在暗底上「亮」起来
        if (index === 0 && theme === 'dark') {
          const core = ctx.createLinearGradient(0, 0, width, 0)
          core.addColorStop(0, 'rgba(246, 232, 204, 0.55)')
          core.addColorStop(0.24, 'rgba(246, 232, 204, 0.42)')
          core.addColorStop(0.5, 'rgba(246, 232, 204, 0.2)')
          core.addColorStop(0.76, 'rgba(246, 232, 204, 0.42)')
          core.addColorStop(1, 'rgba(246, 232, 204, 0.55)')
          ctx.strokeStyle = core
          ctx.lineWidth = 1
          ctx.stroke()
        }

        // ---------- 3. 数据包：沿曲线单向流动，越靠近中间越淡 ----------
        const sprite = sprites[series.tone]
        if (!sprite) continue
        const travel = width + 240
        for (let packet = 0; packet < series.packets; packet += 1) {
          const speed = 108 + index * 18
          const offset = series.phase * 190 + (packet * travel) / series.packets
          const x = ((time * speed + offset) % travel) - 120
          const edge = Math.min(1, Math.abs(x - width / 2) / (width * 0.36))
          const strength = Math.max(0, edge - 0.1) / 0.9
          if (strength <= 0.02) continue

          const y = yAt(x, time, series, amp)

          // 彗尾：一条沿 x 方向渐隐的描边，比逐段叠加更连续，也少几次描边
          const tailLength = 116
          const tail = ctx.createLinearGradient(x - tailLength, 0, x, 0)
          const tailAlpha = theme === 'dark' ? 0.95 : 0.5
          tail.addColorStop(0, rgba(palette[series.tone], 0))
          tail.addColorStop(0.45, rgba(palette[series.tone], tailAlpha * 0.28))
          tail.addColorStop(0.82, rgba(palette[series.tone], tailAlpha * 0.72))
          tail.addColorStop(1, rgba(palette[series.tone], tailAlpha))
          ctx.strokeStyle = tail
          ctx.lineWidth = 1.4
          ctx.globalAlpha = strength
          ctx.beginPath()
          for (let step = 0; step <= tailLength; step += 4) {
            const tailX = x - step
            const tailY = yAt(tailX, time, series, amp)
            if (step === 0) ctx.moveTo(tailX, tailY)
            else ctx.lineTo(tailX, tailY)
          }
          ctx.stroke()

          // 光点本体
          ctx.globalAlpha = strength * (theme === 'dark' ? 0.95 : 0.62)
          ctx.drawImage(sprite, x - 15, y - 15, 30, 30)

          // 高光核：让数据包在深色底上「亮起来」
          if (theme === 'dark') {
            ctx.globalAlpha = strength * 0.9
            ctx.fillStyle = '#fdf4e0'
            ctx.beginPath()
            ctx.arc(x, y, 1.5, 0, Math.PI * 2)
            ctx.fill()
          }
        }
        ctx.globalAlpha = 1
      }
    }

    const resize = () => {
      width = window.innerWidth
      height = window.innerHeight
      const dpr = Math.min(1.5, window.devicePixelRatio || 1)
      canvas.width = Math.max(1, Math.floor(width * dpr))
      canvas.height = Math.max(1, Math.floor(height * dpr))
      canvas.style.width = `${width}px`
      canvas.style.height = `${height}px`
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      if (!animated) draw(0.8)
    }

    const loop = (now: number) => {
      frame = window.requestAnimationFrame(loop)
      if (!visible) return
      if (now - lastPaint < frameBudget) return
      lastPaint = now
      draw(now / 1000)
    }

    const onVisibility = () => {
      visible = !document.hidden
    }

    resize()
    if (animated) {
      draw(0.8)
      frame = window.requestAnimationFrame(loop)
    }

    window.addEventListener('resize', resize)
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      window.cancelAnimationFrame(frame)
      window.removeEventListener('resize', resize)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [theme])

  return (
    <div className="ambient" aria-hidden="true">
      <div className="ambient-base" />
      <div className="ambient-drift ambient-drift--a" />
      <div className="ambient-drift ambient-drift--b" />
      <canvas ref={canvasRef} className="ambient-canvas" />
      <div className="ambient-grain" />
    </div>
  )
}
