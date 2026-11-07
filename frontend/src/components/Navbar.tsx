/**
 * 顶部导航（契约第 7 节路由）
 * 导航项：首页 / 盘后分析 / 选股中心 / 纸面跟踪 / 任务监控 / 关于
 *
 * 视觉：玻璃条 + 墨色品牌标 + 铜色发丝线；激活项用共享 layoutId 的浮块指示器，
 * 在导航项之间平滑迁移。交互行为（路由、搜索提交、命令面板、红点计数）保持不变。
 */
import { Link, NavLink, useNavigate } from 'react-router-dom'
import { useState } from 'react'
import { motion } from 'framer-motion'
import { BarChart3, Moon, Search, Sun } from 'lucide-react'
import { useTheme } from './ThemeProvider'
import { useWatchBadge } from '../hooks/useWatchSignals'
import { NAV_ITEMS, PALETTE_OPEN_EVENT } from '../lib/navigation'
import { cn } from '../lib/utils'

export default function Navbar() {
  const { theme, toggle } = useTheme()
  const { newCount } = useWatchBadge()
  const [query, setQuery] = useState('')
  const navigate = useNavigate()

  const handleSearch = (event: React.FormEvent) => {
    event.preventDefault()
    if (query.trim()) {
      navigate(`/?q=${encodeURIComponent(query.trim())}`)
    }
  }

  return (
    <nav className="glass sticky top-0 z-50">
      {/* 顶部铜色发丝线：整页唯一的品牌横线 */}
      <span aria-hidden="true" className="rule-brass pointer-events-none absolute inset-x-0 top-0 h-px opacity-80" />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16 gap-3">
          {/* ========== 品牌 ========== */}
          <Link to="/" className="group flex shrink-0 items-center gap-2.5" aria-label="GFNA 首页">
            <span
              className="grid h-8 w-8 place-items-center rounded-[10px] bg-primary text-primary-foreground
                ring-1 ring-brass/40 shadow-[inset_0_1px_0_rgba(255,255,255,0.18)]
                transition-transform duration-500 ease-[cubic-bezier(0.16,1,0.3,1)] group-hover:-rotate-6"
            >
              <BarChart3 className="h-4 w-4" strokeWidth={2.3} />
            </span>
            <span className="leading-none">
              <span className="block font-mono text-[15px] font-bold tracking-[0.16em] text-foreground">
                GFNA
              </span>
              <span className="mt-1 hidden text-[9px] font-medium tracking-[0.22em] text-muted-foreground sm:block">
                A-SHARE TERMINAL
              </span>
            </span>
          </Link>

          {/* ========== 搜索 ========== */}
          <form onSubmit={handleSearch} className="hidden md:block flex-1 max-w-sm">
            <div className="group relative">
              <Search
                className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2
                  text-muted-foreground transition-colors group-focus-within:text-brass"
              />
              <input
                type="text"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索财经新闻…"
                aria-label="搜索财经新闻"
                className="h-9 w-full rounded-lg border border-input bg-card/45 pl-9 pr-14 text-[13px]
                  text-foreground placeholder:text-muted-foreground/70
                  transition-[border-color,box-shadow,background-color] duration-300
                  hover:border-brass/40
                  focus:border-ring focus:bg-card focus:outline-none focus:ring-[3px] focus:ring-ring/20"
              />
              <button
                type="button"
                onClick={() => window.dispatchEvent(new Event(PALETTE_OPEN_EVENT))}
                aria-label="打开命令面板（Ctrl+K 或 Command+K）"
                title="命令面板（Ctrl/⌘ + K）"
                className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md border border-border/80
                  bg-muted/60 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground
                  transition-colors hover:border-brass/40 hover:text-foreground"
              >
                ⌘K
              </button>
            </div>
          </form>

          {/* ========== 导航 + 主题：导航项在窄屏内部横向滚动，主题开关固定在右侧 ========== */}
          <div className="flex min-w-0 items-center gap-1">
            <div className="flex min-w-0 items-center gap-0.5 overflow-x-auto scrollbar-thin">
              {NAV_ITEMS.map((item) => {
              const Icon = item.icon
              const isWatchlist = item.to === '/watchlist'
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    cn(
                      'group relative flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-2 text-[13px] font-medium',
                      'transition-colors duration-300',
                      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                      isActive
                        ? 'text-foreground'
                        : 'text-muted-foreground hover:text-foreground',
                    )
                  }
                  title={
                    isWatchlist && newCount > 0
                      ? `自选股有 ${newCount} 条新信号`
                      : item.label
                  }
                  aria-label={
                    isWatchlist
                      ? newCount > 0
                        ? `自选股有 ${newCount} 条新信号`
                        : '自选股'
                      : undefined
                  }
                >
                  {({ isActive }) => (
                    <>
                      {/* 激活浮块：共享 layoutId，跨项平滑迁移 */}
                      {isActive && (
                        <motion.span
                          layoutId="nav-active-pill"
                          aria-hidden="true"
                          className="absolute inset-0 rounded-lg border border-border/80
                            bg-gradient-to-b from-card to-secondary/70
                            shadow-[inset_0_1px_0_rgba(255,255,255,0.35)]"
                          transition={{ type: 'spring', stiffness: 420, damping: 36 }}
                        />
                      )}
                      {!isActive && (
                        <span
                          aria-hidden="true"
                          className="absolute inset-0 rounded-lg bg-secondary/0 transition-colors duration-300 group-hover:bg-secondary/60"
                        />
                      )}
                      <span className="relative flex items-center gap-1.5">
                        <span className="relative inline-flex">
                          <Icon
                            className={cn(
                              'h-4 w-4 transition-colors duration-300',
                              isActive ? 'text-brass' : 'group-hover:text-foreground',
                            )}
                            strokeWidth={isActive ? 2.2 : 1.8}
                          />
                          {isWatchlist && newCount > 0 && (
                            <span
                              aria-hidden="true"
                              className="absolute -right-2 -top-2 min-w-[15px] h-[15px] px-1 rounded-full
                                bg-alert text-background text-[9px] font-bold leading-[15px] text-center
                                border border-background num"
                            >
                              {newCount > 9 ? '9+' : newCount}
                            </span>
                          )}
                        </span>
                        <span className="hidden lg:inline">{item.label}</span>
                      </span>
                    </>
                  )}
                </NavLink>
              )
              })}
            </div>

            <span aria-hidden="true" className="mx-1 h-5 w-px shrink-0 bg-border/80" />

            <button
              onClick={toggle}
              className="group ml-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-transparent
                text-muted-foreground transition-all duration-300
                hover:border-border hover:bg-secondary/60 hover:text-brass"
              aria-label={theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'}
            >
              {theme === 'light' ? (
                <Moon className="h-[17px] w-[17px] transition-transform duration-500 group-hover:-rotate-12" />
              ) : (
                <Sun className="h-[17px] w-[17px] transition-transform duration-500 group-hover:rotate-45" />
              )}
            </button>
          </div>
        </div>
      </div>
    </nav>
  )
}
