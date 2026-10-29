import { useRef } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ExternalLink } from 'lucide-react'
import { Article } from '../api/news'
import { formatTime, getSourceColor } from '../lib/utils'

interface ArticleCardProps {
  article: Article
  index: number
}

export default function ArticleCard({ article, index }: ArticleCardProps) {
  const cardRef = useRef<HTMLDivElement>(null)

  const handleMouseMove = (e: React.MouseEvent) => {
    const card = cardRef.current
    if (!card) return

    const rect = card.getBoundingClientRect()
    const x = e.clientX - rect.left
    const y = e.clientY - rect.top
    const centerX = rect.width / 2
    const centerY = rect.height / 2

    const rotateX = (y - centerY) / 20
    const rotateY = (centerX - x) / 20

    card.style.transform = `perspective(1000px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) scale3d(1.02, 1.02, 1.02)`
  }

  const handleMouseLeave = () => {
    const card = cardRef.current
    if (!card) return
    card.style.transform = 'perspective(1000px) rotateX(0deg) rotateY(0deg) scale3d(1, 1, 1)'
  }

  const sourceColor = getSourceColor(article.source_name)
  const isProcessed = article.is_processed === 1
  const publishTime = formatTime(article.publish_time)

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay: index * 0.05 }}
    >
      <Link to={`/article/${article.id}`}>
        <div
          ref={cardRef}
          onMouseMove={handleMouseMove}
          onMouseLeave={handleMouseLeave}
          className="card card-3d p-5 cursor-pointer hover:border-primary/50 transition-shadow duration-300"
        >
          {/* 头部：来源标签 + 时间 */}
          <div className="flex items-center justify-between mb-3">
            <span
              className="text-xs font-semibold px-2.5 py-0.5 rounded-full"
              style={{
                backgroundColor: sourceColor + '15',
                color: sourceColor,
              }}
            >
              {article.source_name || '未知来源'}
            </span>
            <span className="text-xs text-muted-foreground num">{publishTime}</span>
          </div>

          {/* 标题 */}
          <h3 className="font-semibold text-card-foreground mb-2 line-clamp-2 leading-snug">
            {article.title}
          </h3>

          {/* 摘要/内容预览 */}
          <p className={`text-sm mb-4 line-clamp-3 leading-relaxed ${article.summary ? 'text-muted-foreground' : 'text-muted-foreground/50 italic'}`}>
            {article.summary || 'AI 摘要生成中...'}
          </p>

          {/* 底部：AI 状态 + 查看详情 */}
          <div className="flex items-center justify-between mt-auto pt-3 border-t border-border">
            <span
              className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                isProcessed
                  ? 'bg-success/10 text-success'
                  : 'bg-warning/10 text-warning'
              }`}
            >
              {isProcessed ? '🤖 AI 已总结' : '⏳ 待处理'}
            </span>
            <span className="text-xs text-primary font-medium flex items-center gap-1">
              查看详情
              <ExternalLink className="w-3 h-3" />
            </span>
          </div>
        </div>
      </Link>
    </motion.div>
  )
}
