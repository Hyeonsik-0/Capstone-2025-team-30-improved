# 학습 서버 대역 (fake trainer)

실제 학습 서버 컨테이너(`mlagents-gym`) 자리에 들어가, BFF와 주고받는 API 계약만 그대로 따르는 검증용 서버입니다. (이슈 #7)

실제 학습 서버는 Unity 실행 파일이 x86_64 전용이라 ARM 개발 서버에서 띄울 수 없습니다. 그리고 학습 도중 원하는 순간에 장애를 일으킬 수도 없습니다.
이 대역으로 BFF 쪽 보완 작업(SSE 복원, 응답 없음 판정, 제어 명령 처리 순서, DB 장애 대응)을 "장애를 일으키고 숫자로 재는" 방식으로 검증합니다.

## 구조: HTTP 계층은 실제 코드, 서비스 계층만 대역

```text
이미지 /app
├── app/                     ← backend/fast_unity/app 을 그대로 복사 (빌드 컨텍스트 realapp)
│   ├── main.py              실제 (라우터 구성, CORS)
│   ├── api/routes/*.py      실제 (train·algorithm·artifact 라우트)
│   ├── api/algorithm/*.py   실제 (알고리즘·환경 목록과 파라미터 스키마)
│   ├── schemas/training.py  실제 (요청 검증)
│   └── services/unity_service.py  ← 대역으로 교체 (tools/fake-trainer/unity_service.py)
└── fake/                    대역 전용: 학습 루프, 장애 스위치, 제어 주소 /_fake/*
```

- 실제 라우트 `app/api/routes/train.py`가 `UnityService`를 import하는 그 자리를 대역 구현으로 바꿉니다. 그래서 주소, 요청 검증(422), 응답 형식, 예외 시 500 응답은 실제 코드가 그대로 만듭니다.
- 대역 `UnityService`는 실제 코드의 상태 확인 조건과 `RuntimeError` 메시지를 그대로 따릅니다. 학습·추론 루프만 Unity와 SB3 없이 스텝 수를 늘리는 가짜 루프로 바꿨습니다.
- `fake.main`은 구성이 잘못되어 실제 `UnityService`가 들어 있으면 기동하지 않고 실패합니다.
- Python 의존성은 실제 학습 서버 lock과 같은 버전입니다(`requirements.in` 참고).

## 대신하는 것과 대신하지 않는 것

| 실제 학습 서버 컨테이너 | 대역 |
|---|---|
| BFF 명령 수신(시작·일시정지·재개·중지·속도·피드백·추론) | 실제 라우트 + 대역 서비스 |
| 알고리즘·환경 목록, 산출물 확인·다운로드·삭제 | 실제 코드 그대로 |
| 지표 전송, 완료 신호 (학습 서버 → BFF) | 같은 주소·본문·timeout·실패 처리 |
| SB3 학습 계산 | 없음. 진행률로 만든 가짜 지표 값 |
| 모델 파일(학습 종료 시), 학습 로그 CSV(학습 시작 시 생성) | 같은 시점에 만드는 내용 없는 가짜 파일 (산출물 API가 실제처럼 동작하게 하는 용도). CSV는 에피소드 대신 지표마다 한 줄 |
| Unity 시뮬레이션, 사이드 채널 | 없음. 사이드 채널 명령(`init`, `stop`, `resume`, `simSpeed`)은 기록만 함 |
| Unity 영상 → 시그널링 서버(WebRTC) | 없음 (작업 3에서 CI x86으로 확인) |
| Xvfb, VNC(5900, 6080) | 없음 |
| LLM 피드백의 OpenAI 호출 | 없음. 실제 `AddMessage`처럼 메시지 저장 후 재개만 함 |
| 알고리즘별 학습 준비 단계 검증 (예: hf-llm은 cnn_car 전용, teacher 모델 필요) | 없음 |

## 실제 코드 대응표

경로는 `backend/fast_unity/` 기준입니다. 실제 코드가 바뀌면 같은 PR에서 대역도 맞춥니다.

| 동작 | 실제 코드 | 대역 |
|---|---|---|
| 학습 시작, 스레드에서 실행 | `app/services/unity_service.py:39`, `:64` (`run_in_executor`) | `unity_service.py` `start` |
| 일시정지·재개·중지 상태 확인과 예외 | `app/services/unity_service.py:68`, `:88`, `:104` | `unity_service.py` 같은 메서드 |
| 일시정지 중 대기(0.3초 간격), 중지 신호 확인 | `unity/train_util/training_state.py:114-136` | `fake/training.py` `run_fake_training` |
| 지표 전송 시점: on-policy(ppo, a2c)는 롤아웃마다(n_steps), off-policy는 1000스텝마다 | `unity/train_util/real_time_log_callback.py:28`, `:52`, `:54-67`, `:167` | `_metric_interval`, 루프 |
| 지표 본문 `{runId, phase, timestamp, timesteps, metrics[{name, value, group}]}`, 값 반올림 규칙 | `real_time_log_callback.py:103-144` | `_build_metrics_payload`, `_format_metric_value` |
| 지표 전송: 학습 스레드에서 동기 호출, timeout 2초, 실패하면 로그만 남기고 버림(재시도 없음) | `real_time_log_callback.py:177-194` | `_post` |
| 학습 종료: 모델 저장 → 완료 신호 1회 `{status: STOPPED or COMPLETED}`, timeout 5초 | `training_state.py:139-170` | 루프 끝 |
| 종료 후 상태 초기화 | `unity/train/train_runner.py:62`, `app/services/unity_service.py:26` | `finish_train_callback` |
| 학습 중 예외: 완료 신호 없이 상태만 초기화 | `train_runner.py:61-62` (`finally`만 실행) | 장애 스위치 `crash` |
| 환경 생성 후 사이드 채널 `init`(환경 파라미터) | `unity/train/env_factory.py:36`, `:55` | 학습·추론 시작 시 기록 |
| 학습 시작 시 로그 CSV 생성 | `unity/train_util/logger_callback.py:20-34` | `_open_fake_csv` |
| 추론 종료 신호 `test-completed` | `train_runner.py:98-108` | `run_fake_inference` |
| LLM 피드백: 메시지 저장 후 재개 | `unity/train_util/sentiment_feedback_wrapper.py:86-97` | `FakeLLMFeedback` |
| uvicorn 실행 옵션 | `entrypoint.sh:56-58` | `Dockerfile` `CMD` |

지표 이름은 실제 SB3 로거에 남는 키와 같게 맞췄습니다. SB3 2.6.0 소스의 `logger.record` 호출(`ppo.py`, `a2c.py`, `dqn.py`, `sac.py`, `common/base_class.py`)과 저장소에 있는 실제 DQN 학습 로그(`train_logs/teacher_cnn_car_dqn_run1/progress.csv`)로 확인했습니다. 지표 값은 진행률로 만든 가짜 값입니다.

실제와 다른 점이 하나 있습니다. 실제 콜백은 SB3 로거의 현재 값을 읽는데, 로거가 주기적으로 값을 비우므로 off-policy에서는 전송 시점에 따라 `train/*`나 `exploration_rate`가 빠질 수 있습니다. 대역은 항상 같은 키를 보냅니다. BFF는 빠진 키를 null로 처리합니다.

## 실행

저장소 루트에서 실행합니다. 프로젝트 이름(`-p rl-fake`)이 따로라서 실제 구성과 컨테이너·볼륨이 겹치지 않습니다.

```bash
COMPOSE="docker compose -p rl-fake -f docker-compose.yml -f tools/fake-trainer/compose.fake.yml"
$COMPOSE up -d --build mongodb api-server mlagents-gym   # 시그널링·nginx는 띄우지 않음
curl -s http://127.0.0.1:18000/_fake/state                # 대역
curl -s http://127.0.0.1:18080/api/algorithms             # BFF (대역을 거쳐 실제 목록을 받음)
$COMPOSE down -v                                          # 정리 (볼륨까지 삭제)
```

- `.env` 없이 뜨도록 오버라이드가 BFF 환경 변수를 `env.example` 값으로 넣습니다. 기본 compose 파일의 변수가 비어 있다는 경고(`OPENAI_API_KEY` 등)는 무시해도 됩니다.
- 포트는 `127.0.0.1`에만 열립니다. 바꾸려면 `FAKE_TRAINER_PORT`, `FAKE_BFF_PORT`를 지정합니다.

## 장애 스위치 (`/_fake/*`)

대역 전용 주소입니다. BFF는 호출하지 않습니다. 기본값은 모두 "장애 없음"입니다.

| 요청 | 동작 |
|---|---|
| `GET /_fake/state` | 학습·추론 상태, 진행 스텝, 현재 스위치, 최근 사이드 채널 명령 |
| `PUT /_fake/faults` | 바꿀 항목만 보냄. 예: `{"steps_per_sec": 4096}` |
| `POST /_fake/faults/reset` | 모든 스위치를 기본값으로 |
| `POST /_fake/crash` | 진행 중인 학습 스레드를 완료 신호 없이 종료. 학습 루프가 끝난 뒤(모델 저장·완료 신호 전송 중)에는 409로 거절 |
| `GET /_fake/sent?run_id=` | 대역이 BFF로 보낸 요청마다 결과(응답 코드 또는 `error:<예외>`) |
| `DELETE /_fake/sent` | 전송 기록 비우기 |

| 스위치 | 의미 |
|---|---|
| `steps_per_sec` | 학습 진행 속도. 실제 속도는 환경·배속에 따라 다르므로 시나리오에서 항상 지정 (기본값 1000은 실측값이 아님) |
| `stall` | 학습 루프 멈춤. 진행·지표·완료 신호가 모두 멈추고, HTTP 요청에는 계속 응답. 멈춘 동안에는 중지 신호도 확인하지 못함 |
| `duplicate_metrics` | 같은 지표를 두 번 전송 |
| `control.<start,pause,resume,stop>.fail` | 상태를 바꾸기 전에 실패 → 실제 서버와 같이 500 |
| `control.<start,pause,resume,stop>.delay_sec` | 상태를 바꾼 뒤 응답만 늦춤 (명령은 처리됐는데 응답이 늦는 상황) |

## 측정 시나리오

```bash
python3 tools/fake-trainer/scenarios/run_scenarios.py all --out /tmp/fake-results.json
```

| 시나리오 | 확인하는 것 |
|---|---|
| `normal` | 학습 시작 → 지표가 DB·SSE로 전달 → 일시정지·재개·속도 변경 → 완료, 그리고 중지 |
| `bff-restart` | 학습 중 BFF를 `--down-sec`(기본 5초) 동안 내렸다 올렸을 때, 대역이 보낸 지표 중 DB에 남지 않은 개수와 상태 변화 |
| `duplicate` | 같은 지표를 두 번 보냈을 때 두 번째 응답(409)과 DB 중복 여부 |
| `faults` | 학습 루프 멈춤, 학습 스레드 비정상 종료, 제어 명령 지연·실패에 대한 BFF 반응 |

- 판정(`PASS`/`FAIL`)은 두 가지만 봅니다.
  - 대역과 측정 도구가 의도대로 동작하는가
  - 장애가 없을 때의 정상 흐름과 불변 조건(예: 같은 지표가 여러 번 와도 DB에는 한 번만 저장). 보완 작업 뒤에도 유지되어야 하므로 회귀 확인으로 씁니다.
- 장애 상황에서 BFF가 보이는 현재 반응(예: BFF 재시작 후 학습 중인 실험이 FAILED로 바뀜, 중복 전송에 409)은 보완으로 바뀔 대상이라 판정하지 않고 관찰값(`observed`)으로 기록합니다. 보완 작업에서는 이 관찰값을 "보완 전" 숫자로 씁니다.
- 시나리오가 중간에 실패해도 다음 시나리오가 깨끗한 상태에서 시작하도록, 시나리오마다 장애 스위치를 끄고 남은 학습을 중지합니다.
- 화면 차트 대신 차트가 구독하는 BFF SSE 스트림을 직접 받아 셉니다.
- CI(`.github/workflows/fake-trainer.yml`)에서 같은 시나리오를 실행하고, 결과 JSON과 컨테이너 로그를 아티팩트로 남깁니다.

## lock 재생성

```bash
cd tools/fake-trainer
uv pip compile requirements.in --universal --python-version 3.10.12 \
  --exclude-newer 2025-09-30T15:00:00Z --generate-hashes -o requirements.lock
```

ARM 개발 서버와 x86 CI에서 모두 쓰므로 플랫폼을 고정하지 않습니다(`--universal`). CI의 lock 재생성 확인 작업이 이 명령으로 같은 결과가 나오는지 검사합니다.
