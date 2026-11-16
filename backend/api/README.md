# ML Experiment API Documentation

이 문서는 ML 실험 관리 시스템의 백엔드 API에 대한 명세서입니다.

## 📋 목차

- [개요](#개요)
- [기본 정보](#기본-정보)
- [공통 응답 형식](#공통-응답-형식)
- [API 엔드포인트](#api-엔드포인트)
- [WebSocket](#websocket)
- [에러 코드](#에러-코드)

## 개요

이 API는 머신러닝 실험의 전체 생명주기를 관리합니다:
- 실험 템플릿 생성 및 관리
- 실험 실행 및 모니터링
- 모델 테스트 및 평가
- 실시간 훈련 메트릭 수집
- 알고리즘 및 환경 설정 관리

## 기본 정보

- **Base URL**: `http://localhost:8080`
- **Content-Type**: `application/json`
- **인증**: 현재 인증 없음

## 공통 응답 형식

### 성공 응답
```json
{
  "success": true,
  "data": { ... },
  "message": "Optional success message"
}
```

### 에러 응답
```json
{
  "error": "Error message",
  "details": { ... }
}
```

## API 엔드포인트

### 실험 관리 (Runs)

#### 1. 실험 목록 조회
```http
GET /api/runs
```

#### 2. 실험 상세 조회
```http
GET /api/runs/:id
```

#### 3. 실험 상태 조회
```http
GET /api/runs/:id/status
```

#### 4. 실험 삭제
```http
DELETE /api/runs/:id
```

#### 5. 실험 제어
- 일시정지: `POST /api/runs/:id/pause`
- 재개: `POST /api/runs/:id/resume`
- 종료: `POST /api/runs/:id/stop`
- 시뮬레이터 속도 변경: `POST /api/runs/:id/sim-speed`

### 모델 테스트 (Models)

#### 1. 모델 목록 조회
```http
GET /api/models
```

#### 2. 모델 상세 조회
```http
GET /api/models/:id
```

#### 3. 모델 상태 조회
```http
GET /api/models/:id/status
```

#### 4. 모델 테스트 시작
```http
POST /api/models/:id/test
```

#### 5. 모델 테스트 제어
- 일시정지: `POST /api/models/:id/pause`
- 재개: `POST /api/models/:id/resume`
- 종료: `POST /api/models/:id/stop`
- 시뮬레이터 속도 변경: `POST /api/models/:id/sim-speed`

### 실험 템플릿 (Experiment Templates)

#### 1. 템플릿 목록 조회
```http
GET /api/experiment-templates
```

#### 2. 템플릿 상세 조회
```http
GET /api/experiment-templates/:id
```

#### 3. 템플릿 생성
```http
POST /api/experiment-templates
```

#### 4. 템플릿 수정
```http
PATCH /api/experiment-templates/:id
```

#### 5. 템플릿 삭제
```http
DELETE /api/experiment-templates/:id
```

#### 6. 템플릿 기반 실험 실행
```http
POST /api/experiment-templates/:id/runs
```

### 알고리즘 (Algorithms)

#### 1. 알고리즘 목록 조회
```http
GET /api/algorithms?environment=envName
```

#### 2. 알고리즘 스키마 조회
```http
GET /api/algorithms/:algo
```

### 환경 (Environments)

#### 1. 환경 목록 조회
```http
GET /api/environments
```

#### 2. 환경 스키마 조회
```http
GET /api/environments/:env
```

### 콜백 (Callbacks)

#### 1. 실험 완료 콜백
```http
POST /callbacks/:id/experiment-completed
```

#### 2. 테스트 완료 콜백
```http
POST /callbacks/:id/test-completed
```

#### 3. 학습 서버 생존 신호
```http
POST /callbacks/:id/heartbeat
```
학습 서버가 학습 스레드가 도는 동안(일시정지 포함) 10초마다 보낸다. 진행 중(RUNNING·PAUSED)인 실험이면 마지막 신호 시각을 갱신하고 204, 아니면 409를 돌려준다.
BFF는 10초마다 확인해 마지막 신호(없으면 시작 시각)가 60초보다 오래된 진행 중 실험을 FAILED로 바꾸고 `error`에 이유를 남긴다. BFF가 뜬 직후 60초는 판정하지 않는다. (`src/utils/runWatchdog.ts`, 이슈 #21)

### 훈련 메트릭 (Training Metrics)

#### 1. 메트릭 조회
```http
GET /training-metrics/:runId
```

## WebSocket

실시간 훈련 메트릭을 수신하기 위한 WebSocket 연결을 제공합니다.

- **URL**: `ws://localhost:8080/ws-api`
- **연결 시**: `{ "runId": "실험_ID" }` 전송
- **수신**: 훈련 메트릭 데이터 스트림

## 에러 코드

| 상태 코드 | 설명 |
|----------|------|
| 400 | 잘못된 요청 (잘못된 ID 형식, 필수 필드 누락 등) |
| 404 | 리소스를 찾을 수 없음 |
| 409 | 충돌 (중복된 이름 등) |
| 500 | 서버 내부 오류 |

## 상태 코드

### 실험 상태 (status)
- `IDLE`: 대기 중
- `RUNNING`: 실행 중
- `PAUSED`: 일시정지
- `FAILED`: 실패
- `COMPLETED`: 성공
- `STOPPED`: 중단됨

### 테스트 상태 (testStatus)
- `INVALID`: 테스트 불가능
- `IDLE`: 대기 중
- `TESTING`: 테스트 중
- `PAUSED`: 일시정지
- `COMPLETED`: 완료

## 환경 변수

- `PORT`: 서버 포트 (기본값: 8080)
- `FASTAPI_BASE`: FastAPI 서버 URL (기본값: http://localhost:8000)
- `MONGODB_URI`: MongoDB 연결 문자열

## 개발 정보

- **Node.js**: 18+
- **Express.js**: 5.x
- **MongoDB**: 5.x
- **TypeScript**: 5.x