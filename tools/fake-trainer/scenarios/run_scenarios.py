#!/usr/bin/env python3
"""학습 서버 대역으로 BFF 동작을 재는 시나리오 (이슈 #7).

표준 라이브러리만 쓴다. 저장소 루트에서 대역 구성을 띄운 뒤 실행한다.

    docker compose -p rl-fake -f docker-compose.yml -f tools/fake-trainer/compose.fake.yml \\
      up -d --build mongodb api-server mlagents-gym
    python3 tools/fake-trainer/scenarios/run_scenarios.py all --out /tmp/fake-results.json

시나리오
  normal       학습 시작 → 지표 → 일시정지·재개 → 완료, 중지, 시뮬레이션 속도
  bff-restart  학습 중 BFF를 N초 내렸다 올렸을 때 유실되는 지표 수 (기준값)
  bff-freeze   학습 중 BFF가 N초 응답을 멈췄을 때, 대역이 실패로 기록한 지표 중 실제로 저장된 수
  bff-down-at-end  학습이 끝날 때 BFF가 내려가 있으면 마지막 지표와 완료 신호가 도착하는지 (이슈 #16)
  duplicate    같은 지표를 두 번 보냈을 때 BFF 응답과 DB 중복 여부
  faults       학습 루프 멈춤, 학습 스레드 비정상 종료, 제어 명령 지연·실패에 대한 BFF 반응
  trainer-crash   학습 스레드가 완료 신호 없이 끝났을 때 실험이 FAILED로 바뀌기까지 걸린 시간 (이슈 #21)
  trainer-paused  일시정지가 오래 이어져도 PAUSED로 남는지, 그 상태에서 학습 스레드가 끝나면 FAILED까지 걸린 시간
  trainer-exit    학습 서버 프로세스가 죽었을 때 컨테이너가 다시 뜨는지, FAILED까지 걸린 시간

각 시나리오는 판정(check)과 관찰값(observed)을 함께 출력한다.
- 판정: 대역·측정 도구가 의도대로 동작하는가, 그리고 장애가 없을 때의 정상 흐름과 불변 조건
  (보완 작업 뒤에도 유지되어야 하므로 회귀 확인으로 쓴다)
- 관찰값: 장애 상황에서 BFF가 보이는 현재 반응. 보완으로 바뀔 대상이라 판정하지 않고 기록만 한다.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILES = ["docker-compose.yml", "tools/fake-trainer/compose.fake.yml"]


class Ctx:
    def __init__(self, args: argparse.Namespace) -> None:
        self.bff = args.bff.rstrip("/")
        self.fake = args.fake.rstrip("/")
        self.project = args.project
        self.tag = str(int(time.time()))


# ---------------------------------------------------------------- HTTP

def http(method: str, url: str, body: Any = None, timeout: float = 30) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            status = resp.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode(), e.code
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def must(status_body: tuple[int, Any], ok: tuple[int, ...] = (200, 201)) -> Any:
    status, body = status_body
    if status not in ok:
        raise RuntimeError(f"예상하지 못한 응답 {status}: {body}")
    return body


def wait_until(cond: Callable[[], Any], timeout: float, interval: float = 0.2, what: str = "") -> Any:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = cond()
        if value:
            return value
        time.sleep(interval)
    raise TimeoutError(f"{timeout}초 안에 조건을 만족하지 못함: {what}")


# ---------------------------------------------------------------- docker

def compose(ctx: Ctx, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-p", ctx.project]
    for f in COMPOSE_FILES:
        cmd += ["-f", f]
    return subprocess.run(cmd + list(args), cwd=REPO_ROOT, check=check,
                          capture_output=True, text=True)


def mongo_eval(ctx: Ctx, js: str) -> Any:
    out = compose(ctx, "exec", "-T", "mongodb", "mongosh", "--quiet", "rl_dashboard",
                  "--eval", f"print(EJSON.stringify({js}))").stdout.strip()
    return json.loads(out.splitlines()[-1])


# ---------------------------------------------------------------- BFF·대역 조작

def fake_faults(ctx: Ctx, patch: dict[str, Any] | None = None, reset: bool = False) -> Any:
    if reset:
        must(http("POST", f"{ctx.fake}/_fake/faults/reset"))
    if patch:
        return must(http("PUT", f"{ctx.fake}/_fake/faults", patch))
    return must(http("GET", f"{ctx.fake}/_fake/faults"))


def fake_state(ctx: Ctx) -> Any:
    return must(http("GET", f"{ctx.fake}/_fake/state"))


def fake_sent(ctx: Ctx, run_id: str) -> Any:
    return must(http("GET", f"{ctx.fake}/_fake/sent?run_id={run_id}"))


def start_run(ctx: Ctx, name: str, algorithm: str, alg_config: dict[str, Any], total: int) -> str:
    tpl = must(http("POST", f"{ctx.bff}/api/experiment-templates", {
        "name": f"{name}-{ctx.tag}", "envName": "ball", "algName": algorithm,
        "envConfig": {}, "algConfig": alg_config,
    }))
    tid = tpl["data"]["templateId"]
    run = must(http("POST", f"{ctx.bff}/api/experiment-templates/{tid}/runs", {"totalSteps": total}))
    return run["data"]["runId"]


def run_status(ctx: Ctx, run_id: str) -> str | None:
    status, body = http("GET", f"{ctx.bff}/api/runs/{run_id}/status", timeout=5)
    return body["data"]["status"] if status == 200 else None


def db_timesteps(ctx: Ctx, run_id: str) -> list[int]:
    body = must(http("GET", f"{ctx.bff}/api/runs/{run_id}/training-metrics"))
    return [m["timesteps"] for m in body["data"]["metrics"]]


def ok_metric_count(ctx: Ctx, run_id: str) -> int:
    return fake_sent(ctx, run_id)["summary"].get("metric", {}).get("201", 0)


def completion_items(ctx: Ctx, run_id: str) -> list[dict[str, Any]]:
    return [i for i in fake_sent(ctx, run_id)["items"] if i["kind"] == "experiment-completed"]


class SSEReader(threading.Thread):
    """차트가 구독하는 BFF SSE 스트림을 열어 지표 이벤트의 timesteps를 모은다."""

    def __init__(self, url: str) -> None:
        super().__init__(daemon=True)
        self.url = url
        self.timesteps: list[int] = []
        self.connected = threading.Event()
        self.closed_reason: str | None = None

    def run(self) -> None:
        try:
            with urllib.request.urlopen(self.url, timeout=120) as resp:
                for raw in resp:
                    line = raw.decode().strip()
                    if not line.startswith("data:"):
                        continue
                    data = json.loads(line[5:])
                    if data.get("type") == "connected":
                        self.connected.set()
                    elif "timesteps" in data:
                        self.timesteps.append(data["timesteps"])
        except Exception as e:  # 연결이 끊기면 끝낸다
            self.closed_reason = type(e).__name__
        else:
            self.closed_reason = "eof"


class StatusRecorder(threading.Thread):
    """BFF가 보여주는 실험 상태의 변화를 시간과 함께 기록한다. (화면의 3초 폴링과 같은 정보)"""

    def __init__(self, ctx: Ctx, run_id: str, interval: float = 0.5) -> None:
        super().__init__(daemon=True)
        self.ctx, self.run_id, self.interval = ctx, run_id, interval
        self.t0 = time.monotonic()
        self.changes: list[tuple[float, str | None]] = []
        self._stop_event = threading.Event()

    def run(self) -> None:
        last = object()
        while not self._stop_event.is_set():
            try:
                s = run_status(self.ctx, self.run_id)
            except Exception:
                s = "BFF 응답 없음"
            if s != last:
                self.changes.append((round(time.monotonic() - self.t0, 1), s))
                last = s
            time.sleep(self.interval)

    def stop(self) -> list[tuple[float, str | None]]:
        self._stop_event.set()
        self.join(timeout=5)
        return self.changes


def wait_finished(ctx: Ctx, run_id: str, timeout: float) -> None:
    wait_until(lambda: completion_items(ctx, run_id), timeout, 0.5, "완료 신호 전송")


def wait_trainer_idle(ctx: Ctx, timeout: float = 30) -> None:
    wait_until(lambda: fake_state(ctx)["current_run_id"] is None, timeout, 0.3, "대역 학습 종료")


def wait_sender_idle(ctx: Ctx, timeout: float = 60) -> None:
    """대역의 BFF 전송 대기열이 빌 때까지 기다린다. (이슈 #16 이전 대역에는 대기열이 없다)"""
    def idle() -> bool:
        sender = fake_state(ctx).get("sender")
        return sender is None or sender["pending"] == 0
    wait_until(idle, timeout, 0.5, "전송 대기열 비움")


SAVED = (201, 409)  # BFF가 지표를 저장했거나 이미 저장되어 있음


def _at(item: dict[str, Any]) -> float:
    from datetime import datetime
    return datetime.fromisoformat(item["at"]).timestamp()


def completion_after_last_metric(items: list[dict[str, Any]]) -> bool | None:
    """성공한 완료 신호가 마지막으로 저장된 지표보다 뒤에 도착했는가. 완료 신호가 성공하지 못했으면 None."""
    done = [_at(i) for i in items if i["kind"] == "experiment-completed" and i["result"] == 200]
    saved = [_at(i) for i in items if i["kind"] == "metric" and i["result"] in SAVED]
    if not done:
        return None
    return not saved or min(done) > max(saved)


def db_duplicates(ctx: Ctx, run_id: str) -> int:
    db = db_timesteps(ctx, run_id)
    return len(db) - len(set(db))


def cleanup(ctx: Ctx) -> None:
    """시나리오가 중간에 실패해도 다음 시나리오가 깨끗한 상태에서 시작하게 한다.
    장애 스위치를 끄고, 남은 학습·추론이 있으면 대역에 직접 중지를 보낸 뒤 끝날 때까지 기다린다."""
    try:
        fake_faults(ctx, reset=True)
        run_id = fake_state(ctx)["current_run_id"]
        if run_id:
            http("POST", f"{ctx.fake}/runs/{run_id}:stop")
            try:
                wait_trainer_idle(ctx, 30)
            except TimeoutError:
                http("POST", f"{ctx.fake}/_fake/crash")
                wait_trainer_idle(ctx, 10)
        # 남은 재전송이 다음 시나리오의 전송 앞을 막지 않게 한다.
        wait_sender_idle(ctx)
    except Exception as e:
        print(f"  정리 중 경고: {type(e).__name__}: {e}")


# ---------------------------------------------------------------- 시나리오

def scenario_normal(ctx: Ctx) -> dict[str, Any]:
    fake_faults(ctx, {"steps_per_sec": 2048}, reset=True)
    n_steps, n_metrics = 2048, 10
    run_id = start_run(ctx, "normal-ppo", "ppo", {"n_steps": n_steps}, n_steps * n_metrics)
    sse = SSEReader(f"{ctx.bff}/api/training-metrics/stream/{run_id}")
    sse.start()
    sse.connected.wait(5)

    wait_until(lambda: len(db_timesteps(ctx, run_id)) >= 3, 30, what="지표 3개 저장")
    # 사이드 채널 기록은 모든 실행이 함께 쓰므로, 이 시점 이후의 명령만 센다.
    seq_before = max((c["seq"] for c in fake_state(ctx)["side_channel"]), default=0)
    t_pause = time.monotonic()
    pause_resp = http("POST", f"{ctx.bff}/api/runs/{run_id}/pause")
    pause_latency = round(time.monotonic() - t_pause, 3)
    paused_state = fake_state(ctx)["train"]["is_paused"]
    count_at_pause = len(db_timesteps(ctx, run_id))
    time.sleep(3)
    count_after_hold = len(db_timesteps(ctx, run_id))
    status_while_paused = run_status(ctx, run_id)
    resume_resp = http("POST", f"{ctx.bff}/api/runs/{run_id}/resume")
    speed_resp = http("POST", f"{ctx.bff}/api/runs/{run_id}/sim-speed", {"speed": 3})
    side_channel = [c["cmd"] for c in fake_state(ctx)["side_channel"] if c["seq"] > seq_before]

    wait_finished(ctx, run_id, 60)
    wait_until(lambda: run_status(ctx, run_id) == "COMPLETED", 15, what="COMPLETED 반영")
    time.sleep(1)
    expected = [n_steps * k for k in range(1, n_metrics + 1)]
    db = db_timesteps(ctx, run_id)
    sent = fake_sent(ctx, run_id)
    artifacts = mongo_eval(ctx, f'db.runs.findOne({{_id: ObjectId("{run_id}")}}).artifacts')

    # 중지: 진행률 30% 미만에서 멈추면 BFF가 STOPPED로 기록한다. (callbacks.ts)
    stop_run = start_run(ctx, "normal-stop", "ppo", {"n_steps": n_steps}, n_steps * n_metrics)
    wait_until(lambda: len(db_timesteps(ctx, stop_run)) >= 2, 30, what="지표 2개 저장")
    stop_resp = http("POST", f"{ctx.bff}/api/runs/{stop_run}/stop")
    wait_finished(ctx, stop_run, 30)
    wait_trainer_idle(ctx)
    time.sleep(1)
    stop_completion = completion_items(ctx, stop_run)

    # off-policy(DQN): 1000스텝마다 phase "step"으로 전송, 탐험 지표는 exploration_rate
    dqn_run = start_run(ctx, "normal-dqn", "dqn", {}, 5000)
    wait_finished(ctx, dqn_run, 30)
    wait_trainer_idle(ctx)
    time.sleep(1)
    dqn_metrics = must(http("GET", f"{ctx.bff}/api/runs/{dqn_run}/training-metrics"))["data"]["metrics"]

    checks = {
        "fake_metrics_201": sent["summary"]["metric"] == {"201": n_metrics},
        "db_equals_expected": db == expected,
        "sse_equals_expected": sorted(sse.timesteps) == expected,
        "completion_sent_once_200": [i["result"] for i in sent["items"] if i["kind"] == "experiment-completed"] == [200],
        "status_completed": run_status(ctx, run_id) == "COMPLETED",
        "artifacts_recorded": bool(artifacts and artifacts.get("modelZip") and artifacts.get("logsCsv")),
        "pause_reached_trainer": paused_state is True and pause_resp[0] == 200,
        "no_metrics_while_paused": count_after_hold == count_at_pause,
        "resume_and_speed_reached_trainer": resume_resp[0] == 200 and speed_resp[0] == 200
            and "resume" in side_channel and "simSpeed" in side_channel,
        "stop_sends_stopped": [i.get("status") for i in stop_completion] == ["STOPPED"],
        "dqn_step_metrics_saved": [m["timesteps"] for m in dqn_metrics] == [1000, 2000, 3000, 4000, 5000]
            and all(m["phase"] == "step" for m in dqn_metrics)
            and all(m["chartMetrics"].get("exploration") is not None for m in dqn_metrics),
    }
    return {
        "checks": checks,
        "observed": {
            "run_id": run_id,
            "expected_metrics": n_metrics,
            "fake_sent_summary": sent["summary"],
            "db_count": len(db),
            "sse_count": len(sse.timesteps),
            "pause_latency_sec": pause_latency,
            "status_while_paused": status_while_paused,
            "artifacts": artifacts,
            "stop_run": {"run_id": stop_run, "bff_response": stop_resp[0],
                         "final_status": run_status(ctx, stop_run),
                         "db_count": len(db_timesteps(ctx, stop_run))},
        },
    }


def scenario_bff_restart(ctx: Ctx, down_sec: float) -> dict[str, Any]:
    fake_faults(ctx, {"steps_per_sec": 4096}, reset=True)  # n_steps 2048 → 초당 지표 2개
    n_steps, n_metrics = 2048, 60
    run_id = start_run(ctx, "bff-restart", "ppo", {"n_steps": n_steps}, n_steps * n_metrics)
    recorder = StatusRecorder(ctx, run_id)
    recorder.start()
    wait_until(lambda: ok_metric_count(ctx, run_id) >= 6, 30, what="지표 6개 전송")

    # BFF가 비정상 종료된 상황을 흉내 내기 위해 종료 대기 없이 바로 내린다. (--timeout 0: 즉시 SIGKILL)
    t_stop = time.monotonic()
    compose(ctx, "stop", "--timeout", "0", "api-server")
    stopped_after = round(time.monotonic() - t_stop, 1)
    time.sleep(down_sec)
    compose(ctx, "start", "api-server")
    wait_until(lambda: _bff_up(ctx), 60, 0.2, "BFF 재기동")
    bff_down_total = round(time.monotonic() - t_stop, 1)

    wait_finished(ctx, run_id, 120)
    wait_trainer_idle(ctx)
    time.sleep(2)
    status_changes = recorder.stop()

    sent = fake_sent(ctx, run_id)
    metric_items = [i for i in sent["items"] if i["kind"] == "metric"]
    failed = [i for i in metric_items if i["result"] != 201]
    expected = {n_steps * k for k in range(1, n_metrics + 1)}
    db = set(db_timesteps(ctx, run_id))
    missing = sorted(expected - db)
    failed_steps = sorted(i["timesteps"] for i in failed)

    checks = {
        # 대역이 모든 스텝의 지표를 한 번 이상 보냈는가 (재전송이 생겨도 성립)
        "every_expected_step_attempted": {i["timesteps"] for i in metric_items} == expected,
        "completion_sent": len(completion_items(ctx, run_id)) == 1,
    }
    return {
        "checks": checks,
        "observed": {
            "run_id": run_id,
            "down_sec_requested": down_sec,
            "stop_command_sec": stopped_after,
            "bff_unreachable_total_sec": bff_down_total,
            "metrics_sent": len(metric_items),
            "metrics_failed": len(failed),
            "failed_results": sorted({str(i["result"]) for i in failed}),
            "db_count": len(db),
            "missing_timesteps": missing,
            "db_duplicates": db_duplicates(ctx, run_id),
            # 재전송이 없으면 전송에 실패한 스텝이 그대로 빠진다. 재전송이 있으면 실패한 스텝도 결국 저장된다.
            "missing_matches_failed_sends": missing == failed_steps,
            "failed_steps_saved_later": len(set(failed_steps) & db),
            "failed_send_span_sec": _failed_send_span(failed),
            "completion_after_last_metric": completion_after_last_metric(sent["items"]),
            "status_changes": status_changes,
            # 학습은 계속됐는데 BFF가 재시작하며 실험을 FAILED로 바꿨는가 (이슈 #21)
            "failed_while_training": any(s == "FAILED" for _, s in status_changes),
            "final_status": run_status(ctx, run_id),
        },
    }


def scenario_bff_freeze(ctx: Ctx, freeze_sec: float) -> dict[str, Any]:
    """BFF가 죽지 않고 응답만 멈춘 상황(프로세스 정지). 대역은 timeout으로 실패를 기록하지만,
    BFF가 돌아온 뒤 밀린 요청을 처리하면 '응답 없음'이었던 지표가 실제로는 저장된다."""
    fake_faults(ctx, {"steps_per_sec": 2048}, reset=True)  # n_steps 2048 → 초당 지표 1개
    n_steps, n_metrics = 2048, 12
    t_start = time.monotonic()
    run_id = start_run(ctx, "bff-freeze", "ppo", {"n_steps": n_steps}, n_steps * n_metrics)
    wait_until(lambda: ok_metric_count(ctx, run_id) >= 3, 30, what="지표 3개 전송")
    compose(ctx, "pause", "api-server")
    time.sleep(freeze_sec)
    compose(ctx, "unpause", "api-server")
    wait_finished(ctx, run_id, 120)
    run_duration = round(time.monotonic() - t_start, 1)
    wait_trainer_idle(ctx)
    time.sleep(2)

    metric_items = [i for i in fake_sent(ctx, run_id)["items"] if i["kind"] == "metric"]
    timed_out = [i for i in metric_items if str(i["result"]).startswith("error:")]
    expected = {n_steps * k for k in range(1, n_metrics + 1)}
    db = set(db_timesteps(ctx, run_id))
    checks = {
        "every_expected_step_attempted": {i["timesteps"] for i in metric_items} == expected,
        "completion_attempted": len(completion_items(ctx, run_id)) == 1,
    }
    return {
        "checks": checks,
        "observed": {
            "run_id": run_id,
            "freeze_sec": freeze_sec,
            "metrics_sent": len(metric_items),
            "metrics_no_response": len(timed_out),
            "no_response_results": sorted({str(i["result"]) for i in timed_out}),
            # 대역은 실패로 기록했지만 DB에는 저장된 지표 (응답 없음 ≠ 처리 안 됨)
            "no_response_but_saved": sorted(i["timesteps"] for i in timed_out if i["timesteps"] in db),
            "missing_timesteps": sorted(expected - db),
            "db_duplicates": db_duplicates(ctx, run_id),
            "completion_result": [i["result"] for i in completion_items(ctx, run_id)],
            "final_status": run_status(ctx, run_id),
            # 전송이 학습 스레드에서 동기로 일어나면 timeout 동안 학습도 멈춰 nominal보다 길어진다.
            "run_duration_sec": run_duration,
            "nominal_duration_sec": n_metrics * n_steps / 2048,
        },
    }


def scenario_bff_down_at_end(ctx: Ctx, down_sec: float) -> dict[str, Any]:
    """학습이 끝날 때 BFF가 내려가 있는 상황 (이슈 #16).
    마지막 지표 몇 개와 완료 신호를 보내야 할 때 BFF가 없다. BFF가 돌아온 뒤 이것들이 도착하는지,
    완료 신호가 마지막 지표보다 뒤에 오는지 본다."""
    fake_faults(ctx, {"steps_per_sec": 4096}, reset=True)  # n_steps 2048 → 초당 지표 2개
    n_steps, n_metrics, before_stop = 2048, 20, 16
    run_id = start_run(ctx, "bff-down-at-end", "ppo", {"n_steps": n_steps}, n_steps * n_metrics)
    recorder = StatusRecorder(ctx, run_id)
    recorder.start()
    wait_until(lambda: ok_metric_count(ctx, run_id) >= before_stop, 30, what=f"지표 {before_stop}개 전송")

    t_stop = time.monotonic()
    compose(ctx, "stop", "--timeout", "0", "api-server")
    # 대역 학습 루프가 끝나(남은 지표와 완료 신호를 보낼 시점이 지나) 다시 down_sec 동안 BFF를 내려 둔다.
    wait_until(lambda: fake_state(ctx)["progress"].get("finished"), 30, 0.2, "대역 학습 종료")
    trainer_finished_after = round(time.monotonic() - t_stop, 1)
    time.sleep(down_sec)
    compose(ctx, "start", "api-server")
    wait_until(lambda: _bff_up(ctx), 60, 0.2, "BFF 재기동")
    t_up = time.monotonic()
    bff_down_total = round(t_up - t_stop, 1)

    # 완료 신호가 성공할 때까지 기다린다. 재전송이 없으면 오지 않으므로 제한 시간까지만 기다린다.
    def completion_ok() -> bool:
        return any(i["result"] == 200 for i in completion_items(ctx, run_id))
    try:
        wait_until(completion_ok, 60, 0.5, "완료 신호 200")
        completion_delivered_after_up = round(time.monotonic() - t_up, 1)
    except TimeoutError:
        completion_delivered_after_up = None
    time.sleep(1)
    status_changes = recorder.stop()

    sent = fake_sent(ctx, run_id)
    metric_items = [i for i in sent["items"] if i["kind"] == "metric"]
    failed = [i for i in metric_items if i["result"] not in SAVED]
    expected = {n_steps * k for k in range(1, n_metrics + 1)}
    db = set(db_timesteps(ctx, run_id))
    checks = {
        "every_expected_step_attempted": {i["timesteps"] for i in metric_items} == expected,
        # 측정 도구 확인: BFF를 내린 동안 실제로 전송 실패가 생겼는가 (장애가 의도한 시점에 들어갔는가)
        "sends_failed_while_bff_down": len(failed) > 0,
    }
    return {
        "checks": checks,
        "observed": {
            "run_id": run_id,
            "down_sec_after_trainer_finished": down_sec,
            "trainer_finished_after_stop_sec": trainer_finished_after,
            "bff_unreachable_total_sec": bff_down_total,
            "metrics_failed_attempts": len(failed),
            "db_count": len(db),
            "missing_timesteps": sorted(expected - db),
            "db_duplicates": db_duplicates(ctx, run_id),
            "completion_results": [i["result"] for i in completion_items(ctx, run_id)],
            "completion_delivered_after_bff_up_sec": completion_delivered_after_up,
            "completion_after_last_metric": completion_after_last_metric(sent["items"]),
            "status_changes": status_changes,
            "final_status": run_status(ctx, run_id),
        },
    }


def _bff_up(ctx: Ctx) -> bool:
    try:
        return http("GET", f"{ctx.bff}/api/algorithms", timeout=2)[0] == 200
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return False


def _failed_send_span(items: list[dict[str, Any]]) -> float | None:
    """첫 실패 전송부터 마지막 실패 전송까지의 시간. 각 시각은 요청이 끝난 뒤 기록되므로,
    실제로 BFF에 닿지 못한 구간은 이 값보다 최대 지표 간격 하나만큼 길 수 있다."""
    if not items:
        return None
    from datetime import datetime
    ts = [datetime.fromisoformat(i["at"]) for i in items]
    return round((max(ts) - min(ts)).total_seconds(), 1)


def scenario_duplicate(ctx: Ctx) -> dict[str, Any]:
    fake_faults(ctx, {"steps_per_sec": 4096, "duplicate_metrics": True}, reset=True)
    n_steps, n_metrics = 2048, 5
    run_id = start_run(ctx, "duplicate", "ppo", {"n_steps": n_steps}, n_steps * n_metrics)
    wait_finished(ctx, run_id, 60)
    wait_trainer_idle(ctx)
    fake_faults(ctx, reset=True)
    time.sleep(1)
    items = [i for i in fake_sent(ctx, run_id)["items"] if i["kind"] == "metric"]
    per_step: dict[int, list[Any]] = {}
    for i in items:
        per_step.setdefault(i["timesteps"], []).append(i["result"])
    db = db_timesteps(ctx, run_id)
    checks = {
        "each_step_sent_twice": all(len(v) == 2 for v in per_step.values()) and len(per_step) == n_metrics,
        # 불변 조건: 같은 지표가 여러 번 와도 DB에는 한 번만 저장된다. (재전송 보완의 전제)
        "db_no_duplicates": sorted(db) == sorted(set(db)) and len(db) == n_metrics,
    }
    return {"checks": checks, "observed": {
        "run_id": run_id, "results_per_step": per_step, "db_count": len(db),
        "second_send_409": all(v == [201, 409] for v in per_step.values()),
    }}


def scenario_faults(ctx: Ctx) -> dict[str, Any]:
    n_steps = 2048
    out: dict[str, Any] = {"checks": {}, "observed": {}}

    # 1) 학습 루프 멈춤: 지표가 끊겨도 BFF가 알아채는지 (현재 기준 동작 관찰)
    fake_faults(ctx, {"steps_per_sec": 4096}, reset=True)
    run_id = start_run(ctx, "fault-stall", "ppo", {"n_steps": n_steps}, n_steps * 100)
    wait_until(lambda: ok_metric_count(ctx, run_id) >= 3, 30, what="지표 3개")
    fake_faults(ctx, {"stall": True})
    time.sleep(0.5)
    count_at_stall = ok_metric_count(ctx, run_id)
    time.sleep(8)
    count_after = ok_metric_count(ctx, run_id)
    status_during_stall = run_status(ctx, run_id)
    stop_resp = http("POST", f"{ctx.bff}/api/runs/{run_id}/stop")
    time.sleep(2)
    completion_during_stall = len(completion_items(ctx, run_id))
    fake_faults(ctx, {"stall": False})
    wait_finished(ctx, run_id, 30)
    wait_trainer_idle(ctx)
    out["checks"]["stall_stops_metrics"] = count_after == count_at_stall
    out["checks"]["stall_blocks_completion_until_released"] = (
        completion_during_stall == 0 and len(completion_items(ctx, run_id)) == 1)
    out["observed"]["stall"] = {
        "run_id": run_id, "stall_sec": 8, "status_during_stall": status_during_stall,
        "bff_stop_response": stop_resp[0], "status_after_stop": run_status(ctx, run_id),
    }

    # 2) 학습 스레드 비정상 종료: 완료 신호가 오지 않을 때 BFF 상태, 이후 제어 명령의 처리
    fake_faults(ctx, {"steps_per_sec": 4096}, reset=True)
    run_id = start_run(ctx, "fault-crash", "ppo", {"n_steps": n_steps}, n_steps * 100)
    wait_until(lambda: ok_metric_count(ctx, run_id) >= 3, 30, what="지표 3개")
    must(http("POST", f"{ctx.fake}/_fake/crash"))
    wait_trainer_idle(ctx, 10)
    time.sleep(5)
    status_after_crash = run_status(ctx, run_id)
    pause_resp = http("POST", f"{ctx.bff}/api/runs/{run_id}/pause")
    time.sleep(0.5)
    out["checks"]["crash_sends_no_completion"] = len(completion_items(ctx, run_id)) == 0
    out["observed"]["crash"] = {
        "run_id": run_id, "status_5s_after_crash": status_after_crash,
        "bff_pause_response": pause_resp, "status_after_pause": run_status(ctx, run_id),
    }

    # 3) 제어 명령 응답 지연·실패
    fake_faults(ctx, {"steps_per_sec": 1024}, reset=True)
    run_id = start_run(ctx, "fault-control", "ppo", {"n_steps": n_steps}, n_steps * 100)
    wait_until(lambda: ok_metric_count(ctx, run_id) >= 1, 30, what="지표 1개")
    fake_faults(ctx, {"control": {"pause": {"delay_sec": 3}}})
    # 대역 자체 확인: 대역에 직접 보내 지연이 적용되는지 본다. (BFF 상태는 건드리지 않음)
    t0 = time.monotonic()
    direct_pause = http("POST", f"{ctx.fake}/runs/{run_id}:pause")
    direct_latency = round(time.monotonic() - t0, 2)
    must(http("POST", f"{ctx.fake}/runs/{run_id}:resume"))
    # BFF를 거치면 어떻게 되는지는 관찰값으로 남긴다.
    t0 = time.monotonic()
    pause_resp = http("POST", f"{ctx.bff}/api/runs/{run_id}/pause")
    pause_latency = round(time.monotonic() - t0, 2)
    fake_faults(ctx, {"control": {"pause": {"delay_sec": 0}, "resume": {"fail": True}}})
    resume_resp = http("POST", f"{ctx.bff}/api/runs/{run_id}/resume")
    time.sleep(0.5)
    status_after_failed_resume = run_status(ctx, run_id)
    trainer_paused = fake_state(ctx)["train"]["is_paused"]
    fake_faults(ctx, {"control": {"resume": {"fail": False}}})
    # 정리: BFF는 이미 FAILED로 바꿔 중지 요청을 대역에 전달하지 않으므로 대역에 직접 보낸다.
    must(http("POST", f"{ctx.fake}/runs/{run_id}:stop"))
    wait_trainer_idle(ctx, 10)
    fake_faults(ctx, reset=True)
    out["checks"]["pause_delay_applied_on_fake"] = direct_pause[0] == 200 and direct_latency >= 3
    out["checks"]["resume_fail_keeps_trainer_paused"] = trainer_paused is True
    out["observed"]["control"] = {
        "run_id": run_id, "bff_pause_latency_sec": pause_latency, "bff_pause_response": pause_resp,
        "bff_resume_response_when_trainer_fails": resume_resp,
        "status_after_failed_resume": status_after_failed_resume,
        "trainer_still_paused": trainer_paused,
    }
    return out


# ---------------------------------------------------------------- 응답 없음 판정 (이슈 #21)

# 실험이 FAILED로 바뀌기를 기다리는 시간. 보완 기준(생존 신호 60초 없음 + 확인 주기 10초)에 여유를 더했다.
STALE_LIMIT_SEC = 90
# 일시정지를 유지하는 시간. 판정 기준(60초)보다 길어야 '살아 있으면 PAUSED 유지'를 확인할 수 있다.
PAUSE_HOLD_SEC = 75


def run_error(ctx: Ctx, run_id: str) -> Any:
    """DB에 실제로 저장된 실패 이유. (스키마에 없는 필드는 저장되지 않으므로 DB를 직접 본다)"""
    doc = mongo_eval(ctx, f'db.runs.findOne({{_id: ObjectId("{run_id}")}}, {{_id: 0, error: 1}})')
    return (doc or {}).get("error")


def _fake_up(ctx: Ctx) -> bool:
    try:
        return http("GET", f"{ctx.fake}/_fake/state", timeout=2)[0] == 200
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return False


def _seconds_until(cond: Callable[[], Any], t0: float, limit: float, interval: float = 0.5) -> float | None:
    """t0(monotonic)부터 cond가 참이 될 때까지 걸린 시간. limit초 안에 참이 되지 않으면 None."""
    while time.monotonic() < t0 + limit:
        if cond():
            return round(time.monotonic() - t0, 1)
        time.sleep(interval)
    return None


def _trainer_container(ctx: Ctx) -> dict[str, Any]:
    cid = compose(ctx, "ps", "-a", "-q", "mlagents-gym").stdout.strip()
    out = subprocess.run(["docker", "inspect", "-f",
                          "{{.State.Status}} {{.RestartCount}} {{.HostConfig.RestartPolicy.Name}}", cid],
                         check=True, capture_output=True, text=True).stdout.split()
    return {"state": out[0], "restart_count": int(out[1]), "restart_policy": out[2] if len(out) > 2 else ""}


def scenario_trainer_crash(ctx: Ctx) -> dict[str, Any]:
    """학습 스레드가 완료 신호 없이 끝났을 때(학습 중 예외) 실험이 FAILED로 바뀌기까지 걸린 시간."""
    fake_faults(ctx, {"steps_per_sec": 4096}, reset=True)
    n_steps = 2048
    run_id = start_run(ctx, "trainer-crash", "ppo", {"n_steps": n_steps}, n_steps * 1000)
    wait_until(lambda: ok_metric_count(ctx, run_id) >= 3, 30, what="지표 3개")
    recorder = StatusRecorder(ctx, run_id)
    recorder.start()
    t_fault = time.monotonic()
    must(http("POST", f"{ctx.fake}/_fake/crash"))
    wait_trainer_idle(ctx, 10)
    failed_after = _seconds_until(lambda: run_status(ctx, run_id) == "FAILED", t_fault, STALE_LIMIT_SEC)
    status_changes = recorder.stop()
    checks = {
        "crash_sends_no_completion": len(completion_items(ctx, run_id)) == 0,
        "trainer_idle_after_crash": fake_state(ctx)["current_run_id"] is None,
    }
    return {"checks": checks, "observed": {
        "run_id": run_id,
        "limit_sec": STALE_LIMIT_SEC,
        # None이면 limit_sec 동안 FAILED로 바뀌지 않았다는 뜻이다.
        "failed_after_crash_sec": failed_after,
        "status_changes": status_changes,
        "final_status": run_status(ctx, run_id),
        "error": run_error(ctx, run_id),
    }}


def scenario_trainer_paused(ctx: Ctx) -> dict[str, Any]:
    """일시정지 중인 실험: 학습 서버가 살아 있으면 오래 멈춰 있어도 PAUSED로 남는지,
    그 상태에서 학습 스레드가 끝나면 FAILED로 바뀌기까지 걸린 시간."""
    fake_faults(ctx, {"steps_per_sec": 4096}, reset=True)
    n_steps = 2048
    run_id = start_run(ctx, "trainer-paused", "ppo", {"n_steps": n_steps}, n_steps * 1000)
    wait_until(lambda: ok_metric_count(ctx, run_id) >= 3, 30, what="지표 3개")
    must(http("POST", f"{ctx.bff}/api/runs/{run_id}/pause"))
    trainer_paused = wait_until(lambda: fake_state(ctx)["train"]["is_paused"], 10, what="대역 일시정지")
    recorder = StatusRecorder(ctx, run_id)
    recorder.start()
    time.sleep(PAUSE_HOLD_SEC)
    status_after_hold = run_status(ctx, run_id)

    # 일시정지 대기 중에도 비정상 종료 요청은 바로 적용된다. (fake/training.py)
    t_fault = time.monotonic()
    must(http("POST", f"{ctx.fake}/_fake/crash"))
    wait_trainer_idle(ctx, 10)
    failed_after = _seconds_until(lambda: run_status(ctx, run_id) == "FAILED", t_fault, STALE_LIMIT_SEC)
    status_changes = recorder.stop()
    checks = {
        "trainer_paused": bool(trainer_paused),
        "crash_sends_no_completion": len(completion_items(ctx, run_id)) == 0,
    }
    return {"checks": checks, "observed": {
        "run_id": run_id,
        "pause_hold_sec": PAUSE_HOLD_SEC,
        "status_after_hold": status_after_hold,
        "limit_sec": STALE_LIMIT_SEC,
        "failed_after_crash_sec": failed_after,
        "status_changes": status_changes,
        "final_status": run_status(ctx, run_id),
        "error": run_error(ctx, run_id),
    }}


def scenario_trainer_exit(ctx: Ctx) -> dict[str, Any]:
    """학습 서버 프로세스가 학습 중에 비정상 종료됐을 때: 컨테이너가 다시 뜨는지,
    실험이 FAILED로 바뀌기까지 걸린 시간."""
    fake_faults(ctx, {"steps_per_sec": 4096}, reset=True)
    n_steps = 2048
    run_id = start_run(ctx, "trainer-exit", "ppo", {"n_steps": n_steps}, n_steps * 1000)
    wait_until(lambda: ok_metric_count(ctx, run_id) >= 3, 30, what="지표 3개")
    before = _trainer_container(ctx)
    recorder = StatusRecorder(ctx, run_id)
    recorder.start()
    t_fault = time.monotonic()
    must(http("POST", f"{ctx.fake}/_fake/exit"))
    went_down = _seconds_until(lambda: not _fake_up(ctx), t_fault, 10, 0.1)

    failed_after = back_after = None
    try:
        while time.monotonic() < t_fault + STALE_LIMIT_SEC and (failed_after is None or back_after is None):
            elapsed = round(time.monotonic() - t_fault, 1)
            if failed_after is None and run_status(ctx, run_id) == "FAILED":
                failed_after = elapsed
            if back_after is None and _fake_up(ctx):
                back_after = elapsed
            time.sleep(0.5)
        status_changes = recorder.stop()
        after = _trainer_container(ctx)
    finally:
        # 다음 시나리오를 위해 대역을 다시 띄운다. (restart 정책이 없으면 내려간 채 남는다)
        if not _fake_up(ctx):
            compose(ctx, "start", "mlagents-gym")
            wait_until(lambda: _fake_up(ctx), 60, 0.5, "대역 재기동")
    checks = {
        # 측정 도구 확인: 대역 프로세스가 실제로 내려갔는가
        "trainer_went_down": went_down is not None,
    }
    return {"checks": checks, "observed": {
        "run_id": run_id,
        "limit_sec": STALE_LIMIT_SEC,
        "restart_policy": before["restart_policy"] or "없음",
        # None이면 limit_sec 안에 다시 뜨지 않았다는 뜻이다.
        "trainer_back_after_sec": back_after,
        "restart_count_delta": after["restart_count"] - before["restart_count"],
        "container_state_at_end": after["state"],
        "failed_after_exit_sec": failed_after,
        "status_changes": status_changes,
        "final_status": run_status(ctx, run_id),
        "error": run_error(ctx, run_id),
    }}


# ---------------------------------------------------------------- 실행

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("scenario", choices=["normal", "bff-restart", "bff-freeze", "bff-down-at-end",
                                        "duplicate", "faults", "trainer-crash", "trainer-paused",
                                        "trainer-exit", "all"])
    p.add_argument("--bff", default="http://127.0.0.1:18080")
    p.add_argument("--fake", default="http://127.0.0.1:18000")
    p.add_argument("--project", default="rl-fake", help="docker compose 프로젝트 이름")
    p.add_argument("--down-sec", type=float, default=5.0, help="bff-restart에서 BFF를 내려 둘 시간")
    p.add_argument("--freeze-sec", type=float, default=5.0, help="bff-freeze에서 BFF를 멈춰 둘 시간")
    p.add_argument("--end-down-sec", type=float, default=5.0,
                   help="bff-down-at-end에서 학습이 끝난 뒤 BFF를 더 내려 둘 시간")
    p.add_argument("--out", help="결과 JSON을 저장할 경로")
    args = p.parse_args()
    ctx = Ctx(args)

    runners = {
        "normal": lambda: scenario_normal(ctx),
        "bff-restart": lambda: scenario_bff_restart(ctx, args.down_sec),
        "bff-freeze": lambda: scenario_bff_freeze(ctx, args.freeze_sec),
        "bff-down-at-end": lambda: scenario_bff_down_at_end(ctx, args.end_down_sec),
        "duplicate": lambda: scenario_duplicate(ctx),
        "faults": lambda: scenario_faults(ctx),
        "trainer-crash": lambda: scenario_trainer_crash(ctx),
        "trainer-paused": lambda: scenario_trainer_paused(ctx),
        "trainer-exit": lambda: scenario_trainer_exit(ctx),
    }
    names = list(runners) if args.scenario == "all" else [args.scenario]
    results: dict[str, Any] = {}
    failed = False
    for name in names:
        print(f"\n=== {name} ===", flush=True)
        t0 = time.monotonic()
        try:
            res = runners[name]()
        except Exception as e:
            res = {"error": f"{type(e).__name__}: {e}", "checks": {"completed": False}}
        finally:
            cleanup(ctx)
        res["elapsed_sec"] = round(time.monotonic() - t0, 1)
        results[name] = res
        for check, passed in res["checks"].items():
            print(f"  [{'PASS' if passed else 'FAIL'}] {check}")
            failed |= not passed
        if "error" in res:
            print(f"  error: {res['error']}")
        print("  observed:", json.dumps(res.get("observed", {}), ensure_ascii=False, default=str))

    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    print(f"\n결과: {'실패한 판정 있음' if failed else '모든 판정 통과'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
