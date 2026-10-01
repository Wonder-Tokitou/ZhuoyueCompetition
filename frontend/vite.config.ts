import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// 开发端口和后端代理端口通过 VITE_DEV_PORT / VITE_BACKEND_PORT 配置，
// 默认使用本地化构建的 5174 / 8100，不影响原始 case-sim 项目。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendPort = env.VITE_BACKEND_PORT || '8100'
  const frontendPort = Number(env.VITE_DEV_PORT || '5174')

  return {
    plugins: [react()],
    server: {
      host: true,
      port: frontendPort,
      proxy: {
        '/api': {
          target: `http://127.0.0.1:${backendPort}`,
          changeOrigin: false,
        },
        '/media': {
          target: `http://127.0.0.1:${backendPort}`,
          changeOrigin: false,
        },
      },
    },
  }
})
