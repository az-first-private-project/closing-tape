/**
 * 信号样本状态（P1-2，纯函数）
 * ============================
 * 统一天梯与共识榜的样本口径文案：
 *  - 有效样本 = 已成熟、T+1 可成交且收益非空；
 *  - 已成熟 = 总信号 - 未成熟（含不可成交/缺数据）；
 *  - 有效样本 < 30 视为低样本，与后端 `MIN_SAMPLE` 一致。
 */
import type { LadderHorizonStats } from '../types/algo'

export const MIN_SAMPLE = 30

export type SampleTone = 'ok' | 'low' | 'pending' | 'empty' | 'unavailable'

export interface SampleSummary {
  tone: SampleTone
  label: string
  detail: string
}

type SampleStats = Pick<
  LadderHorizonStats,
  'sample_signals' | 'total_signals' | 'immature' | 'low_sample'
>

export function summarizeSample(
  stats: SampleStats | null | undefined,
): SampleSummary {
  if (!stats) {
    return {
      tone: 'unavailable',
      label: '样本统计不可用',
      detail: '未取得该策略的历史样本',
    }
  }

  const total = Math.max(0, stats.total_signals || 0)
  const sample = Math.max(0, stats.sample_signals || 0)
  const immature = Math.min(total, Math.max(0, stats.immature || 0))
  const mature = total - immature

  if (total === 0) {
    return {
      tone: 'empty',
      label: '窗口内无信号',
      detail: '近 60 日没有该策略的非回测信号',
    }
  }
  if (sample === 0) {
    return immature > 0
      ? {
          tone: 'pending',
          label: '待成熟',
          detail: `0 条有效样本 · ${immature} 条未成熟`,
        }
      : {
          tone: 'empty',
          label: '无有效样本',
          detail: `0 条有效样本 · 已成熟 ${mature} 条但不可成交或缺数据`,
        }
  }

  const low = stats.low_sample || sample < MIN_SAMPLE
  return {
    tone: low ? 'low' : 'ok',
    label: low ? '低样本' : '样本充足',
    detail: `有效样本 ${sample} · 已成熟 ${mature}/${total}`,
  }
}
