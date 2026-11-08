/**
 * 统一免责声明（U04）
 * =====================
 * 固定文案 + 固定视觉层级；页面可在通用文案后追加具体口径。
 * 全局固定入口在 Footer，页内仅在需要解释具体口径时复用。
 */
import type { ReactNode } from 'react'
import { Info } from 'lucide-react'
import { cn } from '../lib/utils'

export const DISCLAIMER_TEXT =
  '数据与信号仅供研究，不构成投资建议；回测不等于实盘，投资有风险，决策需谨慎。'

interface DisclaimerProps {
  /** 通用免责文案，缺省用 DISCLAIMER_TEXT */
  text?: ReactNode
  /** 追加的页面级口径说明 */
  extra?: ReactNode
  /** 只渲染页面级口径；通用免责由页脚统一承担 */
  extraOnly?: boolean
  className?: string
}

export default function Disclaimer({
  text = DISCLAIMER_TEXT,
  extra,
  extraOnly = false,
  className,
}: DisclaimerProps) {
  if (extraOnly && !extra) return null
  return (
    <aside
      aria-label="免责声明"
      className={cn(
        'flex items-start gap-2 border-t border-border/60 pt-4 text-[12px] leading-relaxed text-muted-foreground',
        className,
      )}
    >
      <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground/70" aria-hidden="true" />
      <p>
        {!extraOnly && (
          <>
            <span className="font-medium text-foreground/80">免责声明</span>
            <span>：{text}</span>
          </>
        )}
        {extra && <span className={cn('block', !extraOnly && 'text-muted-foreground/85')}>{extra}</span>}
      </p>
    </aside>
  )
}
