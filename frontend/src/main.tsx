// import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { useExperimentStore } from '@/store/experimentStore'

// 측정용 빌드(VITE_E2E_EXPOSE_STORE=true)에서만 측정 도구가 차트 데이터를 읽을 수 있게 저장소를 노출한다. (이슈 #9)
// 일반 빌드에서는 조건이 false 상수라 이 블록이 결과물에 들어가지 않는다.
if (__E2E_EXPOSE_STORE__) {
  (window as unknown as { __E2E__: unknown }).__E2E__ = { experimentStore: useExperimentStore }
}

createRoot(document.getElementById('root')!).render(
  // <StrictMode>
    <App />
  // </StrictMode>,
)
