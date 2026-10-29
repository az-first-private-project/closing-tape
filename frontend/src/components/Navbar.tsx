/**
 * 顶部导航（契约第 7 节路由）
 * 导航项：首页 / 盘后分析 / 选股中心 / 任务监控 / 关于
 */
import { Link, NavLink, useNavigate } from 'react-router-dom'
import { useState } from 'react'
import {
  Activity,
  BarChart3,
  Brain,
  Info,
  Moon,
  Search,
  Sun,
  TrendingUp,
} from 'lucide-react'
import { useTheme } from './ThemeProvider'
import { cn } from '../lib/utils'

/** 导航配置 */
const NAV_ITEMS = [
  { to: '/', label: '首页', icon: BarChart3, end: true },
  { to: '/dashboard', label: '盘后分析', icon: TrendingUp, end: false },
  { to: '/algo-center', label: '选股中心', icon: Brain, end: false },
  { to: '/jobs', label: '任务监控', icon: Activity, end: false },
  { to: '/about', label: '关于', icon: Info, end: false },
] as const

export default function Navbar() {
  const { theme, toggle } = useTheme()
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
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16 gap-3">
          {/* Logo */}
          <Link
            to="/"
            className="flex items-center gap-2 font-bold text-lg text-primary hover:opacity-80 transition-opacity shrink-0"
          >
            <BarChart3 className="w-6 h-6" />
            <span className="hidden sm:inline">GFNA</span>
          </Link>

          {/* 搜索框 */}
          <form onSubmit={handleSearch} className="flex-1 max-w-md">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <input
                type="text"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索财经新闻…"
                className="w-full pl-10 pr-4 py-2 rounded-full border border-input bg-background text-sm
                  focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent
                  placeholder:text-muted-foreground"
              />
            </div>
          </form>

          {/* 导航 + 主题切换 */}
          <div className="flex items-center gap-0.5">
            {NAV_ITEMS.map((item) => {
              const Icon = item.icon
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    cn(
                      'flex items-center gap-1 px-2.5 py-2 text-sm rounded-lg transition-colors',
                      isActive
                        ? 'text-primary bg-primary/10'
                        : 'text-muted-foreground hover:text-foreground hover:bg-secondary',
                    )
                  }
                  title={item.label}
                >
                  <Icon className="w-4 h-4" />
                  <span className="hidden lg:inline">{item.label}</span>
                </NavLink>
              )
            })}

            <button
              onClick={toggle}
              className="ml-1 p-2 rounded-lg hover:bg-secondary transition-colors"
              aria-label={theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'}
            >
              {theme === 'light' ? (
                <Moon className="w-5 h-5 text-muted-foreground" />
              ) : (
                <Sun className="w-5 h-5 text-muted-foreground" />
              )}
            </button>
          </div>
        </div>
      </div>
    </nav>
  )
}
