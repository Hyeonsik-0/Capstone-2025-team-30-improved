import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const isProduction = mode === 'production'
  
  return {
    plugins: [
      react({
        babel: {
          plugins: isProduction ? [
            ['transform-remove-console', { exclude: ['error', 'log'] }]
          ] : []
        }
      })
    ],
    define: {
      // 측정용 빌드 옵션 (tools/browser-e2e, 이슈 #9). 켠 빌드에서만 실험 상태 저장소를 window에 노출한다.
      // 빌드할 때 상수로 바뀌므로, 끈 빌드(기본값)에서는 노출 코드가 결과물에서 빠진다.
      __E2E_EXPOSE_STORE__: JSON.stringify(env.VITE_E2E_EXPOSE_STORE === 'true'),
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src')
      }
    },
    build: {
      // 프로덕션 빌드 최적화
      minify: isProduction ? 'terser' : false,
      // 소스맵 설정 (개발용)
      sourcemap: !isProduction,
    },
    server: {
      port: 3000,
      proxy: {
        '/signaling': {
          target: env.VITE_SIGNALING_BASE_URL || 'http://localhost:80',
          changeOrigin: true,
          secure: false
        },
        '/config': {
          target: env.VITE_SIGNALING_BASE_URL || 'http://localhost:80',
          changeOrigin: true,
          secure: false
        },
        '/api': {
          target: env.VITE_API_BASE_URL || 'http://localhost:8080',
          changeOrigin: true,
          secure: false
        },
        '/training-metrics': {
          target: env.VITE_API_BASE_URL || 'http://localhost:8080',
          changeOrigin: true,
          secure: false
        },
        '/ws': {
          target: (env.VITE_SIGNALING_BASE_URL || 'http://localhost:80').replace('http', 'ws'),
          changeOrigin: true,
          ws: true
        }
      }
    }
  }
})