import { useParams, Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, ExternalLink, Calendar, User, Sparkles } from 'lucide-react'
import { useArticle } from '../hooks/useArticles'
import { useDelayedFlag } from '../hooks/useDelayedFlag'
import { SkeletonLine } from '../components/StateViews'
import { formatTime, getSourceColor } from '../lib/utils'

/**
 * 新闻详情：单栏编辑排版
 * 标题字重与行高按长文阅读优化（约 68 个字符宽度），AI 摘要单独成块。
 */
export default function DetailPage() {
  const { id } = useParams<{ id: string }>()
  const { data: article, isLoading, isError } = useArticle(Number(id))
  const showSkeleton = useDelayedFlag(isLoading)

  if (isLoading) {
    return (
      <div
        className="mx-auto max-w-3xl px-4 py-10 sm:px-6"
        aria-busy="true"
        style={{ visibility: showSkeleton ? 'visible' : 'hidden' }}
      >
        <div className="space-y-4">
          <SkeletonLine className="h-3 w-24" />
          <SkeletonLine className="h-9 w-4/5" />
          <SkeletonLine className="h-3 w-1/2" />
          <div className="space-y-3 border-t border-border/60 pt-7">
            {Array.from({ length: 9 }).map((_, index) => (
              <SkeletonLine
                key={index}
                className={index % 4 === 3 ? 'h-3 w-2/3' : 'h-3 w-full'}
              />
            ))}
          </div>
        </div>
      </div>
    )
  }

  if (isError || !article) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-24 text-center sm:px-6">
        <p className="text-[15px] text-alert">文章加载失败</p>
        <Link
          to="/"
          className="mt-4 inline-block text-[13px] text-brass-strong underline-offset-4 hover:underline dark:text-brass"
        >
          返回首页
        </Link>
      </div>
    )
  }

  const sourceColor = getSourceColor(article.source_name)
  const publishTime = formatTime(article.publish_time)
  const crawlTime = formatTime(article.crawl_time)

  return (
    <motion.article
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
      className="mx-auto max-w-3xl px-4 py-10 sm:px-6"
    >
      {/* 返回 */}
      <Link
        to="/"
        className="group inline-flex items-center gap-1.5 text-[12px] text-muted-foreground transition-colors hover:text-brass"
      >
        <ArrowLeft className="h-3.5 w-3.5 transition-transform duration-300 group-hover:-translate-x-0.5" />
        返回新闻列表
      </Link>

      {/* 刊头 */}
      <header className="mt-7">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-[3px] text-[10px] font-semibold"
            style={{
              backgroundColor: sourceColor + '12',
              borderColor: sourceColor + '33',
              color: sourceColor,
            }}
          >
            <span aria-hidden="true" className="h-1 w-1 rounded-full" style={{ backgroundColor: sourceColor }} />
            {article.source_name || '未知来源'}
          </span>
          <span className="num text-[10px] tracking-wide text-muted-foreground">{publishTime}</span>
        </div>

        <h1 className="mt-4 text-[26px] font-semibold leading-[1.3] tracking-[-0.025em] sm:text-[32px]">
          {article.title}
        </h1>

        <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-1.5 border-b border-border/70 pb-5 text-[11px] text-muted-foreground">
          <span className="inline-flex items-center gap-1.5">
            <Calendar className="h-3 w-3" />
            抓取: <span className="num">{crawlTime}</span>
          </span>
          {article.author && (
            <span className="inline-flex items-center gap-1.5">
              <User className="h-3 w-3" />
              {article.author}
            </span>
          )}
        </div>
      </header>

      {/* AI 摘要 */}
      <section className="mt-8">
        <div className="relative overflow-hidden rounded-[14px] border border-brass/25 bg-brass/[0.05] p-5">
          <span aria-hidden="true" className="absolute inset-y-0 left-0 w-[2px] bg-brass/70" />
          <div className="mb-2.5 flex items-center gap-2">
            <Sparkles className="h-3.5 w-3.5 text-brass" />
            <span className="eyebrow eyebrow-brass">AI 摘要 · DeepSeek</span>
          </div>
          {article.summary ? (
            <p className="text-[14px] leading-[1.85] text-foreground/90">{article.summary}</p>
          ) : (
            <p className="text-[13px] italic text-muted-foreground">AI 摘要尚未生成，请稍后再查看</p>
          )}
        </div>
      </section>

      {/* 原文 */}
      <section className="mt-9">
        <h2 className="mb-3 flex items-center gap-2 text-[11px] font-semibold tracking-[0.12em] text-muted-foreground">
          <span aria-hidden="true" className="h-px w-5 bg-border" />
          原文内容
        </h2>
        <div className="whitespace-pre-wrap text-[13.5px] leading-[1.95] text-foreground/75">
          {article.content
            ? article.content.length > 3000
              ? article.content.slice(0, 3000) + '...'
              : article.content
            : '正文内容不可用'}
        </div>
      </section>

      {/* 原文链接 */}
      <footer className="mt-10 flex flex-wrap items-center justify-between gap-3 border-t border-border/70 pt-5">
        <span className="text-[10px] text-muted-foreground">内容版权归原始来源所有</span>
        <a
          href={article.url}
          target="_blank"
          rel="noopener noreferrer"
          className="btn btn-sm group"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          查看原文
        </a>
      </footer>
    </motion.article>
  )
}
