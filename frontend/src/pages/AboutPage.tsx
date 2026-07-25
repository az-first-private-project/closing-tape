import { motion } from 'framer-motion'
import { Link } from 'react-router-dom'

const TECH_STACK = [
  { label: '后端框架', value: 'Python 3.11 + FastAPI (异步)' },
  { label: '数据库', value: 'PostgreSQL 16 + asyncpg' },
  { label: 'ORM', value: 'SQLAlchemy 2.0 (异步)' },
  { label: '爬虫', value: 'Scrapy + Playwright' },
  { label: 'AI 模型', value: 'DeepSeek API (deepseek-chat)' },
  { label: '前端', value: 'React 18 + Vite + TypeScript' },
  { label: 'CSS', value: 'Tailwind CSS v4' },
  { label: '状态管理', value: 'TanStack Query (React Query)' },
  { label: '图表', value: 'Recharts' },
  { label: '动画', value: 'Framer Motion' },
  { label: '部署', value: 'Docker + Docker Compose' },
]

export default function AboutPage() {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
      className="max-w-2xl mx-auto px-4 py-8"
    >
      <h1 className="text-2xl font-bold mb-6">📊 关于 GFNA</h1>

      <div className="space-y-6">
        <section className="bg-card border border-border rounded-xl p-6">
          <h2 className="font-semibold text-lg mb-2">项目简介</h2>
          <p className="text-muted-foreground leading-relaxed">
            GFNA (Global Finance News Aggregator) 是一个全栈 Web 应用，
            自动爬取全球主流财经新闻（Reuters、Bloomberg、CNBC、MarketWatch），
            并利用 DeepSeek AI 将英文长文总结为简洁的中文摘要。
          </p>
        </section>

        <section className="bg-card border border-border rounded-xl p-6">
          <h2 className="font-semibold text-lg mb-4">技术栈 (V2)</h2>
          <div className="space-y-2">
            {TECH_STACK.map((item) => (
              <div key={item.label} className="flex justify-between py-1 border-b border-border/50 last:border-0">
                <span className="text-sm text-muted-foreground">{item.label}</span>
                <span className="text-sm font-medium">{item.value}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="bg-card border border-border rounded-xl p-6">
          <h2 className="font-semibold text-lg mb-2">声明</h2>
          <ul className="text-sm text-muted-foreground space-y-2 list-disc list-inside">
            <li>本项目仅用于课程设计教学目的</li>
            <li>新闻内容版权归原作者所有</li>
            <li>AI 摘要仅供学习参考，不构成投资建议</li>
            <li>请遵守各新闻网站的使用条款</li>
          </ul>
        </section>

        <div className="text-center">
          <Link
            to="/"
            className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
          >
            ← 返回首页
          </Link>
        </div>
      </div>
    </motion.div>
  )
}
