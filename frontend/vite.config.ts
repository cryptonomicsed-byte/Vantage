import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '3d-force-graph': path.resolve(__dirname, 'src/stubs/3d-force-graph.js'),
      'three': path.resolve(__dirname, 'src/stubs/three.js'),
      'hls.js': path.resolve(__dirname, 'src/stubs/hls.js'),
      'video.js/dist/video-js.css': path.resolve(__dirname, 'src/stubs/video-js.css'),
      'video.js': path.resolve(__dirname, 'src/stubs/videojs'),
      'lightweight-charts': path.resolve(__dirname, 'src/stubs/lightweight-charts.js'),
      'react-markdown': path.resolve(__dirname, 'src/stubs/react-markdown.jsx'),
      'remark-gfm': path.resolve(__dirname, 'src/stubs/remark-gfm.js'),
      'qrcode': path.resolve(__dirname, 'src/stubs/qrcode.js'),
      '@videojs/http-streaming': path.resolve(__dirname, 'src/stubs/videojs-http-streaming.js'),
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    // e2e/ is Playwright's, and its specs call test() from @playwright/test.
    // Without this vitest globs them too and fails with "Playwright Test did
    // not expect test() to be called here".
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
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
      // /api/intel and /api/alpha are real routers mounted in the main
      // Vantage backend (backend/routers/intel.py, backend/routers/alpha.py)
      // — port 8001, not the legacy standalone Ares dashboard on 8879.
      '/api/intel': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      '/api/debate': {
        target: 'http://localhost:8879',
        changeOrigin: true,
      },
      '/api/alpha': {
        target: 'http://localhost:8001',
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
