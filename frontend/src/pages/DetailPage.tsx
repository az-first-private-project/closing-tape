import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, ChevronDown, ChevronUp, ExternalLink, Calendar, User, Sparkles } from 'lucide-react'
import { useArticle } from '../hooks/useArticles'
import { useDelayedFlag } from '../hooks/useDelayedFlag'
import { ErrorState, SkeletonLine } from '../components/StateViews'
import PageHeader from '../components/PageHeader'
import RangeNotice from '../components/RangeNotice'
import { formatTime, getSourceColor } from '../lib/utils'

const EXCERPT_LENGTH = 3000

/**
 * 新闻详情：单栏编辑排版
 * 标题字重与行高按长文阅读优化（约 68 个字符宽度），AI 摘要单独成块。
 * 长文不再硬截断：默认展示前 3000 字，可展开全文（U03）。
 */
export default function DetailPage() {
  const { id } = useParams<{ id: string }>()
  const { data: article, isLoading, isError, error, refetch } = useArticle(Number(id))
  const showSkeleton = useDelayedFlag(isLoading)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    setExpanded(false)
  }, [id])

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
      <div className="mx-auto max-w-3xl px-4 py-16 sm:px-6">
        <ErrorState
          title="文章加载失败"
          error={error}
          onRetry={() => {
            void refetch()
          }}
        />
        <div className="mt-5 text-center">
          <Link
            to="/"
            className="text-[13px] text-brass-strong underline-offset-4 hover:underline dark:text-brass"
          >
            返回新闻列表
          </Link>
        </div>
      </div>
    )
  }

  const sourceColor = getSourceColor(article.source_name)
  const publishTime = formatTime(article.publish_time)
  const crawlTime = formatTime(article.crawl_time)
  const content = article.content || ''
  const truncated = content.length > EXCERPT_LENGTH
  const visibleContent = expanded || !truncated ? content : content.slice(0, EXCERPT_LENGTH)

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
      <PageHeader
        size="compact"
        eyebrow="News Article · 新闻详情"
        title={article.title}
        className="mt-7 border-b border-border/70 pb-5"
        meta={
          <div className="mt-3.5 flex flex-wrap items-center gap-x-4 gap-y-2 text-[12px] text-muted-foreground">
            <span
              className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-[3px] font-semibold"
              style={{
                backgroundColor: sourceColor + '12',
                borderColor: sourceColor + '33',
                color: sourceColor,
              }}
            >
              <span aria-hidden="true" className="h-1 w-1 rounded-full" style={{ backgroundColor: sourceColor }} />
              {article.source_name || '未知来源'}
            </span>
            <span className="num tracking-wide">{publishTime}</span>
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
        }
      />

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
        <h2 className="mb-3 flex items-center gap-2 text-[12px] font-semibold tracking-[0.12em] text-muted-foreground">
          <span aria-hidden="true" className="h-px w-5 bg-border" />
          原文内容
        </h2>
        <div id="article-content" className="whitespace-pre-wrap text-[13.5px] leading-[1.95] text-foreground/75">
          {visibleContent || '正文内容不可用'}
        </div>
        {truncated && (
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-border/60 pt-4">
            <RangeNotice
              shown={expanded ? content.length : EXCERPT_LENGTH}
              total={content.length}
              unit="字"
              note={expanded ? '已展开全文' : '长文默认折叠'}
            />
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              aria-expanded={expanded}
              aria-controls="article-content"
              className="btn btn-sm"
            >
              {expanded ? '收起全文' : '展开全文'}
              {expanded ? (
                <ChevronUp className="h-3.5 w-3.5" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5" />
              )}
            </button>
          </div>
        )}
      </section>

      {/* 原文链接 */}
      <footer className="mt-10 flex flex-wrap items-center justify-between gap-3 border-t border-border/70 pt-5">
        <span className="text-[12px] text-muted-foreground">内容版权归原始来源所有</span>
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
