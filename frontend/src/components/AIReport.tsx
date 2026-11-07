/**
 * AI 复盘报告（契约 3.1 report）
 * ================================
 * 结构化渲染后端返回的 Markdown 文本（按行解析为 React 元素，不使用 dangerouslySetInnerHTML）。
 * report 为 null 表示尚未生成。
 *
 * 视觉：像一份研报——铜色开启线、刊头元信息、正文独立成栏。
 */
import { motion } from 'framer-motion'
import { TriangleAlert } from 'lucide-react'
import type { DailyReport } from '../types/market'
import { fmtDateTime } from '../lib/format'
import Markdown from './Markdown'

interface Props {
  report: DailyReport | null
}

export default function AIReport({ report }: Props) {
  if (!report || !report.content) {
    return (
      <div className="card p-5 sm:p-6">
        <div className="flex items-center gap-2">
          <span aria-hidden="true" className="h-px w-5 bg-brass/70" />
          <span className="eyebrow">AI Review</span>
        </div>
        <h3 className="mt-2 text-[16px] font-semibold">AI 复盘报告</h3>
        <p className="mt-2 max-w-2xl text-[12px] leading-relaxed text-muted-foreground">
          AI 报告将在每日盘后自动生成。若已过收盘时间仍无报告，请到「任务监控」查看采集/评分/日报任务状态。
        </p>
      </div>
    )
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
      className="card relative overflow-hidden p-5 sm:p-6"
    >
      {/* 研报开启线 */}
      <span
        aria-hidden="true"
        className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-brass/70 via-brass/25 to-transparent"
      />

      {/* 刊头 + 元信息 */}
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <span className="eyebrow eyebrow-brass">AI Review · {report.model ?? 'DeepSeek'}</span>
          <h3 className="mt-1.5 text-[17px] font-semibold tracking-[-0.015em]">AI 复盘报告</h3>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
          {report.model && (
            <span className="rounded border border-border/70 bg-muted/40 px-2 py-0.5 font-mono tracking-wide">
              {report.model}
            </span>
          )}
          {report.created_at && (
            <span className="num">生成于 {fmtDateTime(report.created_at)}</span>
          )}
          {report.is_fallback && (
            <span className="inline-flex items-center gap-1 rounded-full border border-warning/40 bg-warning/[0.08] px-2 py-0.5 text-warning">
              <TriangleAlert className="h-3 w-3" />
              降级模板
            </span>
          )}
        </div>
      </div>

      {/* 正文：安全的轻量 Markdown 渲染 */}
      <div className="border-t border-border/60 pt-4">
        <Markdown text={report.content} />
      </div>

      <div className="mt-5 flex items-start gap-2 border-t border-border/60 pt-3.5">
        <TriangleAlert className="mt-0.5 h-3 w-3 shrink-0 text-warning/80" aria-hidden="true" />
        <p className="text-[10px] leading-relaxed text-muted-foreground">
          此报告由 AI 基于量化模型自动生成，引用数据请以页面展示为准，不构成投资建议。投资有风险，入市需谨慎。
        </p>
      </div>
    </motion.div>
  )
}
