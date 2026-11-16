# 브라우저 측정 도구 (browser-e2e)

실시간 차트에 실제로 들어간 지표를 브라우저에서 읽어 DB와 비교합니다. (이슈 #9)
SSE 재연결과 끊긴 구간 복원은 브라우저(EventSource)와 프론트엔드 상태 저장소에서 일어납니다. 그래서 서버 쪽 기록(대역 전송 기록, DB, SSE 직접 수신)만으로는 차트에서 빠진 스텝을 셀 수 없습니다.

시뮬레이터 영상(WebRTC)의 연결과 재연결도 잽니다. (이슈 #18) 실제 Unity 대신 가짜 Unity 송출기를 브라우저 페이지로 띄워 시그널링 서버에 붙이고, 대시보드에 영상이 실제로 나오는지 봅니다.

## 구성

```text
browser-e2e (Playwright, Chromium)
   ├─ 대시보드 페이지
   │     │  http://toxiproxy:8080/?runId=...  (API, SSE, 화면 쪽 시그널링 /ws)
   │     ▼
   │  toxiproxy [web] ── 장애 주입 지점 (브라우저–nginx 사이)
   │     │
   │     ▼
   │  nginx (측정용 빌드: VITE_E2E_EXPOSE_STORE=true) → api-server(BFF) → mongodb
   │     │ /ws                                            ▲
   │     ▼                               mlagents-gym (학습 서버 대역) ─ 지표 전송
   │  signaling-server
   │     ▲
   │  toxiproxy [signaling] ── 장애 주입 지점 (Unity 쪽 시그널링)
   │     │  ws://toxiproxy:8081
   └─ 가짜 Unity 송출기 페이지 (fake_unity.js)
         영상은 두 페이지 사이에 WebRTC로 직접 흐른다
```

- 학습 서버 대역 구성(`tools/fake-trainer/compose.fake.yml`) 위에 `compose.e2e.yml`을 덮어씁니다.
- nginx는 측정용 빌드 옵션을 켜고 빌드합니다. 이 빌드에서만 `window.__E2E__.experimentStore`로 차트 데이터(`trainingMetrics`)를 읽을 수 있습니다. 옵션을 끈 일반 빌드 결과물에는 노출 코드가 없습니다(PR #11).
- 장애는 브라우저와 nginx 사이에만 넣습니다. 학습 서버 대역 → BFF → DB 경로는 정상이라서, 대역이 보낸 지표는 모두 DB에 저장됩니다.
- 프로젝트 이름은 `rl-e2e`입니다. 웹 확인용 구성(`rl-fake`)과 이미지가 섞이지 않게 나눴습니다. 두 구성은 같은 호스트 포트(18000, 18080, 18081)를 쓰므로 동시에 띄우지 않습니다.

## 실행

저장소 루트에서 실행합니다.

```bash
E2E="docker compose -p rl-e2e -f docker-compose.yml -f tools/fake-trainer/compose.fake.yml -f tools/browser-e2e/compose.e2e.yml"
$E2E up -d --build --wait mongodb api-server mlagents-gym signaling-server nginx toxiproxy
mkdir -p tools/browser-e2e/out
$E2E run --rm --build --user "$(id -u):$(id -g)" browser-e2e all --out /out/results.json
$E2E down -v
```

- 결과는 `tools/browser-e2e/out/`(git 제외)에 남습니다. 다른 곳에 두려면 `E2E_OUT_DIR`을 지정합니다.
- `all`은 SSE 시나리오(`sse`: s0~s3)와 영상 시나리오(`webrtc`: w0~w5)를 모두 실행합니다. 묶음 이름이나 시나리오 이름(`s1-short-drop`, `w2-unity-restart` 등)으로 일부만 실행할 수 있습니다.
- 장애 시간은 `--drop-sec`, `--long-drop-sec`, `--stall-sec`, `--unity-late-sec`, `--unity-away-sec`, `--signaling-down-sec`, `--web-down-sec`로 바꿉니다.
- CI(`.github/workflows/browser-e2e.yml`)는 x86 러너에서 같은 명령을 실행하고, 결과와 컨테이너 로그를 아티팩트로 남깁니다.

## 시나리오

| 시나리오 | 장애 | 지표 속도 |
|---|---|---|
| `s0-normal` | 없음 | 초당 4개, 40개 |
| `s1-short-drop` | 연결 끊김 10초 (프록시를 껐다 켬: 열린 연결을 닫고, 새 연결은 거부) | 초당 4개 |
| `s2-long-drop` | 연결 끊김 20초. 끊긴 동안 쌓이는 지표가 BFF 재전송 버퍼(50개)를 넘게 함 | 초당 4개 |
| `s3-half-open` | 연결은 둔 채 서버→브라우저 데이터만 90초 동안 버림 (timeout toxic, timeout 0) | 초당 1개 |

시나리오마다 순서는 같습니다.

1. 학습 루프를 멈춘 상태(`stall`)로 실험을 시작합니다.
2. 대시보드를 `?runId=`로 열고, 페이지 진입 복원과 SSE 연결(`connected`)이 끝날 때까지 기다립니다.
   - 페이지 진입 복원과 SSE 수신이 겹치는 경합은 #10에서 다룰 대상이라, 이 측정에서는 일부러 피합니다.
3. 멈춤을 풀어 지표를 보내기 시작합니다. 지표가 8개 저장되면 장애를 넣고, 정한 시간이 지나면 복구합니다.
4. 학습이 끝나고 화면이 완료를 알아채면, 차트의 스텝 목록과 DB의 스텝 목록을 비교합니다.

지표 속도는 실제 학습보다 빠르게 잡았습니다. 측정하는 것은 "끊긴 동안 쌓인 지표 개수"와 "재연결 동작"이라서, 시간을 줄여도 결과의 의미는 같습니다.

## 측정하는 값

- **차트**: `trainingMetrics`의 `timesteps` 목록. 실시간 차트가 그리는 배열입니다.
- **DB**: BFF 지표 조회 API(`GET /api/runs/:id/training-metrics`). DB에서 runId로 찾아 스텝 순으로 정렬해 그대로 돌려주며, 기존 측정 시나리오도 같은 방법으로 읽습니다.
- **SSE 연결 기록**: 페이지 코드가 실행되기 전에 `EventSource`를 감싸서, 연결마다 생성·open·error·message·close 시각과 `readyState`를 남깁니다. 동작은 바꾸지 않습니다. 시나리오별 `*.sse.json`에 저장됩니다.

판정(`PASS`/`FAIL`)은 아래 항목만 봅니다.

- 측정 도구와 장애 주입이 의도대로 동작했는가
  - 지표가 나오기 전에 SSE가 연결됐는가
  - 대역의 지표 전송이 모두 성공하고 DB에 모든 스텝이 있는가
  - 끊김 장애에서 SSE 연결이 실제로 끊겼는가
  - 반쯤 죽은 연결 장애에서 데이터가 실제로 멈췄는가
- 장애가 없을 때 차트의 스텝 목록이 DB와 같은가 (`s0-normal`)

장애 상황의 결과는 보완으로 바뀔 대상이라서 판정하지 않고 관찰값(`observed`)으로 기록합니다. 차트에서 빠진 스텝 수, 그중 장애 중에 보낸 것의 수, 재연결까지 걸린 시간, 장애 중 브라우저가 이상을 알아챘는지가 여기에 들어갑니다.

## 시뮬레이터 영상(WebRTC) 시나리오

| 시나리오 | 상황 |
|---|---|
| `w0-normal` | Unity가 먼저 시그널링에 붙어 있고, 사용자가 '재연결' 버튼을 누름 |
| `w1-unity-late` | Unity가 붙기 전에 버튼을 누르고, `--unity-late-sec`(기본 10초) 뒤 Unity가 붙음 |
| `w2-unity-restart` | 영상이 나오는 중 Unity가 종료되고(시그널링·영상 모두 끊김), `--unity-away-sec`(기본 10초) 뒤 다시 붙음 |
| `w3-media-drop` | 영상이 나오는 중 영상 경로만 끊김 (Unity는 시그널링에 그대로 붙어 있음) |
| `w4-unity-signaling-drop` | 영상이 나오는 중 Unity 쪽 시그널링 연결만 `--signaling-down-sec`(기본 10초) 동안 끊김. 영상 경로는 그대로 |
| `w5-web-drop` | 영상이 나오는 중 브라우저–nginx 사이가 `--web-down-sec`(기본 10초) 동안 끊김. 화면 쪽 시그널링과 API도 함께 끊김 |

- **연결 시작**: 페이지에 다시 들어오면 브라우저 자동 재생 정책 때문에 버튼으로 연결합니다. 그래서 모든 시나리오는 대시보드를 `?runId=`로 연 뒤 '재연결' 버튼을 눌러 시작합니다.
- **가짜 Unity 송출기**(`fake_unity.js`): 실제 Unity Render Streaming과 같은 순서로 동작합니다.
  - 화면 offer(데이터 채널만)에 answer로 답합니다.
  - 영상 트랙(캔버스, 15fps)을 붙이고 송출기가 다시 offer를 보냅니다.
  - 시그널링 WebSocket이 끊기면 3초마다 다시 붙습니다.
- **측정값**: 대시보드 페이지에 기록 스크립트(`rtc_hook.js`)를 먼저 넣어 다음을 남깁니다.
  - RTCPeerConnection 생성, ICE 상태, close
  - 시그널링 WebSocket open과 close
  - 연결 상태 문구와 영상 재생 여부: 250ms마다 확인하고 바뀔 때만 남깁니다. 재생 여부는 디코딩한 프레임 수가 늘었는지로 판단합니다.
- **관찰값**: 이 기록에서 계산합니다.
  - 버튼 뒤 영상까지 걸린 시간
  - 영상 없이 '연결됨'으로 표시된 시간
  - 장애 뒤 화면 상태가 바뀐 시각, 복구 뒤 영상이 다시 나온 시각
  - 새로 만든 연결 수
- 시나리오별 원본 기록은 `*.rtc.json`(대시보드와 송출기 양쪽)에 남습니다.

## 장애 주입의 한계

- **반쯤 죽은 연결**: Toxiproxy는 데이터를 버리는 방식으로 흉내 냅니다. 실제 네트워크에서는 TCP가 재전송하지만 여기서는 버린 데이터가 돌아오지 않습니다.
  - toxic을 지우면 Toxiproxy가 그 연결들을 닫습니다(Toxiproxy 로그로 확인). 그래서 복구 시점에 브라우저는 연결 오류를 받고 다시 연결합니다.
  - 이 시나리오에서 판단하는 것은 "멈춘 동안 브라우저가 알아챘는가"입니다. 복구 뒤의 동작은 실제와 다를 수 있습니다.
- 페이지의 다른 요청(상태 폴링 등)도 같은 프록시를 지나므로 같이 끊기거나 멈춥니다. 실제 네트워크 장애와 같습니다.
- 브라우저는 Chromium(headless) 하나만 씁니다. EventSource의 자동 재연결 간격은 브라우저마다 다를 수 있습니다.
- **가짜 Unity 송출기는 실제 Unity가 아닙니다.** 메시지 순서와 형식은 맞췄지만, 실제 Unity의 시그널링 재접속 간격과 영상 인코딩은 다를 수 있습니다. 실제 Unity 확인은 별도 이슈에서 합니다.
- **Unity 종료(`w2`)**: 송출기가 영상 연결을 닫고(`pc.close()`) WebSocket을 닫습니다. 프로세스가 갑자기 죽어 아무 신호도 보내지 못하는 경우와는 다를 수 있습니다. 다만 두 경우 모두 시그널링 서버는 WebSocket이 닫힌 것을 보고 화면에 `disconnect`를 보냅니다.
- **시그널링 서버 재시작은 재현하지 않습니다.** 측정 컨테이너는 Docker를 다룰 수 없습니다. 그리고 서버가 살아 있으면 한쪽 연결이 끊길 때 반대쪽에 `disconnect`를 알리므로, 서버가 죽어 아무것도 알리지 못하는 상황과 다릅니다. 대신 한쪽 연결만 끊기는 두 경우(`w4`: Unity 쪽, `w5`: 브라우저 쪽)를 잽니다.
- 같은 Docker 네트워크 안에서 ICE 후보가 mDNS 이름 대신 실제 주소로 나오도록 Chromium을 `--disable-features=WebRtcHideLocalIpsWithMdns`로 띄웁니다. 그래서 다른 네트워크 사이의 연결(NAT, TURN)은 재현하지 않습니다.

## 버전 고정

- 브라우저: `mcr.microsoft.com/playwright/python:v1.64.1-noble` (태그 + digest 고정, `Dockerfile`)
- Python 패키지: 이미지에는 브라우저만 들어 있어서, 같은 버전의 Playwright를 해시 포함 `requirements.lock`으로 설치합니다.
- Toxiproxy: `ghcr.io/shopify/toxiproxy:2.13.0` (태그 + digest 고정, `compose.e2e.yml`)

이미지 태그를 바꾸면 `requirements.in`의 버전도 맞추고 lock을 다시 만듭니다. CI의 lock 재생성 확인 작업이 아래 명령으로 같은 결과가 나오는지 검사합니다.

```bash
cd tools/browser-e2e
uv pip compile requirements.in --universal --python-version 3.12 \
  --exclude-newer 2026-11-01T00:00:00Z --generate-hashes -o requirements.lock
```
