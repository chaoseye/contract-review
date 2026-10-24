import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// В разработке `npm run dev` проксирует /api на backend/run_dev.py (http://127.0.0.1:8000).
// В Docker собранный интерфейс раздаёт сам backend, поэтому прокси там не нужен.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // Ant Design даёт основной объём (~800 КБ, ~260 КБ в gzip). Для внутреннего инструмента это приемлемо.
    chunkSizeWarningLimit: 1200,
  },
})
