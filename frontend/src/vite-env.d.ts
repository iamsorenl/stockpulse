/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Optional. When set (e.g. "http://localhost:8000") the frontend calls the
   * backend directly at this origin, bypassing the Vite dev proxy. Leave unset
   * to use the proxy (relative paths). See src/api.ts.
   */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
