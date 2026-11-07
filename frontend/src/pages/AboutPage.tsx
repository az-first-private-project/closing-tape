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
      className="mx-auto max-w-3xl px-4 py-10 sm:px-6"
    >
      {/* 刊头 */}
      <div className="rise">
        <div className="flex items-center gap-2.5">
          <span aria-hidden="true" className="h-px w-6 bg-brass" />
          <span className="eyebrow">About · V5</span>
        </div>
        <h1 className="page-title mt-2.5">关于 GFNA</h1>
        <p className="lede mt-3 max-w-xl">
          一个只读本地数据库的 A 股盘后研究终端：新闻聚合、行情复盘、量化选股与回测。
        </p>
      </div>

      <div className="mt-8 space-y-5">
        <section className="card p-5 sm:p-6">
          <div className="mb-3 flex items-baseline gap-2">
            <span className="section-index">01</span>
            <h2 className="text-[14px] font-semibold">项目简介</h2>
          </div>
          <p className="text-[13px] leading-[1.9] text-muted-foreground">
            GFNA (Global Finance News Aggregator) 已升级为 V5：在保留全球财经新闻聚合与 AI 中文摘要的基础上，
            补齐了 A 股全市场行情库、技术指标与因子评分、参数化选股策略（A3/A4 价值策略 + B1–B5 短线策略）以及回测能力。
            所有分析只读本地数据库，页面通过「数据新鲜度」标注每个数据集的可信度。
          </p>
        </section>

        <section className="card p-5 sm:p-6">
          <div className="mb-4 flex items-baseline gap-2">
            <span className="section-index">02</span>
            <h2 className="text-[14px] font-semibold">技术栈</h2>
            <span className="font-mono text-[10px] tracking-[0.14em] text-muted-foreground">V5</span>
          </div>
          <dl className="grid grid-cols-1 gap-x-10 sm:grid-cols-2">
            {TECH_STACK.map((item) => (
              <div
                key={item.label}
                className="flex items-baseline gap-3 border-b border-border/50 py-2.5"
              >
                <dt className="w-[4.5rem] shrink-0 text-[11px] text-muted-foreground">{item.label}</dt>
                <dd className="text-[11.5px] font-medium leading-relaxed">{item.value}</dd>
              </div>
            ))}
          </dl>
        </section>

        <section className="card p-5 sm:p-6">
          <div className="mb-3 flex items-baseline gap-2">
            <span className="section-index">03</span>
            <h2 className="text-[14px] font-semibold">数据与风险声明</h2>
          </div>
          <ul className="space-y-2.5">
            {[
              '本项目仅用于课程设计与个人研究，数据版权归原始数据源所有',
              '行情数据来自公开接口（AKShare 等二次封装），不保证完整、及时、准确',
              '选股信号、因子评分与回测结果均不构成投资建议，回测 ≠ 实盘',
              '不实现任何绕过反爬、验证码识别或代理池轮换，请遵守目标站点条款',
            ].map((text) => (
              <li key={text} className="flex items-start gap-2.5 text-[12.5px] leading-relaxed text-muted-foreground">
                <span aria-hidden="true" className="mt-[0.55rem] h-1 w-1 shrink-0 rounded-full bg-brass/70" />
                {text}
              </li>
            ))}
          </ul>
        </section>
      </div>

      <div className="mt-8 flex justify-center">
        <Link to="/" className="group inline-flex items-center gap-1.5 text-[12px] text-muted-foreground transition-colors hover:text-brass">
          <span aria-hidden="true" className="transition-transform duration-300 group-hover:-translate-x-0.5">←</span>
          返回首页
        </Link>
      </div>
    </motion.div>
  )
}
