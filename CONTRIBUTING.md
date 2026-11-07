# 보완 개발 가이드

졸업과제 종료 후 **1인 보완 개발**을 진행할 때 지키는 최소 규칙입니다.
목적은 기존 시스템의 단일 장애 지점과 정합성 문제를 찾아 개선하고, 그 과정과 검증 결과를 이슈·PR로 남기는 것입니다.
프로젝트 개요와 실행 방법은 `README.md`, `README-Docker.md`를 참고합니다.

## 0. 원칙

- **`main`은 졸업과제 제출 시점 상태로 보존합니다.** 보완 작업은 모두 `develop`에 누적합니다. 원본과 보완본을 비교할 수 있게 하기 위해서입니다.
- **작업 범위는 본인 담당 영역으로 한정합니다.** (아래 2절의 part)
  - `backend/fast_unity`, `algorithms`는 팀원 담당 영역이라 원칙적으로 수정하지 않습니다.
  - 불가피하게 수정해야 하면 최소한으로 변경하고, 이슈와 PR에 이유를 명시합니다.
- **측정하지 않은 수치는 쓰지 않습니다.** "개선됨", "유실 0건" 같은 표현은 검증 결과가 PR에 기록된 경우에만 사용합니다.

## 1. 기본 작업 흐름

1. 이슈를 먼저 만든다. (문제와 근거 코드 위치를 적는다)
2. `develop`에서 이슈 번호 기준으로 브랜치를 만든다.
3. 작은 단위로 커밋한다.
4. 검증 방법대로 확인하고 결과를 기록한다.
5. PR을 만들고 **셀프 리뷰 체크리스트**(8절)를 채운다.
6. 체크리스트를 통과하면 `develop`에 **Squash merge** 하고 이슈를 닫는다.

`main`, `develop`에 직접 push하지 않습니다.

### 저장소 구성

