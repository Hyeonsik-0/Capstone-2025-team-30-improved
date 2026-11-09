### 1. 프로젝트 배경

#### 1.1. 국내외 시장 현황 및 문제점

최근 자율주행 자동차, 스마트 팩토리, 이족보행 로봇 등 다양한 분야에서 인공지능의 중요성이 커지고 있습니다. 인공지능 학습 방식 중 **강화학습(RL)** 은 대표적인 방법으로, 정답 레이블 없이 시행착오를 통해 스스로 **정책(policy)** 을 개선합니다.
강화학습은 **사전지식 없이도 자율 학습 가능**하다는 장점이 있으나, **학습 시간과 시뮬레이션 비용이 매우 크다**는 한계가 존재합니다. 초기에는 다양한 예외 상황에서 실패를 반복하며 학습 진전이 느리기 때문입니다.

#### 1.2. 필요성과 기대효과

본 과제는 강화학습 중에도 **지도 신호**를 제공하여 학습 효율을 높이는 접근입니다.

* **실시간 사용자 피드백(LLM 연동)**: 상황별 행동을 **긍정/중립/부정**으로 피드백
* **Behavior Cloning(BC)**: 사전 정의된 “올바른 선택”을 모사
* **기대효과**: **학습 시간 단축**, **안정적 성능 향상**, **데이터/연산 자원 절감**

---

### 2. 연구 목표

#### 2.1. 목표 및 세부 내용

* 사용자의 실시간 피드백을 **정량화(긍/중/부)** 하여 학습에 반영 → **정책 수렴 가속**
* **BC + RL** 혼합으로 **짧은 시간/적은 자원**으로 **동등 이상의 성능** 확보
* Unity 환경에 알고리즘 **안정 이식**, **ML-Agents ⇄ Gym** 래핑으로 재사용성 확보
* **웹 대시보드**에서 실험 **생성–제어–관찰–분석–산출물 관리** 일원화
  → **재현성**과 **시연 효율** 동시 확보

---

### 3. 시스템 설계

#### 3.1. 시스템 구성도

<img width="1920" height="1080" alt="simulator (3)" src="https://github.com/user-attachments/assets/6f85494b-1504-40d4-906c-11e316d55dc0" />

#### 3.2. 사용 기술

| 분야              | 기술/스택                                                                                                                                                                             |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **강화학습 모델**     | Python 3.13.2, PyTorch 2.7.1, TorchVision 0.22.1, CUDA 12.6.x, cuDNN 9.5.1, Gymnasium 1.1.1 (CarRacing-v3), Stable Baselines3 2.6.0, NumPy 2.2.5, OpenCV 4.11.0.86, Pillow 11.3.0 |
| **실험 로깅·모델 관리** | TensorBoard 2.19.0 (events logs), Git LFS                                                                                                                                         |
| **LLM 모델**      | GPT-4o-mini, OpenAI Python SDK 1.107.2                                                                                                                                            |
| **시뮬레이터**       | Unity 2022.3.0f1,ML-Agents 3.0.0, Gymnasium 1.1.1                                                                                                                                          |
| **프론트엔드**       | React 19.1.0, TypeScript 5.8.3, Vite 7.0.4, Styled Components 6.1.19, Zustand 5.0.7, Recharts 3.1.2                                                                               |
| **백엔드 (API 서버)** | Node.js 20.x(Alpine), Express 5.1.0, Typescript 5.9.2, Mongoose 8.17.1, FastAPI 0.116.1 (강화학습 제어) , pytorch 2.1.2(cpu 전용)                                                      |
| **백엔드 (시그널링 서버)** | Node.js 20.x(Alpine), Express 4.21.2, WebSocket(ws) 8.8.1                                                                                                                    |
| **개발·배포 환경**    | Docker 28.4.0, Docker Compose 2.39.2, Nginx 1.29.1 (Alpine)                                                                                                                          |
| **데이터베이스**      | MongoDB 7.0                                                                                                                                                                           |

---

### 4. 개발 결과

#### 4.1. 전체 시스템 흐름도

