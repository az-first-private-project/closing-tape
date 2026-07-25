/**
 * 数据仪表盘页面 - Phase 3
 * 使用 Recharts 实现数据可视化：
 * - 来源分布饼图
 * - 每日新增趋势面积图
 * - AI 处理进度环形图
 * - 热门关键词词云
 */
import { motion } from 'framer-motion'
import { Loader2 } from 'lucide-react'
import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer,
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Legend,
  BarChart, Bar,
} from 'recharts'
import {
  useDailyTrend,
  useSourceDistribution,
  useKeywords,
  useProcessingStatus,
} from '../hooks/useArticles'
import { getSourceColor } from '../lib/utils'

// ==================== 来源分布饼图 ====================
function SourcePieChart() {
  const { data, isLoading } = useSourceDistribution()

  if (isLoading) return <ChartLoader />
  if (!data?.data.length) return <EmptyState text="暂无数据" />

  return (
    <div className="bg-card border border-border rounded-xl p-6">
      <h3 className="font-semibold mb-4">📊 新闻来源分布</h3>
      <ResponsiveContainer width="100%" height={300}>
        <PieChart>
          <Pie
            data={data.data}
            cx="50%"
            cy="50%"
            innerRadius={60}
            outerRadius={100}
            paddingAngle={2}
            dataKey="count"
            nameKey="name"
            label={({ name, percentage }) => `${name} ${percentage}%`}
          >
            {data.data.map((entry) => (
              <Cell key={entry.name} fill={getSourceColor(entry.name)} />
            ))}
          </Pie>
          <Tooltip />
        </PieChart>
      </ResponsiveContainer>
    </div>
  )
}

// ==================== 每日新增趋势图 ====================
function DailyTrendChart() {
  const { data, isLoading } = useDailyTrend(30)

  if (isLoading) return <ChartLoader />
  if (!data?.data.length) return <EmptyState text="暂无数据" />

  return (
    <div className="bg-card border border-border rounded-xl p-6">
      <h3 className="font-semibold mb-4">📈 每日新增趋势 (近30天)</h3>
      <ResponsiveContainer width="100%" height={300}>
        <AreaChart data={data.data}>
          <defs>
            <linearGradient id="colorCount" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#1a73e8" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#1a73e8" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 12 }}
            tickFormatter={(d: string) => d.slice(5)} // MM-DD
          />
          <YAxis tick={{ fontSize: 12 }} />
          <Tooltip />
          <Area
            type="monotone"
            dataKey="count"
            stroke="#1a73e8"
            fill="url(#colorCount)"
            strokeWidth={2}
            name="新增文章"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

// ==================== AI 处理进度 ====================
function ProcessingProgressCard() {
  const { data, isLoading } = useProcessingStatus()

  if (isLoading) return <ChartLoader />

  const percentage = data?.percentage ?? 0

  return (
    <div className="bg-card border border-border rounded-xl p-6">
      <h3 className="font-semibold mb-4">🤖 AI 处理进度</h3>
      <div className="flex items-center justify-center">
        <div className="relative w-40 h-40">
          {/* 环形进度条 (纯 CSS) */}
          <svg className="w-full h-full -rotate-90" viewBox="0 0 100 100">
            <circle
              cx="50" cy="50" r="42"
              fill="none"
              stroke="var(--color-border)"
              strokeWidth="8"
            />
            <circle
              cx="50" cy="50" r="42"
              fill="none"
              stroke="#0d904f"
              strokeWidth="8"
              strokeLinecap="round"
              strokeDasharray={`${percentage * 2.64} 264`}
            />
          </svg>
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-2xl font-bold text-foreground">{percentage}%</span>
          </div>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2 mt-4 text-center text-sm">
        <div>
          <p className="text-muted-foreground">总计</p>
          <p className="font-semibold">{data?.total ?? 0}</p>
        </div>
        <div>
          <p className="text-muted-foreground">已完成</p>
          <p className="font-semibold text-green-600">{data?.processed ?? 0}</p>
        </div>
        <div>
          <p className="text-muted-foreground">未处理</p>
          <p className="font-semibold text-yellow-600">{data?.unprocessed ?? 0}</p>
        </div>
      </div>
    </div>
  )
}

// ==================== 热门关键词 ====================
function KeywordsChart() {
  const { data, isLoading } = useKeywords()

  if (isLoading) return <ChartLoader />
  if (!data?.keywords.length) return <EmptyState text="暂无数据" />

  const top10 = data.keywords.slice(0, 10)

  return (
    <div className="bg-card border border-border rounded-xl p-6">
      <h3 className="font-semibold mb-4">🔑 热门关键词 (Top 10)</h3>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={top10} layout="vertical" margin={{ left: 40 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
          <XAxis type="number" tick={{ fontSize: 12 }} />
          <YAxis
            type="category"
            dataKey="word"
            tick={{ fontSize: 12 }}
            width={80}
          />
          <Tooltip />
          <Bar dataKey="count" fill="#1a73e8" radius={[0, 4, 4, 0]} name="出现次数" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

// ==================== 辅助组件 ====================
function ChartLoader() {
  return (
    <div className="bg-card border border-border rounded-xl p-6 flex items-center justify-center h-[300px]">
      <Loader2 className="w-6 h-6 animate-spin text-primary" />
    </div>
  )
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="bg-card border border-border rounded-xl p-6 flex items-center justify-center h-[300px] text-muted-foreground">
      {text}
    </div>
  )
}

// ==================== 仪表盘主页 ====================
export default function DashboardPage() {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
      className="max-w-7xl mx-auto px-4 py-8"
    >
      <h1 className="text-2xl font-bold mb-6">📊 数据仪表盘</h1>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <SourcePieChart />
        <ProcessingProgressCard />
        <DailyTrendChart />
        <KeywordsChart />
      </div>
    </motion.div>
  )
}
