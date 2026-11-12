/**
 * 市场状态仪表盘（V7 T1）
 * ========================
 * 三张卡：阶段判断 / 持仓配置建议 / 一周市场温度计。
 * 数据积累中时只展示温度卡；三块内容都带「为什么」展开区。
 */
import { RefreshCw } from 'lucide-react'
import { ApiError } from '../api/client'
import { useMarketOverview } from '../hooks/useMarketOverview'
import AllocationCard from './AllocationCard'
import RegimeCard from './RegimeCard'
import ThermometerCard from './ThermometerCard'
import { ErrorState } from './StateViews'

function OverviewSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1.15fr_1fr_1fr]">
      {[0, 1, 2].map((index) => (
        <div key={index} className="card space-y-3 p-5">
          <div className="h-3 w-24 rounded bg-muted animate-pulse" />
          <div className="h-7 w-36 rounded bg-muted animate-pulse" />
          <div className="h-20 rounded-lg bg-muted/60 animate-pulse" />
          <div className="h-3 w-full rounded bg-muted/60 animate-pulse" />
          <div className="h-3 w-2/3 rounded bg-muted/60 animate-pulse" />
        </div>
      ))}
    </div>
  )
}

export default function MarketOverview({ tradeDate }: { tradeDate?: string }) {
  const overview = useMarketOverview(tradeDate)
  const data = overview.data

  if (overview.isLoading && !data) {
    return <OverviewSkeleton />
  }

  if (!data) {
    const notReady = overview.error instanceof ApiError && overview.error.code === 'DATA_NOT_READY'
    return (
      <ErrorState
        title={notReady ? '该交易日还没有市场状态数据' : '市场状态加载失败'}
        error={overview.error}
        onRetry={() => overview.refetch()}
      />
    )
  }

  const { meta, regime, allocation, thermometer, evidence, caveats } = data
  const accumulating = !regime || !allocation

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <span className="eyebrow">Market State</span>
          <div className="mt-1.5 flex flex-wrap items-baseline gap-2">
            <h2 className="text-[15px] font-semibold tracking-[-0.01em]">市场状态与配置</h2>
            <span className="num text-[11px] text-muted-foreground">
              {meta.index_used} · {meta.trade_date}
            </span>
            {meta.data_quality === 'partial' && (
              <span className="rounded-full border border-warning/30 bg-warning/[0.06] px-2 py-0.5 text-[10px] text-warning">
                部分数据未更新
              </span>
            )}
            {meta.stale_fields.length > 0 && (
              <span className="text-[10px] text-muted-foreground">
                缺：{meta.stale_fields.join('、')}
              </span>
            )}
          </div>
        </div>
        <button
          type="button"
          onClick={() => overview.refetch()}
          disabled={overview.isFetching}
          className="btn btn-sm h-8"
          title="重新加载市场状态"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${overview.isFetching ? 'animate-spin' : ''}`} />
          刷新{overview.isFetching ? '中…' : ''}
        </button>
      </div>

      {accumulating ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="card flex flex-col justify-center p-5">
            <span className="eyebrow">Accumulating</span>
            <h3 className="mt-2 text-[17px] font-semibold">市场状态数据积累中</h3>
            <p className="mt-2 text-[12px] leading-relaxed text-muted-foreground">
              阶段判断需要至少 250 个交易日的历史分位；当前数据还不够，温度计仍可正常查看。
              每日管线运行后会自动补齐。
            </p>
          </div>
          <ThermometerCard thermometer={thermometer} evidence={evidence} />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1.15fr_1fr_1fr]">
          <RegimeCard regime={regime!} meta={meta} evidence={evidence} />
          <AllocationCard allocation={allocation!} evidence={evidence} />
          <ThermometerCard thermometer={thermometer} evidence={evidence} />
        </div>
      )}

      <div className="space-y-1">
        {caveats.map((item) => (
          <p key={item} className="text-[11px] leading-relaxed text-muted-foreground">
            · {item}
          </p>
        ))}
        <p className="pt-1 text-[11px] leading-relaxed text-muted-foreground">
          {meta.disclaimer}
        </p>
      </div>
    </section>
  )
}