1. 실험 생성 및 설정
- 웹 대시보드에서 실험 생성 (템플릿 선택 또는 새로 작성)
  - 환경 선택: Ball, Car, CNN Car
  - 알고리즘 선택: DQN, PPO 등
  - 환경 파라미터 설정: Motor Torque, Brake Torque 등 (환경에 따른 물리적인 값)
  - 알고리즘 하이퍼파라미터 설정: Learning Rate, Batch Size, Exploration Fraction 등
2. 학습 실행 및 모니터링
- BFF(Node.js) → FastAPI로 학습 시작 요청
- FastAPI가 Unity 환경 초기화 및 학습 시작
- 실시간 모니터링
  - Unity Render Streaming(WebRTC)으로 에이전트 행동 시각화
  - 보상, 손실, 탐험률 등 메트릭 실시간 차트 표시
3. 사용자 피드백 (선택적)
- CNN Car + HF_LLM 조합 전용 기능
- 에이전트 행동에 대해 실시간 피드백 입력 (긍정/중립/부정)
- LLM(GPT-4o-mini)이 정성적 피드백을 정량화된 보상으로 변환
- 변환된 보상을 학습 알고리즘에 실시간 반영하여 학습 방향 개선
4. 학습 완료 및 저장
- 학습 종료 후 자동 저장
- 모델 파일(.zip): 학습된 가중치
- 학습 로그(CSV): 메트릭 히스토리
- 실험 메타데이터: MongoDB에 저장
5. 결과 분석 및 활용
- 여러 실험 결과 비교 및 분석
- 학습 곡선 시각화 및 성능 비교
- 모델 다운로드 및 테스트
- 성공적인 설정을 템플릿으로 저장하여 재사용

#### 4.2. 기능 설명 및 주요 기능 명세서

##### 기능 개요

* **대시보드 구성**: 실험 템플릿, 실행 상태, 실시간 모니터링, 결과 확인을 한 화면에서 관리

##### 실험 템플릿 관리

* **실험 생성**: 실험 이름·환경·알고리즘 선택, 환경 파리미터 / 알고리즘 하이퍼파라미터 설정
* **실험 불러오기**: 생성된 실험 목록에서 선택/삭제
* **실험 정보 표시**: 선택된 실험의 기본 정보(좌측 패널)
* **실험 노트**: 실험 관련 메모 입력 및 관리

##### 실험 실행 관리

* **실험 시작** / **일시정지·재개** / **종료**
* 상태·진행률·최근 메트릭 표시

##### 실시간 시뮬레이터 및 모니터링

* **비디오 플레이어**: Unity Render Streaming(WebRTC) 실시간 출력, 연결 상태 표시
* **배속 조절**: 슬라이더(느림/보통/빠름/매우빠름)
* **실시간 차트**: 보상(reward), 손실(loss), 탐험률(ε), 에피소드 길이

##### 모델 테스트

* **모델 불러오기/삭제**, **모델 정보 표시**
* **테스트 실행 관리**: 시작/일시정지/재개/종료
* **실시간 시뮬레이터**: 학습과 동일한 스트리밍 (속도 조절 불가)

##### 결과 페이지

* **학습 결과**: 종료된 실험 선택 → 상세 로그 차트
* **실험 비교**: 동일 환경 두 실험 나란히 비교
* **다운로드**: 모델(.zip), 로그(.csv)

#### 4.3. 디렉토리 구조

