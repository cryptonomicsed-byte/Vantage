import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8001',
      '/media': 'http://localhost:8001',
    },
  },
  build: {
    outDir: 'dist',
  },
})
