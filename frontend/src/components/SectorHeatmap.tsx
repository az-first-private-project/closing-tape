/**
 * 板块涨跌热力图（契约 3.1 heatmap_data）
 * 纯 CSS Grid 方格，颜色由 lib/color.ts 的连续色阶插值；pct_chg 为 null 时显示灰色「—」。
 * 颜色遵循 A 股口径：红涨绿跌；↑/↓ 与正负号作为颜色之外的冗余语义。
 */
import { motion } from 'framer-motion'
import type { HeatmapItem } from '../types/market'
import { fmtPct, trendGlyph } from '../lib/format'
import { trendHeat } from '../lib/color'

interface Props {
  data: HeatmapItem[]
}

export default function SectorHeatmap({ data }: Props) {
  if (!data.length) {
    return (
      <div className="card p-6 flex items-center justify-center h-[300px] text-muted-foreground text-sm">
        暂无板块行情数据
      </div>
    )
  }

  return (
    <div className="card p-4">
      <h3 className="font-semibold mb-3">🔥 板块涨跌热力图</h3>
      <div className="grid grid-cols-5 sm:grid-cols-8 md:grid-cols-10 gap-1">
        {data.map((item, index) => {
          const heat = trendHeat(item.pct_chg)
          const glyph = item.pct_chg === null ? '' : trendGlyph(item.pct_chg)
          return (
            <motion.div
              key={item.sector_code}
              initial={{ opacity: 0, scale: 0.8 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: index * 0.01, duration: 0.2 }}
              className="rounded-md p-1.5 text-center cursor-default hover:ring-2 hover:ring-offset-1 hover:ring-primary transition-shadow"
              style={{ backgroundColor: heat.background, color: heat.color }}
              title={`${item.sector_name}: ${fmtPct(item.pct_chg)}`}
              aria-label={`${item.sector_name} ${fmtPct(item.pct_chg)}`}
            >
              <p className="text-[11px] leading-tight truncate">{item.sector_name}</p>
              <p className="text-xs font-semibold num">
                {glyph}
                {fmtPct(item.pct_chg, 1)}
              </p>
            </motion.div>
          )
        })}
      </div>
    </div>
  )
}