```          
repo-root/
├─ docs                     # 보고서, 포스터, 발표자료 등 문서
├─ algorithms               # DQN 기반 개선된 알고리즘 실험 관련 파일
├─ frontend/                # React + Vite 대시보드 (실험 관리 UI)
│  ├─ src/
│  │  ├─ features/         # 기능별 컴포넌트 (dashboard, model-test, results)
│  │  ├─ shared/           # 공통 컴포넌트, API 클라이언트, 유틸리티
│  │  ├─ core/             # WebRTC, 스트리밍 핵심 로직
│  │  └─ store/            # Zustand 상태 관리
│  └─ dist/                # 빌드 결과물
├─ backend/                # 백엔드 서비스들
│  ├─ api/                 # Node.js BFF (API Gateway)
│  │  └─ src/
│  │     ├─ routes/        # REST API 엔드포인트
│  │     ├─ models/        # MongoDB 스키마
│  │     └─ utils/         # 헬퍼 함수들
│  ├─ fast_unity/          # FastAPI RL 제어 서버
│  │  ├─ app/              # FastAPI 애플리케이션
│  │  ├─ unity/            # Unity 환경 및 학습 로직
│  │  │  ├─ envs/          # Unity 빌드된 환경들 (ball, car, cnn_car)
│  │  │  ├─ train/         # 학습 스크립트 (algo_registry, train_runner)
│  │  │  └─ train_util/    # 학습 유틸리티 (래퍼, 콜백, 피드백)
│  │  ├─ models/           # 학습된 모델 파일들
│  │  └─ train_logs/       # 학습 로그 및 메트릭
│  └─ signaling-server/    # WebRTC 시그널링 서버 (TypeScript)
├─ nginx/                  # 리버스 프록시 설정
├─ docker-compose.yml      # 전체 스택 배포 설정
├─ env.example            # 환경 변수 예시
└─ README-Docker.md       # Docker 사용법 문서
```

---

### 5. 설치 및 실행 방법

#### 5.1. 설치 절차 및 실행

```bash
# 0) 필수: Windows 환경에서 Docker Desktop 설치
#    (Docker Compose V2 포함)

# 1) 환경 변수 파일 설정
cp .env.example .env
# .env 파일에 다음 항목 작성:
# - OPENAI_API_KEY
# - CLOUDFLARE_TOKEN
# (예시는 .env.example 참고)

# 2) 빌드 & 실행
# 루트 디렉토리에서 실행
docker compose up --build

# 3) 접속
# - 기본: http://localhost
# - Cloudflare Tunnel을 사용하는 경우: 토큰을 .env에 입력하고, 발급받은 도메인으로 접속

```


## 5.2. 오류 발생 시 해결 방법(자주 나오는 이슈)

| 증상                | 원인/설명                 | 조치                                       |
| ----------------- | --------------------- | ---------------------------------------- |
| /usr/bin/env: ‘bash\r’: No such file or directory | fast_unity/entrypoint.sh의 End of Line Sequence가 CRLF로 설정됨       | End of Line Sequence를 LF로 변경      |
| 시뮬레이터 영상 스트리밍 불가      | NAT 타입/방화벽 문제 또는 UDP 차단 | 동일 네트워크 환경에서 재테스트, TURN 서버 설정 |
| 시뮬레이터 영상 스트리밍 끊김  | WebRTC 연결 불안정         | 재연결 버튼으로 연결 재시도              |
| OpenAI API 오류     | API 키 미설정 또는 레이트 리밋 초과          | .env 키 확인, 백오프 재시도, 요금제 확인             |
| 실시간 학습 메트릭 미전송    | SSE 연결 불안정    | 새로고침 후 DB로부터 메트릭 재수신            |

#### 5.3. 빌드 재현성 (보완 작업, 이슈 #3)

같은 소스로 다시 빌드했을 때 **Python·Node 의존성이 같은 버전으로 설치되도록** 고정했습니다. 기준 시점은 졸업과제 배포 구성이 완성된 2025-09-30입니다. 이미지가 바이트 단위로 같아지는 것은 아니며, 남는 위험은 아래에 정리했습니다.

| 대상 | 고정 방식 |
| --- | --- |
| 학습 서버 Python 의존성 | `backend/fast_unity/requirements.lock` (하위 의존성까지 해시 포함 고정) |
| 소스 빌드 도구 | `backend/fast_unity/build-requirements.lock` (gym·pettingzoo는 소스 배포판만 있어 설치 중 빌드되므로 setuptools·wheel도 해시 포함 고정) |
| Node 의존성 | 각 `package-lock.json` + `npm ci` (`.dockerignore`로 호스트의 `node_modules` 유입 차단) |
| 베이스·서비스 이미지 | 버전 태그 고정 (예: `node:20.19.5-alpine3.22`, `mongo:7.0.23`, `nginx:1.29.1-alpine3.22`) |
| 학습 서버 플랫폼 | `linux/amd64` (Unity 환경 실행 파일이 x86_64 전용) |

