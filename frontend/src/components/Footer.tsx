import { Link } from 'react-router-dom'

/**
 * 页脚：编辑部落款式的三段结构
 * —— 品牌 / 数据来源 / 免责声明，信息不变，层级重新组织。
 */
export default function Footer() {
  return (
    <footer className="mt-20 border-t border-border/70">
      <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6 lg:px-8">
        <div className="flex flex-wrap items-end justify-between gap-8">
          {/* 品牌落款 */}
          <div>
            <div className="flex items-center gap-2.5">
              <span aria-hidden="true" className="h-1.5 w-1.5 rotate-45 border border-brass" />
              <span className="font-mono text-[11px] font-semibold tracking-[0.28em] text-foreground">
                GFNA
              </span>
              <span className="kicker">A 股盘后终端</span>
            </div>
            <p className="mt-3 max-w-md text-xs leading-relaxed text-muted-foreground">
              行情来自本地行情库（公开数据源采集）
            </p>
          </div>

          {/* 数据来源 */}
          <div className="text-right">
            <p className="eyebrow">Data Sources</p>
            <p className="mt-2 text-xs text-muted-foreground">
              新浪财经 / 同花顺 / 东方财富 / 华尔街见闻
            </p>
          </div>
        </div>

        <div className="mt-8 flex flex-wrap items-center justify-between gap-3 border-t border-border/60 pt-5">
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            GFNA © 2026 · AI 摘要与日报由 DeepSeek 提供 · 数据与信号仅供研究，不构成投资建议
          </p>
          <Link
            to="/about"
            className="group inline-flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground
              transition-colors hover:text-brass"
          >
            关于项目
            <span
              aria-hidden="true"
              className="transition-transform duration-300 group-hover:translate-x-0.5"
            >
              →
            </span>
          </Link>
        </div>
      </div>
    </footer>
  )
}
