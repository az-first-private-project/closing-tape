import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import HomePage from './pages/HomePage'
import DetailPage from './pages/DetailPage'
import DashboardPage from './pages/DashboardPage'
import AboutPage from './pages/AboutPage'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/article/:id" element={<DetailPage />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/about" element={<AboutPage />} />
      </Route>
    </Routes>
  )
}
