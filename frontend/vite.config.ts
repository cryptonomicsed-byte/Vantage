import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
  },
  server: {
    proxy: {
      // Vantage SOC endpoints (primary)
      '/api/admin': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      '/api/agents': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      '/api/feed': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      '/api/platform': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      '/api/trading': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      '/api/pine': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      '/api/copilot': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      '/api/code': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      '/api/video': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      '/api/mesh': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      // Ares intelligence endpoints (port 8879)
      '/api/intel': {
        target: 'http://localhost:8879',
        changeOrigin: true,
      },
      '/api/debate': {
        target: 'http://localhost:8879',
        changeOrigin: true,
      },
      '/api/alpha': {
        target: 'http://localhost:8879',
        changeOrigin: true,
      },
      '/api/backtest': {
        target: 'http://localhost:8879',
        changeOrigin: true,
      },
      '/api/health': {
        target: 'http://localhost:8879',
        changeOrigin: true,
      },
      '/api/arbitrage': {
        target: 'http://localhost:8879',
        changeOrigin: true,
      },
      '/api/sentiment': {
        target: 'http://localhost:8879',
        changeOrigin: true,
      },
      '/api/sources': {
        target: 'http://localhost:8879',
        changeOrigin: true,
      },
      // Ares data sources (port 9861)
      '/api/rpc': {
        target: 'http://localhost:9861',
        changeOrigin: true,
      },
      '/api/wallets': {
        target: 'http://localhost:9861',
        changeOrigin: true,
      },
      '/media': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
  },
})
