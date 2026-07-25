import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { Loader2, SearchX } from 'lucide-react'
import StatsBar from '../components/StatsBar'
import FilterTabs from '../components/FilterTabs'
import ArticleCard from '../components/ArticleCard'
import PaginationBar from '../components/PaginationBar'
import { useArticles, useSearch } from '../hooks/useArticles'

export default function HomePage() {
  const [searchParams] = useSearchParams()
  const searchQuery = searchParams.get('q') || ''

  const [page, setPage] = useState(1)
  const [sourceId, setSourceId] = useState<number | null>(null)

  const articlesQuery = useArticles(page, sourceId)
  const searchQueryHook = useSearch(searchQuery, page)

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
            <p className="text-destructive text-lg">⚠️ 加载失败</p>
            <p className="text-muted-foreground text-sm mt-2">
              {(error as Error)?.message || '请检查网络连接后重试'}
            </p>
          </div>
        )}

        {/* 空状态 */}
        {!isLoading && !isError && data && data.items.length === 0 && (
          <div className="text-center py-20">
            <SearchX className="w-16 h-16 mx-auto text-muted-foreground/30 mb-4" />
            <p className="text-muted-foreground text-lg">
              {isSearching ? `未找到与「${searchQuery}」相关的新闻` : '📭 暂无新闻数据'}
            </p>
            <p className="text-muted-foreground/60 text-sm mt-2">
              {isSearching ? '请尝试其他关键词' : '请等待爬虫抓取新闻后刷新页面'}
            </p>
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
