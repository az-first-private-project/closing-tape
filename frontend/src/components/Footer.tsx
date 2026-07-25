import { Link } from 'react-router-dom'

export default function Footer() {
  return (
    <footer className="border-t border-border bg-card">
      <div className="max-w-7xl mx-auto px-4 py-6 text-center text-sm text-muted-foreground">
        <p>GFNA © 2025 | 数据来源: Reuters, Bloomberg, CNBC, MarketWatch</p>
        <p className="mt-1">
          AI 摘要由 DeepSeek 提供 |{' '}
          <Link to="/about" className="text-primary hover:underline">
            关于项目
          </Link>
        </p>
      </div>
    </footer>
  )
}
