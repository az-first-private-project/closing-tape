import { Outlet } from 'react-router-dom'
import { ThemeProvider } from './ThemeProvider'
import Navbar from './Navbar'
import Footer from './Footer'
import CommandPalette from './CommandPalette'

export default function Layout() {
  return (
    <ThemeProvider>
      <div className="min-h-screen flex flex-col bg-background">
        <Navbar />
        <CommandPalette />
        <main className="flex-1">
          <Outlet />
        </main>
        <Footer />
      </div>
    </ThemeProvider>
  )
}
