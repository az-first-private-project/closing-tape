import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const API_PROXY = {
  '/api': {
    target: 'http://localhost:8000',
    changeOrigin: true,
  },
  // /health 不在 /api 前缀下；nginx 已单独反代，dev 也需保持一致
  '/health': {
    target: 'http://localhost:8000',
    changeOrigin: true,
  },
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: API_PROXY,
  },
  // `vite preview` 默认不继承 server.proxy，这里显式复制一份，
  // 否则用构建产物试用时 /api 会 404（打包预览场景经常踩这个坑）
  preview: {
    port: 5173,
    proxy: API_PROXY,
  },
})
