/**
 * 泛型数据表格
 * =============
 * - 列定义驱动，支持本地排序、行点击、空态、底部统计；
 * - 传入 csvFilename 时显示「导出 CSV」按钮（纯前端导出当前数据）；
 * - 服务端 CSV 导出（如 /algo/{code}/export.csv）由页面单独调用 api/client 的 downloadCsv。
 */
import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronDown, ChevronRight, Download } from 'lucide-react'
import { downloadText, toCsv } from '../lib/csv'
import RangeNotice from './RangeNotice'

/** 单元格原始值（排序与 CSV 导出用） */
export type DataTableValue = string | number | boolean | null | undefined

/** 排序状态（供页面持久化后回传） */
export interface DataTableSortState {
  key: string
  direction: 'asc' | 'desc'
}

export interface DataTableColumn<T> {
  key: string
  header: string
  align?: 'left' | 'right' | 'center'
  sortable?: boolean
  /** 排序/CSV 取值；缺省时尝试读 row[key] */
  value?: (row: T) => DataTableValue
  /** 自定义渲染；缺省渲染 value */
  render?: (row: T, index: number) => ReactNode
  className?: string
}

interface DataTableProps<T> {
  columns: DataTableColumn<T>[]
  rows: T[]
  rowKey: (row: T, index: number) => string
  onRowClick?: (row: T, rowKey: string) => void
  /** null = 明确不排序；undefined = 使用调用方默认或表格自然顺序 */
  initialSort?: DataTableSortState | null
  /** 恢复展开行 / 选中行 / 表格滚动（仅在挂载时生效，配合 key 切换 run） */
  initialExpandedKey?: string | null
  initialActiveKey?: string | null
  initialScrollTop?: number | null
  onSortChange?: (sort: DataTableSortState | null) => void
  onExpandedChange?: (expandedKey: string | null) => void
  onScrollTopChange?: (scrollTop: number) => void
  csvFilename?: string
  emptyText?: string
  footer?: ReactNode
  /** 表格最大高度（固定表头滚动） */
  maxHeight?: string
  /** 行展开内容；不传则不显示展开列 */
  renderExpanded?: (row: T, index: number) => ReactNode
  /** 服务端分页总数；与 rows.length 不同时表头显示「已显示 N / 共 M」 */
  total?: number
  /** 范围说明补充（如「第 1 / 3 页」） */
  rangeNote?: string
}

/** 读取排序值 */
function resolveValue<T>(row: T, column: DataTableColumn<T>): DataTableValue {
  if (column.value) return column.value(row)
  if (typeof row === 'object' && row !== null) {
    const record = row as Record<string, unknown>
    const value = record[column.key]
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return value
  }
  return undefined
}

/** 单元格值 → 展示文本（CSV 用） */
function valueToText(value: DataTableValue): string {
  if (value === null || value === undefined) return ''
  return String(value)
}

/** 可比较排序（null 永远排在最后） */
function compareValues(a: DataTableValue, b: DataTableValue): number {
  if (a === null || a === undefined) return 1
  if (b === null || b === undefined) return -1
  if (typeof a === 'number' && typeof b === 'number') return a - b
  if (typeof a === 'boolean' && typeof b === 'boolean') return Number(a) - Number(b)
  return String(a).localeCompare(String(b), 'zh-CN')
}

const ALIGN_CLASS: Record<'left' | 'right' | 'center', string> = {
  left: 'text-left',
  right: 'text-right',
  center: 'text-center',
}

