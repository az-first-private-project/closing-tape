import { motion } from 'framer-motion'
import { Link } from 'react-router-dom'

const TECH_STACK = [
  { label: '后端框架', value: 'Python 3.11+ + FastAPI (异步)' },
  { label: '数据库', value: 'PostgreSQL 16 (行情 + 新闻 + 任务元信息)' },
  { label: 'ORM', value: 'SQLAlchemy 2.0 (异步)' },
  { label: '行情采集', value: 'AKShare 主采集 + Baostock 兜底' },
  { label: '新闻采集', value: 'Scrapy + Playwright' },
  { label: 'AI 模型', value: 'DeepSeek API (日报 / 摘要)' },
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
      <h1 className="text-[28px] font-bold mb-6">📊 关于 GFNA</h1>

      <div className="space-y-6">
        <section className="card p-6">
          <h2 className="font-semibold text-lg mb-2">项目简介</h2>
          <p className="text-muted-foreground leading-relaxed">
            GFNA (Global Finance News Aggregator) 已升级为 V5：在保留全球财经新闻聚合与 AI 中文摘要的基础上，
            补齐了 A 股全市场行情库、技术指标与因子评分、参数化选股策略（A3/A4 价值策略 + B1–B5 短线策略）以及回测能力。
            所有分析只读本地数据库，页面通过「数据新鲜度」标注每个数据集的可信度。
          </p>
        </section>

        <section className="card p-6">
          <h2 className="font-semibold text-lg mb-4">技术栈 (V5)</h2>
          <div className="space-y-2">
            {TECH_STACK.map((item) => (
              <div key={item.label} className="flex justify-between py-1 border-b border-border/50 last:border-0">
                <span className="text-sm text-muted-foreground">{item.label}</span>
                <span className="text-sm font-medium text-right">{item.value}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="card p-6">
          <h2 className="font-semibold text-lg mb-2">数据与风险声明</h2>
          <ul className="text-sm text-muted-foreground space-y-2 list-disc list-inside">
            <li>本项目仅用于课程设计与个人研究，数据版权归原始数据源所有</li>
            <li>行情数据来自公开接口（AKShare 等二次封装），不保证完整、及时、准确</li>
            <li>选股信号、因子评分与回测结果均不构成投资建议，回测 ≠ 实盘</li>
            <li>不实现任何绕过反爬、验证码识别或代理池轮换，请遵守目标站点条款</li>
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
