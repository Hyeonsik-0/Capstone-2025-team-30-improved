#!/usr/bin/env python3
"""학습 서버 대역으로 BFF 동작을 재는 시나리오 (이슈 #7).

표준 라이브러리만 쓴다. 저장소 루트에서 대역 구성을 띄운 뒤 실행한다.

    docker compose -p rl-fake -f docker-compose.yml -f tools/fake-trainer/compose.fake.yml \\
      up -d --build mongodb api-server mlagents-gym
    python3 tools/fake-trainer/scenarios/run_scenarios.py all --out /tmp/fake-results.json

시나리오
  normal       학습 시작 → 지표 → 일시정지·재개 → 완료, 중지, 시뮬레이션 속도
  bff-restart  학습 중 BFF를 N초 내렸다 올렸을 때 유실되는 지표 수 (기준값)
  duplicate    같은 지표를 두 번 보냈을 때 BFF 응답과 DB 중복 여부
  faults       학습 루프 멈춤, 학습 스레드 비정상 종료, 제어 명령 지연·실패에 대한 BFF 반응

각 시나리오는 판정(check)과 관찰값(observed)을 함께 출력한다.
판정은 "대역과 측정 도구가 의도대로 동작하는가"만 본다. BFF의 현재 약점은 관찰값으로만 기록한다.
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


# ---------------------------------------------------------------- 시나리오

def scenario_normal(ctx: Ctx) -> dict[str, Any]:
    fake_faults(ctx, {"steps_per_sec": 2048}, reset=True)
    n_steps, n_metrics = 2048, 10
    run_id = start_run(ctx, "normal-ppo", "ppo", {"n_steps": n_steps}, n_steps * n_metrics)
    sse = SSEReader(f"{ctx.bff}/api/training-metrics/stream/{run_id}")
    sse.start()
    sse.connected.wait(5)

    wait_until(lambda: len(db_timesteps(ctx, run_id)) >= 3, 30, what="지표 3개 저장")
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
    side_channel = [c["cmd"] for c in fake_state(ctx)["side_channel"]]

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

    # 비정상 종료처럼 바로 내린다. (--timeout 0: 종료 대기 없이 SIGKILL)
    # node가 PID 1이라 SIGTERM을 무시하므로, 기본 stop은 10초 동안 계속 요청을 받는다.
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
        "every_metric_attempted_once": sorted(i["timesteps"] for i in metric_items) == sorted(expected),
        "missing_equals_failed_sends": missing == failed_steps,
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
            "missing_window_sec": _window(failed),
            "status_changes": status_changes,
            "final_status": run_status(ctx, run_id),
        },
    }


def _bff_up(ctx: Ctx) -> bool:
    try:
        return http("GET", f"{ctx.bff}/api/algorithms", timeout=2)[0] == 200
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return False


def _window(items: list[dict[str, Any]]) -> float | None:
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
        "second_send_409": all(v == [201, 409] for v in per_step.values()),
        "db_no_duplicates": sorted(db) == sorted(set(db)) and len(db) == n_metrics,
    }
    return {"checks": checks, "observed": {"run_id": run_id, "results_per_step": per_step, "db_count": len(db)}}


def scenario_faults(ctx: Ctx) -> dict[str, Any]:
    n_steps = 2048
    out: dict[str, Any] = {"checks": {}, "observed": {}}

    # 1) 학습 루프 멈춤: 지표가 끊겨도 BFF가 알아채는지 (현재 기준 동작 관찰)
    fake_faults(ctx, {"steps_per_sec": 4096}, reset=True)
    run_id = start_run(ctx, "fault-stall", "ppo", {"n_steps": n_steps}, n_steps * 100)
    wait_until(lambda: ok_metric_count(ctx, run_id) >= 3, 30, what="지표 3개")
    fake_faults(ctx, {"stall": True})
    time.sleep(0.5)
    count_at_stall = len(db_timesteps(ctx, run_id))
    time.sleep(8)
    count_after = len(db_timesteps(ctx, run_id))
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
    out["checks"]["pause_delay_applied"] = pause_latency >= 3
    out["checks"]["resume_fail_keeps_trainer_paused"] = trainer_paused is True
    out["observed"]["control"] = {
        "run_id": run_id, "bff_pause_latency_sec": pause_latency, "bff_pause_response": pause_resp,
        "bff_resume_response_when_trainer_fails": resume_resp,
        "status_after_failed_resume": status_after_failed_resume,
        "trainer_still_paused": trainer_paused,
    }
    return out


# ---------------------------------------------------------------- 실행

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("scenario", choices=["normal", "bff-restart", "duplicate", "faults", "all"])
    p.add_argument("--bff", default="http://127.0.0.1:18080")
    p.add_argument("--fake", default="http://127.0.0.1:18000")
    p.add_argument("--project", default="rl-fake", help="docker compose 프로젝트 이름")
    p.add_argument("--down-sec", type=float, default=5.0, help="bff-restart에서 BFF를 내려 둘 시간")
    p.add_argument("--out", help="결과 JSON을 저장할 경로")
    args = p.parse_args()
    ctx = Ctx(args)

    runners = {
        "normal": lambda: scenario_normal(ctx),
        "bff-restart": lambda: scenario_bff_restart(ctx, args.down_sec),
        "duplicate": lambda: scenario_duplicate(ctx),
        "faults": lambda: scenario_faults(ctx),
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
