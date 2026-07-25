import { Outlet } from 'react-router-dom'
import { ThemeProvider } from './ThemeProvider'
import Navbar from './Navbar'
import Footer from './Footer'

export default function Layout() {
  return (
    <ThemeProvider>
      <div className="min-h-screen flex flex-col bg-background">
        <Navbar />
        <main className="flex-1">
          <Outlet />
        </main>
        <Footer />
      </div>
    </ThemeProvider>
  )
}