- **알려진 버전 충돌**: SB3 2.6.0은 torch 2.3 이상을 요구하지만, 당시 학습 서버는 torch 2.1.2(CPU)로 동작했습니다. 재현이 목적이므로 `backend/fast_unity/overrides.txt`로 torch 2.1.2를 유지합니다. CI가 이 조합으로 앱 import와 PPO 학습·저장·로드를 확인합니다.
- **lock 재생성**: 최상위 의존성은 `requirements.in`, `build-requirements.in`에서 관리하고, `backend/fast_unity`에서 아래 명령으로 lock을 다시 만듭니다. lock은 **uv 0.12.20**으로 만들었습니다.

  ```bash
  uv pip compile requirements.in --override overrides.txt --torch-backend cpu \
    --python-version 3.10.12 --python-platform x86_64-manylinux_2_28 \
    --exclude-newer 2025-09-30T15:00:00Z --generate-hashes -o requirements.lock
  uv pip compile build-requirements.in \
    --python-version 3.10.12 --python-platform x86_64-manylinux_2_28 \
    --exclude-newer 2025-09-30T15:00:00Z --generate-hashes -o build-requirements.lock
  ```

  lock 파일 안에 `--index-url`을 넣으면 Dockerfile 명령줄의 PyTorch 저장소 설정이 덮어써지므로 `--emit-index-url`은 쓰지 않습니다.

- **자동 확인** (`.github/workflows/build.yml`, PR·develop push·매주 1회)
  - lock 파일이 `requirements.in`·`overrides.txt`에서 그대로 다시 만들어지는지
  - compose 문법, 빌드하지 않는 서비스 이미지(mongo, cloudflared) 태그가 존재하는지
  - 모든 Dockerfile이 캐시 없이 빌드되는지
  - 학습 서버 이미지의 설치 목록이 lock과 일치하는지, 앱 import·SB3 학습이 되는지, x86 환경에서 Unity 환경 3개가 기동하는지
  - 공개 저장소는 60일간 활동이 없으면 주간 실행이 자동으로 멈추므로, 장기간 감시하려면 Actions 화면에서 다시 활성화해야 합니다.

- **남는 위험** (이번에 고정하지 않은 것)
  - **apt 패키지**: 학습 서버 이미지에 추가로 설치하는 시스템 패키지(약 220개)는 빌드 시점의 Debian bookworm 저장소에서 받습니다. 버전이 바뀔 수 있고, bookworm 지원이 끝나 저장소가 archive로 옮겨지면 설치가 실패할 수 있습니다. (대안: snapshot.debian.org 고정)
  - **이미지 태그**: 버전 태그도 다시 push될 수 있습니다. 실제로 `node:20.19.5-alpine3.22`는 2025-10-22에, `node:20.19.5-bookworm-slim`은 2025-11-18에 다시 push되었습니다. (대안: `@sha256:` digest 고정)
  - **외부 저장소**: torch(CPU 빌드)는 PyTorch 저장소가 계속 제공해야 받을 수 있습니다. 해시가 내용 변조는 막지만 삭제는 막지 못합니다. (대안: 빌드한 이미지를 레지스트리에 digest로 보관)
  - **소스 빌드 결과물**: gym·pettingzoo는 설치 중 빌드되므로 설치 목록과 버전은 같아도 wheel 파일 자체는 빌드마다 바이트 단위로 다릅니다.
  - **frontend `Dockerfile`(serve)**: `serve@14.2.5`의 하위 의존성은 고정되지 않습니다. compose는 이 파일 대신 `Dockerfile.nginx`를 씁니다.

---

### 6. 소개 자료 및 시연 영상

#### 6.1. 프로젝트 소개 자료

