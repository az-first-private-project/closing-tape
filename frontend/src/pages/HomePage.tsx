import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { CheckCircle2, SearchX, Loader2 } from 'lucide-react'
import StatsBar from '../components/StatsBar'
import FilterTabs from '../components/FilterTabs'
import ArticleCard from '../components/ArticleCard'
import PaginationBar from '../components/PaginationBar'
import { ErrorState } from '../components/StateViews'
import { useArticles, useSearch, useAutoCrawl } from '../hooks/useArticles'
import { useDelayedFlag } from '../hooks/useDelayedFlag'

export default function HomePage() {
  const [searchParams] = useSearchParams()
  const searchQuery = searchParams.get('q') || ''

  const [page, setPage] = useState(1)
  const [sourceId, setSourceId] = useState<number | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const articlesQuery = useArticles(page, sourceId)
  const searchQueryHook = useSearch(searchQuery, page)
  const { crawling, lastResult } = useAutoCrawl()

  useEffect(() => {
    if (crawling) {
      setNotice(null)
      return
    }
    if (lastResult && lastResult.new > 0) {
      setNotice(`已更新 ${lastResult.new} 条最新财经新闻`)
      const timer = setTimeout(() => setNotice(null), 8000)
      return () => clearTimeout(timer)
    }
  }, [crawling, lastResult])

  // 如果在搜索，用搜索结果；否则用文章列表
  const isSearching = searchQuery.length > 0
  const { data, isLoading, isError, error, refetch } = isSearching ? searchQueryHook : articlesQuery
  const showListSkeleton = useDelayedFlag(isLoading)

  return (
    <div>
      <StatsBar />
      <FilterTabs
        active={sourceId}
        onChange={(id) => {
          setSourceId(id)
          setPage(1)
        }}
      />

      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        {/* ===== 状态条 ===== */}
        {crawling && (
          <div className="mb-5 flex items-center gap-2.5 rounded-lg border border-brass/30 bg-brass/[0.06] px-4 py-2.5 text-[12px] text-brass-strong">
            <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" aria-hidden="true" />
            正在搜索最新财经新闻…已有新闻可先浏览
          </div>
        )}
        {!crawling && notice && (
          <div className="mb-5 flex items-center gap-2.5 rounded-lg border border-success/30 bg-success/[0.06] px-4 py-2.5 text-[12px] text-success">
            <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
            {notice}
          </div>
        )}

        {/* ===== 列表刊头 ===== */}
        <div className="mb-6 flex flex-wrap items-end justify-between gap-3 border-b border-border/60 pb-3">
          <div>
            <div className="flex items-center gap-2">
              <span aria-hidden="true" className="h-px w-5 bg-brass/70" />
              <span className="eyebrow">{isSearching ? 'Search Result' : 'Latest Wire'}</span>
            </div>
            <h2 className="mt-2 text-[19px] font-semibold tracking-[-0.02em]">
              {isSearching ? (
                <>
                  搜索「<span className="text-brass-strong dark:text-brass">{searchQuery}</span>」
                </>
              ) : (
                '最新情报'
              )}
            </h2>
          </div>

          <div className="flex items-center gap-3 pb-0.5 font-mono text-[11px] text-muted-foreground">
            {data && <span>{data.total.toLocaleString()} 条</span>}
            {data && data.total_pages > 1 && (
              <span className="text-muted-foreground/60">
                P{String(data.page).padStart(2, '0')}/{String(data.total_pages).padStart(2, '0')}
              </span>
            )}
          </div>
        </div>

        {/* 加载状态 — 骨架占位始终参与布局，超过 150ms 才可见，避免闪烁与跳变 */}
        {isLoading && (
          <div
            className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3 lg:gap-5"
            aria-busy="true"
            style={{ visibility: showListSkeleton ? 'visible' : 'hidden' }}
          >
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="skeleton h-56 rounded-[14px]" />
            ))}
          </div>
        )}

        {/* 错误状态 */}
        {isError && (
          <ErrorState title="新闻加载失败" error={error} onRetry={() => refetch()} />
        )}

        {/* 空状态 */}
        {!isLoading && !isError && data && data.items.length === 0 && (
          crawling ? (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3 lg:gap-5" aria-busy="true">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="skeleton h-56 rounded-[14px]" />
              ))}
              <p className="col-span-full text-center text-sm text-muted-foreground">
                正在搜索最新财经新闻…首次抓取通常需要几秒钟
              </p>
            </div>
          ) : (
            <div className="card flex flex-col items-center gap-3 px-6 py-20 text-center">
              <span className="grid h-12 w-12 place-items-center rounded-full border border-border/80 bg-muted/40">
                <SearchX className="h-5 w-5 text-muted-foreground/60" />
              </span>
              <p className="mt-1 text-[15px] font-medium text-foreground">
                {isSearching ? `未找到与「${searchQuery}」相关的新闻` : '暂无新闻数据'}
              </p>
              <p className="max-w-sm text-[12px] leading-relaxed text-muted-foreground">
                {isSearching ? '请尝试其他关键词' : '新闻抓取失败或被限流，刷新页面可重试'}
              </p>
            </div>
          )
        )}

        {/* 文章网格 */}
        {data && data.items.length > 0 && (
          <AnimatePresence mode="wait">
            <motion.div
              key={`${page}-${sourceId}-${searchQuery}`}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3 lg:gap-5"
            >
              {data.items.map((article, index) => (
                <ArticleCard
                  key={article.id}
                  article={article}
                  index={index}
                  featured={index === 0 && data.items.length >= 4}
                />
              ))}
            </motion.div>
          </AnimatePresence>
        )}

        {/* 分页 */}
        {data && data.total_pages > 1 && (
          <PaginationBar
            page={data.page}
            totalPages={data.total_pages}
            total={data.total}
            onPageChange={setPage}
          />
        )}
      </div>
    </div>
  )
}
