import { useParams, Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, ExternalLink, Calendar, User, Bot, Loader2 } from 'lucide-react'
import { useArticle } from '../hooks/useArticles'
import { formatTime, getSourceColor } from '../lib/utils'

export default function DetailPage() {
  const { id } = useParams<{ id: string }>()
  const { data: article, isLoading, isError } = useArticle(Number(id))

  if (isLoading) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-16 text-center">
        <Loader2 className="w-8 h-8 mx-auto animate-spin text-primary" />
        <p className="mt-4 text-muted-foreground">加载文章中...</p>
      </div>
    )
  }

  if (isError || !article) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-16 text-center">
        <p className="text-alert text-lg">⚠️ 文章加载失败</p>
        <Link to="/" className="text-primary hover:underline mt-4 inline-block">
          返回首页
        </Link>
      </div>
    )
  }

  const sourceColor = getSourceColor(article.source_name)
  const publishTime = formatTime(article.publish_time)
  const crawlTime = formatTime(article.crawl_time)

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="max-w-4xl mx-auto px-4 py-8"
    >
      {/* 返回链接 */}
      <Link
        to="/"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors mb-6"
      >
        <ArrowLeft className="w-4 h-4" />
        返回新闻列表
      </Link>

      {/* 文章标题 */}
      <h1 className="text-[28px] font-bold text-foreground mb-4 leading-tight">
        {article.title}
      </h1>

      {/* 元信息 */}
      <div className="flex flex-wrap gap-3 text-sm text-muted-foreground mb-8 pb-6 border-b border-border">
        <span
          className="font-semibold px-2 py-0.5 rounded-full text-xs"
          style={{ backgroundColor: sourceColor + '15', color: sourceColor }}
        >
          {article.source_name || '未知来源'}
        </span>
        <span className="flex items-center gap-1">
          <Calendar className="w-3.5 h-3.5" />
          {publishTime}
        </span>
        <span className="flex items-center gap-1">
          <Calendar className="w-3.5 h-3.5" />
          抓取: {crawlTime}
        </span>
        {article.author && (
          <span className="flex items-center gap-1">
            <User className="w-3.5 h-3.5" />
            {article.author}
          </span>
        )}
      </div>

      {/* AI 摘要 */}
      <section className="mb-8">
        <h2 className="flex items-center gap-2 text-lg font-semibold mb-3">
          <Bot className="w-5 h-5 text-primary" />
          AI 中文摘要 (DeepSeek)
        </h2>
        <div className="bg-primary/5 border-l-4 border-primary rounded-r-lg p-4">
          {article.summary ? (
            <p className="text-foreground leading-relaxed">{article.summary}</p>
          ) : (
            <p className="text-muted-foreground italic">AI 摘要尚未生成，请稍后再查看</p>
          )}
        </div>
      </section>

      {/* 原文内容 */}
      <section className="mb-8">
        <h2 className="text-lg font-semibold mb-3">📄 原文内容</h2>
        <div className="text-muted-foreground leading-relaxed whitespace-pre-wrap text-sm">
          {article.content
            ? article.content.length > 3000
              ? article.content.slice(0, 3000) + '...'
              : article.content
            : '正文内容不可用'}
        </div>
      </section>

      {/* 原文链接 */}
      <section>
        <h2 className="text-lg font-semibold mb-3">🔗 原文链接</h2>
        <a
          href={article.url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 px-4 py-2 bg-secondary rounded-lg text-sm text-primary hover:bg-secondary/80 transition-colors"
        >
          <ExternalLink className="w-4 h-4" />
          查看原文
        </a>
      </section>
    </motion.div>
  )
}
