import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
//
// Dev server runs on http://localhost:5173 (Vite default).
//
// Backend wiring — two supported approaches (see README):
//   1. Dev proxy (default): requests to /api and /health are proxied to the
//      FastAPI backend on http://localhost:8000, so the browser only ever talks
//      to the Vite origin (no CORS needed in dev, though the backend enables it).
//   2. Env-configurable base URL: set VITE_API_BASE_URL in .env to point the
//      frontend directly at the backend (see src/api.ts). When set, the proxy
//      is bypassed.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
