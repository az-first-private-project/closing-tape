import { motion } from 'framer-motion'
import { Newspaper, CheckCircle2, Radio } from 'lucide-react'
import { useStats } from '../hooks/useArticles'

function AnimatedNumber({ value }: { value: number }) {
  return (
    <motion.span
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, type: 'spring', stiffness: 100 }}
      className="text-3xl font-bold text-foreground tabular-nums"
    >
      {value.toLocaleString()}
    </motion.span>
  )
}

export default function StatsBar() {
  const { data: stats, isLoading } = useStats()

  if (isLoading) {
    return (
      <div className="border-b border-border bg-card">
        <div className="max-w-7xl mx-auto px-4 py-4">
          <div className="grid grid-cols-3 gap-4">
            {[1, 2, 3].map((i) => (
              <div key={i} className="skeleton h-20 rounded-xl" />
            ))}
          </div>
        </div>
      </div>
    )
  }

  const cards = [
    {
      label: '文章总数',
      value: stats?.total_articles ?? 0,
      icon: Newspaper,
      color: 'text-blue-500',
      bg: 'bg-blue-50 dark:bg-blue-950',
    },
    {
      label: '已摘要',
      value: stats?.processed_count ?? 0,
      icon: CheckCircle2,
      color: 'text-green-500',
      bg: 'bg-green-50 dark:bg-green-950',
    },
    {
      label: '今日新增',
      value: stats?.articles_today ?? 0,
      icon: Radio,
      color: 'text-orange-500',
      bg: 'bg-orange-50 dark:bg-orange-950',
    },
  ]

  return (
    <div className="border-b border-border bg-card">
      <div className="max-w-7xl mx-auto px-4 py-4">
        <div className="grid grid-cols-3 gap-4">
          {cards.map((card) => (
            <motion.div
              key={card.label}
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 0.3 }}
              className="flex items-center gap-3 p-4 rounded-xl border border-border bg-card hover:shadow-md transition-shadow"
            >
              <div className={`p-2 rounded-lg ${card.bg}`}>
                <card.icon className={`w-5 h-5 ${card.color}`} />
              </div>
              <div>
                <p className="text-xs text-muted-foreground">{card.label}</p>
                <AnimatedNumber value={card.value} />
              </div>
            </motion.div>
          ))}
        </div>
      </div>
    </div>
  )
}
