"""학습 서버 → BFF 전송 대기열 (이슈 #16)

지표와 완료 신호를 대기열 하나에 넣고, 전송 스레드 하나가 넣은 순서대로 보낸다.

- 학습 루프는 대기열에 넣기만 하고 전송을 기다리지 않는다.
- 재시도 규칙
  - 네트워크 오류·5xx: 1·2·4… 최대 30초 간격으로 다시 보낸다. 성공하면 간격을 처음으로 되돌린다.
  - 2xx, 409(이미 저장됨): 성공으로 본다. BFF는 (runId, phase, timesteps) 유니크 인덱스로 중복 저장을 막는다.
  - 그 밖의 4xx: 다시 보내도 성공할 수 없으므로 버린다.
- 대기열은 최대 1,000개다. 넘치면 가장 오래된 것부터 버리고, 버린 개수를 로그로 남긴다.
- 완료 신호도 같은 대기열에 넣으므로, 먼저 넣은 지표를 다 보낸 뒤에 보낸다.

생존 신호(Heartbeat)는 대기열과 따로 보낸다. (이슈 #21)

SB3·Unity에 의존하지 않는다. 학습 서버 대역(tools/fake-trainer)이 이 파일을 그대로 복사해 쓴다.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, Optional, Union

import requests

MAX_QUEUE = 1000
RETRY_BASE_SEC = 1.0
RETRY_MAX_SEC = 30.0

Result = Union[int, str]  # HTTP 상태 코드, 또는 네트워크 오류면 "error:<예외 이름>"


@dataclass
class Item:
    kind: str  # "metric", "experiment-completed"
    url: str
    payload: Dict[str, Any]
    timeout: float
    meta: Dict[str, Any] = field(default_factory=dict)
    attempts: int = 0


def classify(result: Result) -> str:
    """전송 결과를 'ok'(대기열에서 뺌), 'retry'(다시 보냄), 'drop'(버림)으로 나눈다."""
    if isinstance(result, str):
        return "retry"
    if 200 <= result < 300 or result == 409:
        return "ok"
    if result >= 500:
        return "retry"
    return "drop"


def retry_delay(failures: int) -> float:
    """연속 실패 횟수에 따른 대기 시간: 1, 2, 4, 8, 16초, 이후 30초"""
    return min(RETRY_BASE_SEC * (2 ** failures), RETRY_MAX_SEC)


class BffSender:
    """전송 스레드 하나로 대기열을 순서대로 비운다."""

    def __init__(
        self,
        max_queue: int = MAX_QUEUE,
        on_attempt: Optional[Callable[[Item, Result, str, int], None]] = None,
    ) -> None:
        self._queue: Deque[Item] = deque()
        self._cond = threading.Condition()
        self._max_queue = max_queue
        self._on_attempt = on_attempt  # (항목, 결과, 분류, 걸린 ms). 대역이 전송 기록을 남기는 데 쓴다.
        self.dropped_overflow = 0
        self.dropped_rejected = 0
        self._thread = threading.Thread(target=self._run, name="bff-sender", daemon=True)
        self._thread.start()

    def send(self, kind: str, url: str, payload: Dict[str, Any], timeout: float, **meta: Any) -> None:
        """대기열에 넣고 바로 돌아온다."""
        with self._cond:
            if len(self._queue) >= self._max_queue:
                old = self._queue.popleft()
                self.dropped_overflow += 1
                print(f"[BffSender][WARN] 대기열이 가득 차 가장 오래된 {old.kind}를 버림 "
                      f"(누적 {self.dropped_overflow}개)")
            self._queue.append(Item(kind, url, payload, timeout, dict(meta)))
            self._cond.notify()

    def stats(self) -> Dict[str, int]:
        with self._cond:
            return {
                "pending": len(self._queue),
                "dropped_overflow": self.dropped_overflow,
                "dropped_rejected": self.dropped_rejected,
            }

    def _post(self, item: Item) -> Result:
        try:
            return requests.post(item.url, json=item.payload, timeout=item.timeout).status_code
        except requests.RequestException as e:
            return f"error:{type(e).__name__}"

    def _run(self) -> None:
        failures = 0
        while True:
            with self._cond:
                while not self._queue:
                    self._cond.wait()
                item = self._queue[0]  # 성공하거나 버릴 때까지 맨 앞에 둔다 (순서 유지)

            item.attempts += 1
            t0 = time.monotonic()
            result = self._post(item)
            outcome = classify(result)
            if self._on_attempt:
                try:
                    self._on_attempt(item, result, outcome, round((time.monotonic() - t0) * 1000))
                except Exception as e:  # 기록 실패가 전송을 멈추지 않게 한다
                    print(f"[BffSender][WARN] 전송 기록 실패: {e}")

            if outcome == "retry":
                delay = retry_delay(failures)
                failures += 1
                print(f"[BffSender][WARN] {item.kind} 전송 실패({result}), {delay:.0f}초 뒤 다시 보냄 "
                      f"(시도 {item.attempts}회, 대기 {len(self._queue)}개)")
                time.sleep(delay)
                continue

            failures = 0
            if outcome == "drop":
                self.dropped_rejected += 1
                print(f"[BffSender][ERROR] {item.kind} 전송이 거절되어 버림({result})")
            with self._cond:
                # 기다리는 동안 대기열이 넘쳐 이 항목이 이미 밀려났을 수 있다.
                if self._queue and self._queue[0] is item:
                    self._queue.popleft()


HEARTBEAT_INTERVAL_SEC = 10.0
HEARTBEAT_TIMEOUT_SEC = 3.0


class Heartbeat:
    """학습 스레드가 도는 동안 BFF에 생존 신호를 보낸다. (이슈 #21)

    - 학습 스레드가 시작할 때 start(), 끝날 때(정상 종료·예외 모두) stop()을 부른다.
      학습 스레드가 끝나거나 프로세스가 죽으면 신호가 멈추고, BFF가 일정 시간 뒤 실패로 판정한다.
    - 일시정지 중에도 보낸다. 일시정지 대기도 학습 스레드 안에서 돈다.
    - 학습 스텝마다 보내지 않는다. PPO는 모델을 갱신하는 동안 콜백을 부르지 않아서,
      스텝을 기준으로 하면 정상 학습 중에도 신호가 끊길 수 있다.
    - 지표 대기열과 따로 보내고, 실패해도 다시 보내지 않는다. 다음 신호가 재시도 역할을 한다.
      대기열에 넣으면 BFF가 오래 내려가 있을 때 쌓인 신호가 지표를 밀어낼 수 있다.
    """

    def __init__(
        self,
        url: str,
        interval: float = HEARTBEAT_INTERVAL_SEC,
        timeout: float = HEARTBEAT_TIMEOUT_SEC,
        on_attempt: Optional[Callable[[Result, int], None]] = None,
    ) -> None:
        self.url = url
        self._interval = interval
        self._timeout = timeout
        self._on_attempt = on_attempt  # (결과, 걸린 ms). 대역이 전송 기록을 남기는 데 쓴다.
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="bff-heartbeat", daemon=True)

    def start(self) -> "Heartbeat":
        self._thread.start()
        return self

    def stop(self) -> None:
        """기다리지 않고 돌아온다. 보내는 중인 신호가 있으면 그것까지만 보낸다."""
        self._stop.set()

    def _run(self) -> None:
        failing = False
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                result: Result = requests.post(self.url, json={}, timeout=self._timeout).status_code
            except requests.RequestException as e:
                result = f"error:{type(e).__name__}"
            if self._on_attempt:
                try:
                    self._on_attempt(result, round((time.monotonic() - t0) * 1000))
                except Exception as e:
                    print(f"[Heartbeat][WARN] 전송 기록 실패: {e}")
            ok = isinstance(result, int) and 200 <= result < 300
            # 실패가 이어지는 동안 10초마다 로그가 쌓이지 않게, 상태가 바뀔 때만 남긴다.
            if not ok and not failing:
                print(f"[Heartbeat][WARN] 생존 신호 전송 실패({result}), {self._interval:.0f}초마다 계속 보냄")
            elif ok and failing:
                print("[Heartbeat] 생존 신호 전송 복구")
            failing = not ok
            self._stop.wait(self._interval)


def start_heartbeat(
    base_url: Optional[str],
    run_id: Optional[str],
    on_attempt: Optional[Callable[[Result, int], None]] = None,
) -> Optional[Heartbeat]:
    """BFF 주소와 실험 ID가 있으면 생존 신호를 시작한다. 없으면 None."""
    if not base_url or not run_id:
        return None
    return Heartbeat(f"{base_url}/callbacks/{run_id}/heartbeat", on_attempt=on_attempt).start()


_sender: Optional[BffSender] = None
_sender_lock = threading.Lock()


def get_sender() -> BffSender:
    """프로세스에 하나인 전송 대기열. 학습이 여러 번 실행돼도 같은 대기열과 스레드를 쓴다."""
    global _sender
    with _sender_lock:
        if _sender is None:
            _sender = BffSender()
        return _sender
