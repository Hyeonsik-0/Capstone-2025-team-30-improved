"""장애 스위치, 진행 상태, 전송 기록.

모두 대역 전용이다. 실제 학습 서버에는 없고, BFF도 호출하지 않는다.
기본값은 전부 "장애 없음"이며, 기본값일 때 대역은 실제 서버와 같은 동작만 한다.
"""

from __future__ import annotations

import threading
import time
from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

CONTROL_ACTIONS = ("start", "pause", "resume", "stop")


@dataclass
class ControlFault:
    # 상태를 바꾼 뒤 응답을 늦춘다. (명령은 처리됐는데 응답이 늦는 상황)
    delay_sec: float = 0.0
    # 상태를 바꾸기 전에 실패한다. 실제 서버의 RuntimeError와 같이 500으로 응답한다.
    fail: bool = False


@dataclass
class Faults:
    # 학습 진행 속도. 실제 속도는 Unity 환경·시뮬레이션 배속에 따라 다르므로
    # 측정 시나리오에서 항상 명시적으로 지정한다. (기본값은 실측값이 아니다)
    steps_per_sec: float = 1000.0
    # 학습 루프 멈춤: 진행·지표·완료 신호가 모두 멈춘다. HTTP 요청에는 계속 응답한다.
    stall: bool = False
    # 같은 지표를 두 번 보낸다. (재전송·중복 처리 검증용)
    duplicate_metrics: bool = False
    control: dict[str, ControlFault] = field(
        default_factory=lambda: {a: ControlFault() for a in CONTROL_ACTIONS}
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FaultState:
    """요청 스레드와 학습 스레드가 함께 읽고 쓰므로 잠금으로 보호한다."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._faults = Faults()
        self._crash_requested = False

    def get(self) -> Faults:
        with self._lock:
            return self._faults

    def update(self, patch: dict[str, Any]) -> Faults:
        with self._lock:
            f = self._faults
            for key, value in patch.items():
                if key == "control":
                    for action, ctl in value.items():
                        if action not in CONTROL_ACTIONS:
                            raise ValueError(f"알 수 없는 제어 명령: {action}")
                        cur = f.control[action]
                        for ck, cv in ctl.items():
                            if ck not in ("delay_sec", "fail"):
                                raise ValueError(f"알 수 없는 항목: control.{action}.{ck}")
                            setattr(cur, ck, float(cv) if ck == "delay_sec" else bool(cv))
                elif key == "steps_per_sec":
                    if float(value) <= 0:
                        raise ValueError("steps_per_sec는 0보다 커야 한다")
                    f.steps_per_sec = float(value)
                elif key in ("stall", "duplicate_metrics"):
                    setattr(f, key, bool(value))
                else:
                    raise ValueError(f"알 수 없는 항목: {key}")
            return f

    def reset(self) -> Faults:
        with self._lock:
            self._faults = Faults()
            self._crash_requested = False
            return self._faults

    def request_crash(self) -> None:
        with self._lock:
            self._crash_requested = True

    def consume_crash(self) -> bool:
        with self._lock:
            requested, self._crash_requested = self._crash_requested, False
            return requested

    def crash_pending(self) -> bool:
        with self._lock:
            return self._crash_requested


class SentLog:
    """대역이 BFF로 보낸 요청과 그 결과. 측정의 기준 자료다."""

    def __init__(self, maxlen: int = 20000) -> None:
        self._lock = threading.Lock()
        self._items: deque[dict[str, Any]] = deque(maxlen=maxlen)

    def add(self, **item: Any) -> None:
        item["at"] = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._items.append(item)

    def query(self, run_id: str | None) -> dict[str, Any]:
        with self._lock:
            items = [i for i in self._items if run_id is None or i["run_id"] == run_id]
        by_kind: dict[str, Counter] = {}
        for i in items:
            by_kind.setdefault(i["kind"], Counter())[str(i["result"])] += 1
        return {
            "run_id": run_id,
            "summary": {k: dict(v) for k, v in by_kind.items()},
            "items": items,
        }

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class Progress:
    """현재 학습의 진행 상황 (관찰용)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}

    def set(self, **data: Any) -> None:
        with self._lock:
            self._data.update(data, updated_at=time.time())

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)


class FakeSideChannel:
    """Unity 사이드 채널 대신 받은 명령을 기록만 한다."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._commands: deque[dict[str, Any]] = deque(maxlen=50)

    def send_command(self, cmd: str, msg: str) -> None:
        with self._lock:
            self._commands.append(
                {"cmd": cmd, "msg": msg, "at": datetime.now(timezone.utc).isoformat()}
            )

    def recent(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._commands)


FAULTS = FaultState()
SENT_LOG = SentLog()
PROGRESS = Progress()
