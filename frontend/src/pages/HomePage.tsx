import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { CheckCircle2, Loader2, SearchX } from 'lucide-react'
import StatsBar from '../components/StatsBar'
import FilterTabs from '../components/FilterTabs'
import ArticleCard from '../components/ArticleCard'
import PaginationBar from '../components/PaginationBar'
import { useArticles, useSearch, useAutoCrawl } from '../hooks/useArticles'

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
  const { data, isLoading, isError, error } = isSearching ? searchQueryHook : articlesQuery

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

      <div className="max-w-7xl mx-auto px-4 py-6">
        {/* 自动抓取进度（旧新闻保持可见） */}
        {crawling && (
          <div className="mb-4 flex items-center gap-2.5 rounded-lg border border-primary/30 bg-primary/5 px-4 py-2.5 text-sm text-primary">
            <Loader2 className="w-4 h-4 animate-spin shrink-0" />
            正在搜索最新财经新闻…已有新闻可先浏览
          </div>
        )}
        {!crawling && notice && (
          <div className="mb-4 flex items-center gap-2.5 rounded-lg border border-success/30 bg-success/5 px-4 py-2.5 text-sm text-success">
            <CheckCircle2 className="w-4 h-4 shrink-0" />
            {notice}
          </div>
        )}

        {/* 搜索提示 */}
        {isSearching && (
          <div className="mb-4 text-sm text-muted-foreground">
            搜索「{searchQuery}」的结果
          </div>
        )}

        {/* 加载状态 — 骨架屏 */}
        {isLoading && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="skeleton h-56 rounded-xl" />
            ))}
          </div>
        )}

        {/* 错误状态 */}
        {isError && (
          <div className="text-center py-20">
            <p className="text-alert text-lg">⚠️ 加载失败</p>
            <p className="text-muted-foreground text-sm mt-2">
              {(error as Error)?.message || '请检查网络连接后重试'}
            </p>
          </div>
        )}

        {/* 空状态 */}
        {!isLoading && !isError && data && data.items.length === 0 && (
          <div className="text-center py-20">
            {crawling ? (
              <>
                <Loader2 className="w-14 h-14 mx-auto text-primary/60 mb-4 animate-spin" />
                <p className="text-muted-foreground text-lg">正在搜索最新财经新闻…</p>
                <p className="text-muted-foreground/60 text-sm mt-2">
                  首次抓取通常需要几秒钟，请稍候
                </p>
              </>
            ) : (
              <>
                <SearchX className="w-16 h-16 mx-auto text-muted-foreground/30 mb-4" />
                <p className="text-muted-foreground text-lg">
                  {isSearching ? `未找到与「${searchQuery}」相关的新闻` : '暂无新闻数据'}
                </p>
                <p className="text-muted-foreground/60 text-sm mt-2">
                  {isSearching ? '请尝试其他关键词' : '新闻抓取失败或被限流，刷新页面可重试'}
                </p>
              </>
            )}
          </div>
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
              className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"
            >
              {data.items.map((article, index) => (
                <ArticleCard key={article.id} article={article} index={index} />
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