- **원본**: [pnucse-capstone2025/Capstone-2025-team-30](https://github.com/pnucse-capstone2025/Capstone-2025-team-30) (3인 팀 졸업과제, 제출본)
- **이 저장소**: 원본의 전체 커밋 이력을 그대로 옮긴 개인 보완 저장소입니다. fork가 아니므로 원본에 PR이 올라가지 않습니다.
- PR은 항상 `develop`을 대상으로 엽니다.

  ```bash
  gh pr create --base develop
  ```

## 2. 브랜치 컨벤션

형식:

```text
<type>/<part>-<issue-number>-<short-description>
```

part — 작업 영역을 하나 고릅니다.

```text
be      BFF API 서버 (backend/api)
infra   배포·인프라 (docker-compose.yml, nginx/, MongoDB 구성, 백업 스크립트)
fe      프론트엔드 대시보드 (frontend/)
sig     WebRTC 시그널링 서버 (backend/signaling-server)
```

한 브랜치는 한 part만 담습니다. part 경계를 넘는 변경(예: MongoDB 레플리카셋 구성 + BFF 연결 옵션 변경)은 브랜치와 PR을 나누고, 이슈에 순서를 적습니다.

type:

```text
feat      기능 추가
fix       버그 수정
refactor  구조 개선 (동작 변화 없음)
test      테스트 추가/수정
docs      문서 수정
chore     설정/의존성/빌드 작업
```

예시:

```text
fix/be-1-stale-run-detection
chore/infra-2-mongodb-backup
feat/infra-3-mongodb-replica-set
refactor/be-4-sse-change-stream
feat/infra-5-bff-load-balancing
docs/be-6-failover-test-report
```

## 3. 커밋 컨벤션

Conventional Commits 형식을 사용합니다. summary는 한국어로 씁니다.

```text
<type>(<scope>): <summary>
```

scope 예시: `run`, `callback`, `metrics`, `sse`, `db`, `compose`, `nginx`, `backup`, `readme`

예시:

```text
fix(run): 서버 시작 시 실행 중인 run 일괄 FAILED 처리 제거
feat(run): 마지막 메트릭 시각 기반 stale run 주기 점검 추가
chore(backup): mongodump 백업 스크립트 추가
feat(compose): MongoDB 3노드 레플리카셋 구성
docs(readme): 장애 테스트 절차와 결과 기록
```

규칙:

- 한 커밋은 하나의 논리적 변경만 포함합니다.
- 로직 변경과 단순 포맷팅을 섞지 않습니다.
- `.env`, `OPENAI_API_KEY`, `CLOUDFLARE_TOKEN`, 백업 덤프 파일은 커밋하지 않습니다.

## 4. 이슈 작성 규칙

이슈 제목:

```text
[<PART> <Type>] <작업 요약>
```

`<PART>`는 브랜치의 part를 대문자로 씁니다 — `BE` · `INFRA` · `FE` · `SIG`.

예시:

```text
[BE Fix] BFF 재시작 시 학습 중인 run이 FAILED로 처리되는 문제
[INFRA Chore] MongoDB 정기 백업 및 복구 절차 추가
[INFRA Feat] MongoDB 레플리카셋 구성
[BE Refactor] SSE 이벤트 전달을 Change Stream 기반으로 변경
[INFRA Feat] BFF 다중 인스턴스 및 nginx 로드밸런싱
```

이슈에는 최소한 아래 내용을 작성합니다.

- **목적**: 이 작업으로 줄이려는 위험 (예: 단일 장애 지점, 상태 불일치)
- **현재 문제**: 발생 조건, 결과, 근거 코드 위치 (`파일:라인`)
- **작업 범위**: 할 것 / **하지 않을 것** (범위 밖과 그 이유)
- **완료 기준**: 확인 가능한 문장으로 작성
- **검증 방법**: 재현 절차, 장애 주입 방법, 측정 항목

## 5. PR 작성 규칙

PR 제목:

```text
[<PART>] <type>(<scope>): <summary>
```

예시:

```text
[BE] fix(run): 생존 확인 기반으로 실행 중 run 정리
[INFRA] chore(backup): MongoDB 백업·복구 스크립트 추가
[INFRA] feat(compose): MongoDB 3노드 레플리카셋 구성
[BE] refactor(sse): Change Stream 기반 이벤트 브로드캐스트
```

PR 본문에 `Closes #<이슈 번호>`를 적고, 최소한 아래 내용을 작성합니다.

- **작업 요약**
- **변경 이유**: 이슈의 어떤 문제를 어떻게 해결하는지
- **변경 전 / 후 동작**: 같은 시나리오에서 무엇이 달라지는지
- **계약 변경 여부**: 프론트엔드 API, FastAPI 콜백(`/callbacks/*`, `/training-metrics`) 요청·응답 변화
- **데이터 스키마 / 환경변수 / compose 변경 여부**
- **검증 결과**: 실행한 명령어와 실제 출력, 측정값
- **한계와 남은 과제**: 해결하지 않은 부분과 이유

## 6. PR 크기 기준

- 하나의 PR은 하나의 목적만 가진다.
- 기능 변경과 대형 리팩토링을 섞지 않는다.
- 상태 전이(run status) 로직, DB 연결 설정, 인스턴스 구성 변경은 검증 결과를 반드시 포함한다.

큰 작업은 아래처럼 나눕니다.

```text
1. 인프라 구성 (예: 레플리카셋)
2. 애플리케이션 연결 설정 변경
3. 로직 변경 (예: Change Stream 적용)
4. 장애 테스트 및 결과 기록
5. 문서 업데이트
```

## 7. 검증 규칙

현재 `backend/api`, `frontend`에는 자동화된 테스트가 없습니다. 테스트 도구 도입이 필요하면 별도 이슈로 진행합니다.

그 전까지는 아래 방식으로 검증하고, PR에 명령어와 결과를 그대로 기록합니다.

- **빌드 확인**: `npm run build` (변경한 패키지)
- **통합 확인**: `docker compose up --build`로 전체 스택을 띄워 시나리오 실행
- **장애 주입**: 이중화·정합성 관련 변경은 실제로 장애를 일으켜 확인
  - 예: `docker compose kill api-server`, `docker compose stop <mongo primary>`
  - 측정 항목 예: 재선출 시간, 유실된 메트릭 수, run 상태 전이, SSE 재연결 여부
- **변경 전 / 후 비교**: 같은 시나리오를 `main`(원본)과 작업 브랜치에서 각각 실행해 차이를 기록

## 8. 셀프 리뷰 체크리스트

리뷰어가 없으므로 PR 머지 전에 아래 항목을 직접 확인하고 PR에 체크합니다.

- [ ] 프론트엔드 API와 FastAPI 콜백 계약이 깨지지 않았는가
- [ ] run 상태 전이가 조건부 업데이트로 처리되어, 여러 요청·여러 인스턴스가 동시에 실행해도 안전한가
- [ ] BFF, MongoDB, FastAPI 중 하나가 멈췄다 돌아올 때의 동작을 확인했는가
- [ ] 이미 저장된 데이터가 변경·삭제되지 않는가 (마이그레이션이 필요하면 절차를 적었는가)
- [ ] API Key, 토큰, 접속 정보가 로그·코드·에러 응답에 남지 않는가
- [ ] 검증 결과가 실제 실행 출력에 근거하는가
- [ ] README 또는 문서 수정이 필요한 변경인가

## 9. 금지 사항

- `main`, `develop` 직접 push
- `main` 브랜치 변경 (제출본 보존)
- `.env`, API Key, Cloudflare 토큰, 백업 덤프 파일 커밋
- 팀원 담당 영역(`backend/fast_unity`, `algorithms`)을 이슈 없이 수정
- 프론트엔드 API, FastAPI 콜백의 응답 필드 삭제·타입 변경을 계약 변경 표기 없이 진행
- 측정하지 않은 수치나 검증하지 않은 결과를 문서·PR에 기록
