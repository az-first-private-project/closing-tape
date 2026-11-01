/**
 * 全局导航与命令面板共享的路由清单（V6 T15）
 * ----------------------------------------------
 * Navbar 与 CommandPalette 共用一份配置，避免两处页面列表漂移。
 * `keywords` 只参与命令面板检索，不参与展示。
 */
import {
  Activity,
  BarChart3,
  Brain,
  Info,
  LineChart,
  Star,
  TrendingUp,
  type LucideIcon,
} from 'lucide-react'

export interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  end?: boolean
  keywords?: string[]
}

export const NAV_ITEMS: NavItem[] = [
  { to: '/', label: '首页', icon: BarChart3, end: true, keywords: ['新闻', '资讯'] },
  { to: '/dashboard', label: '盘后分析', icon: TrendingUp, keywords: ['大盘', '温度', '板块'] },
  { to: '/algo-center', label: '选股中心', icon: Brain, keywords: ['策略', '天梯', '共识', '回测'] },
  { to: '/paper', label: '纸面跟踪', icon: LineChart, keywords: ['组合', '净值'] },
  { to: '/watchlist', label: '自选', icon: Star, keywords: ['自选股', '提醒'] },
  { to: '/jobs', label: '任务监控', icon: Activity, keywords: ['调度', '数据'] },
  { to: '/about', label: '关于', icon: Info, keywords: ['说明'] },
]

/** Navbar 键位提示与全局 Ctrl/Cmd+K 共用的自定义事件名 */
export const PALETTE_OPEN_EVENT = 'gfna:open-command-palette'
