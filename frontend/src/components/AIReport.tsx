/**
 * AI 复盘报告（契约 3.1 report）
 * ================================
 * 结构化渲染后端返回的 Markdown 文本（按行解析为 React 元素，不使用 dangerouslySetInnerHTML）。
 * report 为 null 表示尚未生成。
 */
import { motion } from 'framer-motion'
import { Bot, TriangleAlert } from 'lucide-react'
import type { DailyReport } from '../types/market'
import { fmtDateTime } from '../lib/format'
import Markdown from './Markdown'

interface Props {
  report: DailyReport | null
}

export default function AIReport({ report }: Props) {
  if (!report || !report.content) {
    return (
      <div className="card p-6">
        <div className="flex items-center gap-2 mb-4">
          <Bot className="w-5 h-5 text-primary" />
          <h3 className="font-semibold">🤖 AI 复盘报告</h3>
        </div>
        <p className="text-muted-foreground text-sm">
          AI 报告将在每日盘后自动生成。若已过收盘时间仍无报告，请到「任务监控」查看采集/评分/日报任务状态。
        </p>
      </div>
    )
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="card p-6"
    >
      {/* 标题 + 元信息 */}
      <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
        <div className="flex items-center gap-2">
          <Bot className="w-5 h-5 text-primary" />
          <h3 className="font-semibold">🤖 AI 复盘报告</h3>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
          {report.model && (
            <span className="px-2 py-0.5 rounded-full border border-border font-mono">{report.model}</span>
          )}
          {report.created_at && (
            <span className="num">生成于 {fmtDateTime(report.created_at)}</span>
          )}
          {report.is_fallback && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border border-warning/40 bg-warning/10 text-warning">
              <TriangleAlert className="w-3 h-3" />
              降级模板
            </span>
          )}
        </div>
      </div>

      {/* 正文：安全的轻量 Markdown 渲染 */}
      <div className="text-sm leading-relaxed">
        <Markdown text={report.content} />
      </div>

      <div className="mt-4 pt-4 border-t border-border">
        <p className="text-xs text-muted-foreground">
          ⚠️ 此报告由 AI 基于量化模型自动生成，引用数据请以页面展示为准，不构成投资建议。投资有风险，入市需谨慎。
        </p>
      </div>
    </motion.div>
  )
}
