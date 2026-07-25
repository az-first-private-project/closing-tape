import { Link } from 'react-router-dom'
import { BarChart3, Search, Sun, Moon } from 'lucide-react'
import { useTheme } from './ThemeProvider'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

export default function Navbar() {
  const { theme, toggle } = useTheme()
  const [query, setQuery] = useState('')
  const navigate = useNavigate()

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    if (query.trim()) {
      navigate(`/?q=${encodeURIComponent(query.trim())}`)
    }
  }

  return (
    <nav className="glass sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16 gap-4">
          {/* Logo */}
          <Link
            to="/"
            className="flex items-center gap-2 font-bold text-xl text-primary hover:opacity-80 transition-opacity shrink-0"
          >
            <BarChart3 className="w-6 h-6" />
            <span className="hidden sm:inline">GFNA</span>
          </Link>

          {/* 搜索框 */}
          <form onSubmit={handleSearch} className="flex-1 max-w-lg">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索财经新闻..."
                className="w-full pl-10 pr-4 py-2 rounded-full border border-input bg-background text-sm
                  focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent
                  placeholder:text-muted-foreground"
              />
            </div>
          </form>

          {/* 右侧按钮 */}
          <div className="flex items-center gap-2">
            <Link
              to="/dashboard"
              className="hidden sm:flex items-center gap-1 px-3 py-2 text-sm text-muted-foreground
                hover:text-foreground rounded-lg hover:bg-secondary transition-colors"
            >
              <BarChart3 className="w-4 h-4" />
              <span className="hidden md:inline">仪表盘</span>
            </Link>

            <button
              onClick={toggle}
              className="p-2 rounded-lg hover:bg-secondary transition-colors"
              aria-label="切换深色模式"
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