export default function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  initialSort,
  initialExpandedKey,
  initialActiveKey,
  initialScrollTop,
  onSortChange,
  onExpandedChange,
  onScrollTopChange,
  csvFilename,
  emptyText = '暂无数据',
  footer,
  maxHeight,
  renderExpanded,
  total,
  rangeNote,
}: DataTableProps<T>) {
  const [sort, setSort] = useState<DataTableSortState | null>(initialSort ?? null)
  const [expandedKey, setExpandedKey] = useState<string | null>(initialExpandedKey ?? null)
  const [activeKey, setActiveKey] = useState<string | null>(initialActiveKey ?? null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const scrollRestored = useRef(false)
  const lastScrollTop = useRef<number | null>(null)

  /** 恢复表格内部滚动位置：只在挂载后执行一次，避免排序/展开时抢滚动 */
  useLayoutEffect(() => {
    if (scrollRestored.current) return
    scrollRestored.current = true
    const element = scrollRef.current
    if (!element || initialScrollTop === null || initialScrollTop === undefined) return
    if (initialScrollTop > 0) element.scrollTop = initialScrollTop
  }, [initialScrollTop])


  /** 排序后的行（rows 顺序不变时计算结果稳定） */
  const sortedRows = useMemo(() => {
    if (!sort) return rows
    const column = columns.find((item) => item.key === sort.key)
    if (!column) return rows
    const factor = sort.direction === 'asc' ? 1 : -1
    return [...rows].sort((left, right) => compareValues(resolveValue(left, column), resolveValue(right, column)) * factor)
  }, [rows, columns, sort])

  /** 点击表头切换排序（asc → desc → 不排序） */
  const handleSort = (column: DataTableColumn<T>) => {
    if (!column.sortable) return
    let next: DataTableSortState | null
    if (!sort || sort.key !== column.key) next = { key: column.key, direction: 'asc' }
    else if (sort.direction === 'asc') next = { key: column.key, direction: 'desc' }
    else next = null
    setSort(next)
    onSortChange?.(next)
  }

  const handleExpandedChange = (key: string | null) => {
    setExpandedKey(key)
    onExpandedChange?.(key)
  }

  /** 表格内部滚动：同步上报（页面侧只更新 ref + 防抖落盘），离开前可取到最新值 */
  const handleScroll = () => {
    if (!onScrollTopChange) return
    const top = scrollRef.current?.scrollTop ?? 0
    if (lastScrollTop.current === top) return
    lastScrollTop.current = top
    onScrollTopChange(top)
  }

  /** 导出当前表格数据为 CSV */
  const handleExport = () => {
    const headers = columns.map((column) => column.header)
    const body = sortedRows.map((row) =>
      columns.map((column) => valueToText(resolveValue(row, column))),
    )
    downloadText(toCsv(headers, body), csvFilename ?? 'table.csv')
  }

  return (
    <div className="card overflow-hidden">
      <div className="flex items-center justify-between gap-2 border-b border-border/70 px-4 py-2.5">
        {typeof total === 'number' && total !== rows.length ? (
          <RangeNotice shown={rows.length} total={total} unit="条" note={rangeNote} />
        ) : (
          <span className="font-mono text-[12px] tracking-wide text-muted-foreground">共 {rows.length} 条</span>
        )}
        {csvFilename && (
          <button
            onClick={handleExport}
            disabled={rows.length === 0}
            className="btn btn-sm disabled:opacity-40"
            title={`导出当前表格显示的 ${rows.length} 行为 CSV（与页面范围一致）`}
          >
            <Download className="w-3.5 h-3.5" />
            导出 CSV
          </button>
        )}
      </div>

      <div
        ref={scrollRef}
        onScroll={onScrollTopChange ? handleScroll : undefined}
        className="overflow-x-auto overflow-y-auto scrollbar-thin"
        style={maxHeight ? { maxHeight } : undefined}
      >
        <table className="w-full text-sm">
          <thead className="table-sticky">
            <tr className="border-b border-border/80">
              {renderExpanded && <th className="w-8 px-2" aria-label="展开依据" />}
              {columns.map((column) => {
                const active = sort?.key === column.key
                const align = ALIGN_CLASS[column.align ?? 'left']
                return (
                  <th key={column.key} className={`px-4 py-2.5 text-[12px] font-medium tracking-[0.06em] text-muted-foreground ${align} ${column.className ?? ''}`}>
                    {column.sortable ? (
                      <button
                        onClick={() => handleSort(column)}
                        className={`inline-flex items-center gap-1 hover:text-foreground transition-colors ${active ? 'text-foreground' : ''}`}
                      >
                        {column.header}
                        {active ? (
                          sort?.direction === 'asc' ? (
                            <ArrowUp className="w-3 h-3" />
                          ) : (
                            <ArrowDown className="w-3 h-3" />
                          )
                        ) : (
                          <ArrowUpDown className="w-3 h-3 opacity-50" />
                        )}
                      </button>
                    ) : (
                      column.header
                    )}
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {sortedRows.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length + (renderExpanded ? 1 : 0)}
                  className="px-4 py-12 text-center text-[12px] text-muted-foreground"
                >
                  {emptyText}
                </td>
              </tr>
            ) : (
              sortedRows.map((row, index) => {
                const key = rowKey(row, index)
                const expanded = expandedKey === key
                const active = activeKey === key
                const handleRowClick = onRowClick
                  ? () => {
                      // 离开前同步一次滚动值：即使最后一帧的 scroll 事件被节流也不丢位置
                      if (onScrollTopChange) {
                        const top = scrollRef.current?.scrollTop ?? 0
                        lastScrollTop.current = top
                        onScrollTopChange(top)
                      }
                      setActiveKey(key)
                      onRowClick(row, key)
                    }
                  : undefined
                return (
                  <Fragment key={key}>
                    <tr
                      onClick={handleRowClick}
                      onKeyDown={
                        handleRowClick
                          ? (event) => {
                              if (event.target !== event.currentTarget) return
                              if (event.key !== 'Enter' && event.key !== ' ') return
                              event.preventDefault()
                              handleRowClick()
                            }
                          : undefined
                      }
                      tabIndex={onRowClick ? 0 : undefined}
                      className={`table-row border-b border-border/60 ${
                        onRowClick
                          ? 'cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring'
                          : ''
                      } ${expanded || active ? 'bg-brass/[0.05]' : ''}`}
                    >
                      {renderExpanded && (
                        <td className="px-2 py-2 text-center">
                          <button
                            onClick={(event) => {
                              event.stopPropagation()
                              handleExpandedChange(expanded ? null : key)
                            }}
                            aria-expanded={expanded}
                            aria-label={expanded ? '收起信号依据' : '展开信号依据'}
                            className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                          >
                            {expanded ? (
                              <ChevronDown className="w-3.5 h-3.5" />
                            ) : (
                              <ChevronRight className="w-3.5 h-3.5" />
                            )}
                          </button>
                        </td>
                      )}
                      {columns.map((column) => {
                        const align = ALIGN_CLASS[column.align ?? 'left']
                        const value = resolveValue(row, column)
                        return (
                          <td key={column.key} className={`px-4 py-[7px] text-[12px] ${column.align === 'right' ? 'num' : ''} ${align} ${column.className ?? ''}`}>
                            {column.render ? column.render(row, index) : value === null || value === undefined ? '—' : String(value)}
                          </td>
                        )
                      })}
                    </tr>
                    {renderExpanded && expanded && (
                      <tr className="border-b border-border/60 bg-muted/20">
                        <td colSpan={columns.length + 1} className="px-4 py-3">
                          {renderExpanded(row, index)}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })
            )}
          </tbody>
        </table>
      </div>

      {footer && (
        <div className="border-t border-border/70 px-4 py-2.5 text-[12px] tracking-wide text-muted-foreground">
          {footer}
        </div>
      )}
    </div>
  )
}
