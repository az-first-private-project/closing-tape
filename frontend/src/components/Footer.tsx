import { Link } from 'react-router-dom'

export default function Footer() {
  return (
    <footer className="border-t border-border bg-card">
      <div className="max-w-7xl mx-auto px-4 py-6 text-center text-sm text-muted-foreground">
        <p>GFNA © 2026 · 行情来自本地行情库（公开数据源采集）· 新闻来自新浪财经 / 同花顺 / 东方财富 / 华尔街见闻</p>
        <p className="mt-1">
          AI 摘要与日报由 DeepSeek 提供 · 数据与信号仅供研究，不构成投资建议 ·{' '}
          <Link to="/about" className="text-primary hover:underline">
            关于项目
          </Link>
        </p>
      </div>
    </footer>
  )
}
