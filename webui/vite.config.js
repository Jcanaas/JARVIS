import { svelte } from '@sveltejs/vite-plugin-svelte'
import { defineConfig } from 'vite'

// Dev-only: the Flask backend (actions/webui_server.py) runs on 127.0.0.1
// at the port from app_settings("webui_port", 8877). Proxying here keeps
// fetch()/EventSource same-origin from the browser's point of view, so no
// CORS handling is needed in the Python backend either in dev or prod
// (in prod, webui_server.py itself serves this app's built dist/).
const BACKEND_URL = 'http://127.0.0.1:8877'

export default defineConfig({
  plugins: [svelte()],
  server: {
    proxy: {
      '/api': {
        target: BACKEND_URL,
        changeOrigin: true,
      },
    },
  },
})
