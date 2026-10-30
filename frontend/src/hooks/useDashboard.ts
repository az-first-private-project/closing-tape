/**
 * 盘后分析数据 Hook
 * ==================
 * - useDashboard：按 trade_date 拉取仪表盘（缺省 = 最新交易日）；
 * - useTradeCalendar：拉取最近 N 天交易日历，供日期选择器使用。
 */
import { useQuery } from '@tanstack/react-query'
import { fetchDashboard } from '../api/market'
import { fetchTradeCalendar } from '../api/meta'
import type { DashboardData } from '../types/market'

/** 本地日期 → YYYY-MM-DD */
function localDateString(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** 仪表盘（最新交易日或指定交易日） */
export function useDashboard(tradeDate?: string) {
  return useQuery<DashboardData>({
    queryKey: ['dashboard', tradeDate || 'latest'],
    queryFn: () => fetchDashboard(tradeDate),
    staleTime: 5 * 60 * 1000,
    // 只有「最新交易日」视图需要定时刷新，回看历史时不必轮询
    refetchInterval: tradeDate ? false : 10 * 60 * 1000,
  })
}

/** 最近 N 天交易日历（用于历史回看） */
export function useTradeCalendar(days = 180) {
  const end = new Date()
  const start = new Date()
  start.setDate(start.getDate() - days)
  return useQuery({
    queryKey: ['trade-calendar', days],
    queryFn: () => fetchTradeCalendar(localDateString(start), localDateString(end)),
    staleTime: 6 * 60 * 60 * 1000,
  })
}