[발표 자료](https://github.com/pnucse-capstone2025/Capstone-2025-team-30/blob/main/docs/03.%EB%B0%9C%ED%91%9C%EC%9E%90%EB%A3%8C/2025%EC%A0%84%EA%B8%B0_30_%EA%B0%95%EC%95%84%EC%A7%80%EB%8F%84%ED%95%99%EC%8A%B5_%EB%B0%9C%ED%91%9C%EC%9E%90%EB%A3%8C.pdf)

#### 6.2. 시연 영상

[![시연 영상](https://img.youtube.com/vi/zVyBsuvrQdw/0.jpg)](https://www.youtube.com/watch?v=zVyBsuvrQdw&t=2s)
**YouTube 링크**: [시연 영상 보기](https://www.youtube.com/watch?v=zVyBsuvrQdw&t=2s)

---

### 7. 팀 구성

#### 7.1. 팀원별 소개 및 역할 분담

| 프로필                                                           | 이름  | 역할                 | GitHub                                             |
| ------------------------------------------------------------- | --- | ------------------ | -------------------------------------------------- |
| <img src="https://github.com/skybluesharkk.png" width="60" /> | 심영찬 | 강화학습 알고리즘 개선 & 실험  | [@skybluesharkk](https://github.com/skybluesharkk) |
| <img src="https://github.com/zopa2161.png" width="60" />      | 김동건 | 시뮬레이션 환경 구축(UNITY) | [@zopa2161](https://github.com/zopa2161)             |
| <img src="https://github.com/Hyeonsik-0.png" width="60" />    | 오현식 | 웹 대시보드 & 시각화 개발    | [@Hyeonsik-0](https://github.com/Hyeonsik-0)             |

#### 7.2. 팀원 별 참여 후기

* **심영찬**: 이번 프로젝트를 통해 강화학습과 DQN의 원리를 깊이 있게 학습할 수 있었고, 실제 구현 과정에서 발생하는 다양한 문제들을 해결하며 실전 경험을 쌓을 수 있었다. 특히 장기간의 협업을 통해 팀원들과의 의사소통 방식을 개선하고, 코드 리뷰와 디버깅을 함께 진행하면서 협업 능력을 향상시킬 수 있었다. BC와 Teacher Feedback을 결합한 새로운 접근법을 시도하며 여러 실험을 반복했고, 이 과정에서 하이퍼파라미터 조정과 학습 전략의 중요성을 체감할 수 있었다. 결과적으로 이론과 실습을 병행하며 강화학습에 대한 이해를 넓힐 수 있었던 의미 있는 경험이었다.
* **김동건**: 유니티를 통해 학습 시뮬레이션을 제작하면서 유니티 툴 자체의 숙련도가 올랐을 뿐만 아니라 학습 환경의 구조 설계까지 공부 할 수 있었다. 학습의 관측, 보상 설계를 시뮬레이션의 구조 측면에서만 제작하는것이 아니라 학습의 흐름과 효율에도 집중하여  전반의 이해도를 높였다. StableBaseLines3을 통한 알고리즘 리팩토링을 진행하면서 학습 알고리즘 내부의 흐름과 하이퍼 파라미터가 코드로서 적용되는 방법을 배울 수 있었다. 기술적인 부분 뿐만 아니라 몇 개월동안 하나의 프로젝트를 협업하며 프로젝트의 일정 조절,전체 구조 설계, 깃 협업등에 대한 경험을 얻을 수 있었다.
* **오현식**: 이번 프로젝트에서 백엔드 개발을 담당하며 실험 관리 시스템의 핵심 기능들을 구현하였다. WebSocket 기반 실시간 통신을 처음 적용하면서 많은 시행착오가 있었지만, 점차 익숙해지며 성취감을 느낄 수 있었다. 특히 실험 템플릿 관리, 실행 제어, 에러 핸들링 및 문서화 작업을 통해 팀원들이 활용할 수 있는 안정적인 서비스를 완성하였다. 이번 경험을 통해 새로운 기술을 익히고 문제 해결 능력을 키울 수 있었으며, 더 복잡한 프로젝트에도 도전할 자신감을 얻게 되었다.

---

### 8. 참고 문헌 및 출처

* [OpenAI API Docs] https://platform.openai.com/docs/api-reference/introduction
* [Stable Baselines3] https://github.com/DLR-RM/stable-baselines3
* [Unity ML-Agents] https://github.com/Unity-Technologies/ml-agents
* [Gymnasium] https://gymnasium.farama.org/index.html

---
