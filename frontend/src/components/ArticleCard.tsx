import { useRef } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowUpRight } from 'lucide-react'
import { Article } from '../api/news'
import { cn, formatTime, getSourceColor } from '../lib/utils'
import { useTiltEnabled } from '../hooks/useTilt'

interface ArticleCardProps {
  article: Article
  index: number
  /** 首条稿件用更大的版面呈现，形成编辑部的「头条」层级 */
  featured?: boolean
}

const MAX_TILT = 4.2

export default function ArticleCard({ article, index, featured = false }: ArticleCardProps) {
  const cardRef = useRef<HTMLDivElement>(null)
  const rectRef = useRef<DOMRect | null>(null)
  const rafRef = useRef<number | null>(null)
  const tiltEnabled = useTiltEnabled()

  /** 变换写入 CSS 变量，避免每次 mousemove 重写内联 style 造成整卡重算 */
  const handleMouseEnter = () => {
    if (!tiltEnabled) return
    rectRef.current = cardRef.current?.getBoundingClientRect() ?? null
  }

  const handleMouseMove = (event: React.MouseEvent) => {
    const card = cardRef.current
    const rect = rectRef.current
    if (!tiltEnabled || !card || !rect || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

    const clientX = event.clientX
    const clientY = event.clientY
    if (rafRef.current !== null) return

    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null
      const x = clientX - rect.left
      const y = clientY - rect.top
      const rotateX = ((y - rect.height / 2) / (rect.height / 2)) * -MAX_TILT
      const rotateY = ((x - rect.width / 2) / (rect.width / 2)) * MAX_TILT
      card.classList.add('is-tilting')
      card.style.setProperty('--tilt-x', `${rotateX.toFixed(2)}deg`)
      card.style.setProperty('--tilt-y', `${rotateY.toFixed(2)}deg`)
      card.style.setProperty('--tilt-lift', '-3px')
      card.style.setProperty('--sheen-x', `${x}px`)
      card.style.setProperty('--sheen-y', `${y}px`)
    })
  }

  const handleMouseLeave = () => {
    const card = cardRef.current
    if (!card) return
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
    rectRef.current = null
    card.classList.remove('is-tilting')
    card.style.setProperty('--tilt-x', '0deg')
    card.style.setProperty('--tilt-y', '0deg')
    card.style.setProperty('--tilt-lift', '0px')
  }

  const sourceColor = getSourceColor(article.source_name)
  const isProcessed = article.is_processed === 1
  const publishTime = formatTime(article.publish_time)

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, delay: Math.min(index * 0.04, 0.32), ease: [0.16, 1, 0.3, 1] }}
      className={cn(featured && 'md:col-span-2')}
    >
      <Link to={`/article/${article.id}`} className="block h-full">
        <article
          ref={cardRef}
          onMouseEnter={handleMouseEnter}
          onMouseMove={handleMouseMove}
          onMouseLeave={handleMouseLeave}
          className={cn(
            'card group relative flex h-full cursor-pointer flex-col overflow-hidden p-5',
            tiltEnabled && 'card-3d card-sheen',
            'hover:border-brass/45',
            featured && 'sm:p-7',
          )}
        >
          {/* 头条：顶部铜线 + 更轻的纸面渐变，形成版面重量差 */}
          {featured && (
            <span
              aria-hidden="true"
              className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-brass/70 via-brass/25 to-transparent"
            />
          )}

          {/* 头部：来源 + 时间 */}
          <div className="mb-3 flex items-center justify-between gap-2">
            <span
              className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-[3px] text-[12px] font-semibold"
              style={{
                backgroundColor: sourceColor + '12',
                borderColor: sourceColor + '33',
                color: sourceColor,
              }}
            >
              <span
                aria-hidden="true"
                className="h-1 w-1 rounded-full"
                style={{ backgroundColor: sourceColor }}
              />
              {article.source_name || '未知来源'}
            </span>
            <span className="num text-[12px] text-muted-foreground">{publishTime}</span>
          </div>

          {/* 标题 */}
          <h3
            className={cn(
              'font-semibold leading-snug text-card-foreground transition-colors duration-300',
              'group-hover:text-brass-strong dark:group-hover:text-brass',
              featured ? 'text-[20px] tracking-[-0.015em] sm:text-[23px]' : 'text-[15px]',
              'line-clamp-2',
            )}
          >
            {article.title}
          </h3>

          {/* 摘要 */}
          <p
            className={cn(
              'mt-2.5 mb-5 text-[13px] leading-relaxed',
              featured ? 'line-clamp-3' : 'line-clamp-3',
              article.summary ? 'text-muted-foreground' : 'italic text-muted-foreground/55',
            )}
          >
            {article.summary || 'AI 摘要生成中...'}
          </p>

          {/* 底部：AI 状态 + 查看详情 */}
          <div className="mt-auto flex items-center justify-between border-t border-border/60 pt-3">
            <span
              className={cn(
                'inline-flex items-center gap-1.5 text-[12px] font-medium',
                isProcessed ? 'text-success' : 'text-warning',
              )}
            >
              <span
                aria-hidden="true"
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  isProcessed ? 'bg-success' : 'bg-warning',
                )}
              />
              {isProcessed ? 'AI 已总结' : '待处理'}
            </span>

            <span className="inline-flex items-center gap-1 text-[12px] font-medium text-muted-foreground transition-colors group-hover:text-brass">
              查看详情
              <ArrowUpRight
                className="h-3.5 w-3.5 transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] group-hover:-translate-y-0.5 group-hover:translate-x-0.5"
                strokeWidth={2}
              />
            </span>
          </div>
        </article>
      </Link>
    </motion.div>
  )
}
