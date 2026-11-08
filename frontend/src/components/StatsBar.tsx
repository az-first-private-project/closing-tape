import { motion } from 'framer-motion'
import { Newspaper, CheckCircle2, Radio } from 'lucide-react'
import { useStats } from '../hooks/useArticles'

/**
 * 首页刊头（原 StatsBar）
 * ====================
 * 左侧为「刊头」式的品牌区，右侧为三块等宽读数。
 * 数字仍来自 /stats 同一份数据，只是把三张并排小卡改成仪表读数条，
 * 让首屏一眼看到「规模感」而不是三张同质卡片。
 */

function AnimatedNumber({ value }: { value: number }) {
  return (
    <motion.span
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
      className="readout block text-[26px] text-foreground sm:text-[34px] lg:text-[38px]"
    >
      {value.toLocaleString()}
    </motion.span>
  )
}

export default function StatsBar() {
  const { data: stats, isLoading } = useStats()

  if (isLoading) {
    return (
      <div className="border-b border-border/70">
        <div className="mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8">
          <div className="grid items-end gap-10 lg:grid-cols-[1.15fr_1fr]">
            <div className="space-y-4">
              <div className="skeleton h-3 w-40" />
              <div className="skeleton h-12 w-64" />
              <div className="skeleton h-3 w-80 max-w-full" />
            </div>
            <div className="grid grid-cols-3 gap-4">
              {[1, 2, 3].map((i) => (
                <div key={i} className="space-y-2">
                  <div className="skeleton h-2.5 w-14" />
                  <div className="skeleton h-9 w-20" />
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    )
  }

  const total = stats?.total_articles ?? 0
  const processed = stats?.processed_count ?? 0
  const coverage = total > 0 ? Math.round((processed / total) * 100) : 0

  const cards = [
    {
      label: '文章总数',
      note: '全量入库',
      value: total,
      icon: Newspaper,
    },
    {
      label: '已摘要',
      note: total > 0 ? `覆盖 ${coverage}%` : '暂无覆盖',
      value: processed,
      icon: CheckCircle2,
    },
    {
      label: '今日新增',
      note: '今日入库',
      value: stats?.articles_today ?? 0,
      icon: Radio,
    },
  ]

  return (
    <section className="relative overflow-hidden border-b border-border/70">
      {/* 刊头底部渐隐：让内容区干净接入 */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 bottom-0 h-px
          bg-gradient-to-r from-transparent via-border to-transparent"
      />

      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="grid items-end gap-10 py-10 lg:grid-cols-[1.15fr_1fr] lg:py-14">
          {/* ===== 刊头 ===== */}
          <div className="rise">
            <div className="flex items-center gap-2.5">
              <span aria-hidden="true" className="h-px w-7 bg-brass" />
              <span className="eyebrow">Market Intelligence · CN</span>
            </div>

            <h1 className="display-1 mt-5 text-foreground">
              盘后情报<span className="text-brass">.</span>
            </h1>

            <p className="lede mt-4 max-w-lg">
              全球财经新闻聚合与 AI 中文摘要，叠加本地行情库的盘后复盘、板块评分与量化选股信号。
            </p>

            <div className="mt-6 flex flex-wrap items-center gap-x-5 gap-y-2 text-[12px] text-muted-foreground">
              <span className="inline-flex items-center gap-1.5">
                <span aria-hidden="true" className="h-1 w-1 rounded-full bg-brass" />
                新闻聚合 · AI 摘要
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span aria-hidden="true" className="h-1 w-1 rounded-full bg-brass" />
                红涨绿跌 · A 股口径
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span aria-hidden="true" className="h-1 w-1 rounded-full bg-brass" />
                数据每日盘后更新
              </span>
            </div>
          </div>

          {/* ===== 读数条 ===== */}
          <div className="grid grid-cols-3 gap-y-6 border-t border-border/70 pt-6 lg:border-t-0 lg:pt-0">
            {cards.map((card, index) => (
              <div
                key={card.label}
                className={`px-4 first:pl-0 ${index > 0 ? 'border-l border-border/60' : ''}`}
              >
                <div className="flex items-center gap-1.5 text-muted-foreground">
                  <card.icon className="h-3 w-3" strokeWidth={1.8} aria-hidden="true" />
                  <span className="text-[12px] font-medium tracking-wide">{card.label}</span>
                </div>

                <div className="mt-2.5 overflow-hidden">
                  <AnimatedNumber value={card.value} />
                </div>

                <p className="mt-2 font-mono text-[12px] tracking-[0.08em] text-muted-foreground/80">
                  {card.note}
                </p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
