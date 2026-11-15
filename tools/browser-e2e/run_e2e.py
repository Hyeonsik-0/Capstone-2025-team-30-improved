#!/usr/bin/env python3
"""브라우저에서 실시간 차트 내용을 읽어, 브라우저–nginx 사이 장애가 차트에 남기는 영향을 잰다 (이슈 #9).

compose.e2e.yml의 browser-e2e 컨테이너 안에서 실행한다. 사용법은 같은 폴더의 README.md를 본다.

시나리오
  s0-normal      장애 없음. 학습 1회 동안 차트의 스텝 목록이 DB와 같은지 확인 (측정 도구 자체 확인)
  s1-short-drop  브라우저–nginx 연결을 --drop-sec(기본 10초) 동안 끊었다가 복구
  s2-long-drop   끊긴 동안 쌓이는 지표가 BFF 재전송 버퍼(50개)를 넘도록 --long-drop-sec(기본 20초) 동안 끊었다가 복구
  s3-half-open   연결은 그대로 두고 서버→브라우저 데이터만 --stall-sec(기본 90초) 동안 멈췄다가 복구

모든 시나리오에서 BFF·DB·학습 서버 대역은 정상이다. 장애는 브라우저와 nginx 사이에만 넣는다.

측정 방법
- 차트 데이터: 측정용 빌드가 노출한 window.__E2E__.experimentStore의 trainingMetrics (차트가 그리는 배열)
- DB: 기존 측정 시나리오(run_scenarios.py)와 같은 BFF 지표 조회 API. DB에서 runId로 찾아 스텝 순으로 정렬해 그대로 돌려준다.
- SSE 연결 기록: 페이지 코드가 실행되기 전에 EventSource를 감싸서, 연결별 생성·open·error·message·close 시각을 남긴다.

판정(check)은 두 가지만 본다. 측정 도구와 장애 주입이 의도대로 동작했는지, 그리고 장애가 없을 때의 불변 조건이다.
장애 상황에서 차트가 보이는 현재 결과(빠진 스텝 수, 재연결 시간)는 보완으로 바뀔 대상이다.
그래서 판정하지 않고 관찰값(observed)으로 기록한다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# 학습 서버 대역 측정 시나리오의 BFF·대역 조작 함수를 그대로 쓴다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fake-trainer" / "scenarios"))
import run_scenarios as rs  # noqa: E402

from playwright.sync_api import Browser, Page, sync_playwright  # noqa: E402
from playwright.sync_api import TimeoutError as PlaywrightTimeout  # noqa: E402

N_STEPS = 2048  # PPO n_steps. 대역은 n_steps마다 지표를 1개 보낸다.
PROXY = "web"
HALF_OPEN_TOXIC = "half_open"
BEFORE_FAULT_METRICS = 8  # 장애를 넣기 전에 정상으로 받을 지표 수
AFTER_FAULT_SEC = 10  # 복구 뒤 학습이 더 진행되는 시간

# 페이지 코드보다 먼저 실행되어 EventSource를 감싼다. 동작은 바꾸지 않고 기록만 한다.
SSE_HOOK = r"""
(() => {
  const Native = window.EventSource;
  if (!Native || window.__E2E_SSE__) return;
  const log = [];
  window.__E2E_SSE__ = log;
  let seq = 0;
  class RecordedEventSource extends Native {
    constructor(url, init) {
      super(url, init);
      this.__e2eId = ++seq;
      this.__e2eRec('new', { url: String(url) });
      this.addEventListener('open', () => this.__e2eRec('open'));
      this.addEventListener('error', () => this.__e2eRec('error'));
      this.addEventListener('message', (e) => {
        let type = null, timesteps = null;
        try { const d = JSON.parse(e.data); type = d.type ?? null; timesteps = d.timesteps ?? null; } catch (_) {}
        this.__e2eRec('message', { lastEventId: e.lastEventId, type, timesteps });
      });
    }
    __e2eRec(kind, extra) {
      log.push(Object.assign({ t: Date.now(), es: this.__e2eId, kind, readyState: this.readyState }, extra || {}));
    }
    close() {
      this.__e2eRec('close');
      super.close();
    }
  }
  window.EventSource = RecordedEventSource;
})();
"""

READ_STORE = """() => {
  const s = window.__E2E__.experimentStore.getState();
  return { runId: s.runId, status: s.trainingStatus, timesteps: s.trainingMetrics.map(m => m.timesteps) };
}"""


def now_ms() -> int:
    return int(time.time() * 1000)


class Cfg:
    def __init__(self, args: argparse.Namespace) -> None:
        self.ctx = rs.Ctx(argparse.Namespace(bff=args.bff, fake=args.fake, project="rl-e2e"))
        self.toxiproxy = args.toxiproxy.rstrip("/")
        self.web = args.web.rstrip("/")
        self.upstream = args.upstream
        self.out_dir = Path(args.out).parent if args.out else None


# ---------------------------------------------------------------- Toxiproxy (브라우저–nginx 사이)

def toxi(cfg: Cfg, method: str, path: str, body: Any = None) -> Any:
    return rs.must(rs.http(method, f"{cfg.toxiproxy}{path}", body, timeout=10), ok=(200, 201, 204))


def proxy_ready(cfg: Cfg) -> None:
    """프록시가 없으면 만들고, 있으면 초기 상태(켜짐, toxic 없음)로 되돌린다."""
    status, _ = rs.http("GET", f"{cfg.toxiproxy}/proxies/{PROXY}", timeout=10)
    if status == 404:
        toxi(cfg, "POST", "/proxies", {"name": PROXY, "listen": "0.0.0.0:8080",
                                       "upstream": cfg.upstream, "enabled": True})
    toxi(cfg, "POST", "/reset")


def inject(cfg: Cfg, kind: str) -> None:
    if kind == "drop":
        # 프록시를 끄면 열린 연결을 모두 닫고, 새 연결은 거부한다.
        toxi(cfg, "POST", f"/proxies/{PROXY}", {"enabled": False})
    else:
        # timeout toxic(timeout 0): 연결은 닫지 않고 서버→브라우저 데이터만 버린다. (반쯤 죽은 연결)
        toxi(cfg, "POST", f"/proxies/{PROXY}/toxics", {
            "name": HALF_OPEN_TOXIC, "type": "timeout", "stream": "downstream",
            "toxicity": 1.0, "attributes": {"timeout": 0}})


def recover(cfg: Cfg, kind: str) -> None:
    if kind == "drop":
        toxi(cfg, "POST", f"/proxies/{PROXY}", {"enabled": True})
    else:
        toxi(cfg, "DELETE", f"/proxies/{PROXY}/toxics/{HALF_OPEN_TOXIC}")


# ---------------------------------------------------------------- 브라우저

def open_dashboard(cfg: Cfg, browser: Browser, run_id: str, console: list[str]) -> tuple[Any, Page]:
    """대시보드를 ?runId=로 열고, 실험 복원과 SSE 연결이 끝날 때까지 기다린다."""
    context = browser.new_context()
    context.add_init_script(SSE_HOOK)
    page = context.new_page()
    page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
    page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))

    # 페이지 진입 시 지표 복원 요청(GET /api/runs/:id/training-metrics)의 응답까지 기다린다.
    restore_path = f"/api/runs/{run_id}/training-metrics"
    with page.expect_response(lambda r: r.request.method == "GET" and r.url.endswith(restore_path),
                              timeout=30_000):
        page.goto(f"{cfg.web}/?runId={run_id}")
    try:
        page.wait_for_function("() => !!window.__E2E__", timeout=10_000)
    except PlaywrightTimeout:
        raise RuntimeError("window.__E2E__가 없다. nginx를 VITE_E2E_EXPOSE_STORE=true로 빌드했는지 확인") from None
    page.wait_for_function("""(runId) => {
      const s = window.__E2E__.experimentStore.getState();
      return s.runId === runId && s.trainingStatus === 'running'
        && (window.__E2E_SSE__ || []).some(e => e.kind === 'message' && e.type === 'connected');
    }""", arg=run_id, timeout=30_000)
    page.wait_for_timeout(1500)  # 복원 직후의 상태 변화가 가라앉을 시간
    return context, page


def wait_page_final(page: Page, timeout_ms: int) -> int | None:
    """화면이 학습 종료를 알아챌 때까지(3초 상태 폴링) 기다린다. 걸린 ms, 못 알아채면 None."""
    t0 = now_ms()
    try:
        page.wait_for_function(
            "() => ['completed', 'stopped', 'failed'].includes(window.__E2E__.experimentStore.getState().trainingStatus)",
            timeout=timeout_ms, polling=200)
    except PlaywrightTimeout:
        return None
    page.wait_for_timeout(500)
    return now_ms() - t0


# ---------------------------------------------------------------- 분석

def at_ms(item: dict[str, Any]) -> int:
    return int(datetime.fromisoformat(item["at"]).timestamp() * 1000)


def sse_summary(log: list[dict[str, Any]], window: tuple[int, int] | None) -> dict[str, Any]:
    kinds = lambda k: [e for e in log if e["kind"] == k]  # noqa: E731
    metric_msgs = [e for e in log if e["kind"] == "message" and e.get("timesteps") is not None]
    out: dict[str, Any] = {
        "connections_created": len(kinds("new")),
        "opens": len(kinds("open")),
        "errors": len(kinds("error")),
        "closed_by_app": len(kinds("close")),
        "metric_messages": len(metric_msgs),
        "duplicate_metric_messages": len(metric_msgs) - len({e["timesteps"] for e in metric_msgs}),
    }
    if window:
        t0, t1 = window
        first = lambda evs, since: next((e["t"] - since for e in evs if e["t"] >= since), None)  # noqa: E731
        reactions = [e for e in log if e["kind"] in ("new", "close", "error") and t0 <= e["t"] <= t1]
        out.update({
            "first_error_after_fault_ms": first(kinds("error"), t0),
            "first_open_after_recover_ms": first(kinds("open"), t1),
            "first_metric_after_recover_ms": first(metric_msgs, t1),
            "metric_messages_during_fault": sum(1 for e in metric_msgs if t0 + 1000 < e["t"] < t1),
            # 장애 중 브라우저가 연결 이상을 알아챘는가 (반쯤 죽은 연결에서 의미가 있다)
            "reacted_during_fault": bool(reactions),
            "first_reaction_ms": reactions[0]["t"] - t0 if reactions else None,
            # 장애 시작 ~ 복구 60초 뒤까지의 연결 이벤트: [장애 시작 기준 초, 연결 번호, 종류, readyState]
            "timeline": [[round((e["t"] - t0) / 1000, 1), e["es"], e["kind"], e["readyState"]]
                         for e in log if e["kind"] != "message" and t0 - 1000 <= e["t"] <= t1 + 60_000],
        })
    return out


# ---------------------------------------------------------------- 시나리오

def run_case(cfg: Cfg, browser: Browser, name: str, *, metrics_per_sec: int, n_metrics: int,
             fault: tuple[str, float] | None) -> dict[str, Any]:
    ctx = cfg.ctx
    # 학습 루프를 멈춘 채 시작해 두고, 화면의 복원·SSE 연결이 끝난 뒤 풀어 준다.
    # (페이지 진입 복원과 SSE 수신이 겹치는 경합은 #10의 대상이라 이 측정에서는 피한다)
    rs.fake_faults(ctx, {"steps_per_sec": N_STEPS * metrics_per_sec, "stall": True}, reset=True)
    proxy_ready(cfg)
    run_id = rs.start_run(ctx, f"e2e-{name}", "ppo", {"n_steps": N_STEPS}, N_STEPS * n_metrics)
    rs.wait_until(lambda: rs.run_status(ctx, run_id) == "RUNNING", 30, what="실험 RUNNING")

    console: list[str] = []
    context, page = open_dashboard(cfg, browser, run_id, console)
    try:
        sse_before = page.evaluate("() => window.__E2E_SSE__.filter(e => e.kind === 'new').length")
        rs.fake_faults(ctx, {"stall": False})

        window = None
        if fault:
            kind, sec = fault
            rs.wait_until(lambda: rs.ok_metric_count(ctx, run_id) >= BEFORE_FAULT_METRICS, 60,
                          what=f"장애 전 지표 {BEFORE_FAULT_METRICS}개")
            t0 = now_ms()
            inject(cfg, kind)
            page.wait_for_timeout(sec * 1000)
            recover(cfg, kind)
            window = (t0, now_ms())

        rs.wait_finished(ctx, run_id, n_metrics / metrics_per_sec + 90)
        rs.wait_trainer_idle(ctx)
        page_final_ms = wait_page_final(page, 120_000)
        store = page.evaluate(READ_STORE)
        sse_log = page.evaluate("() => window.__E2E_SSE__")
    finally:
        context.close()
        if cfg.out_dir:
            (cfg.out_dir / f"{name}.console.log").write_text("\n".join(console) + "\n")
    if cfg.out_dir:
        (cfg.out_dir / f"{name}.sse.json").write_text(json.dumps(sse_log, ensure_ascii=False))

    expected = [N_STEPS * k for k in range(1, n_metrics + 1)]
    db = rs.db_timesteps(ctx, run_id)
    metric_items = [i for i in rs.fake_sent(ctx, run_id)["items"] if i["kind"] == "metric"]
    chart = store["timesteps"]
    chart_set, db_set = set(chart), set(db)
    missing = sorted(db_set - chart_set)

    checks: dict[str, bool] = {
        "sse_connected_before_metrics": sse_before >= 1,
        # 장애는 브라우저 쪽에만 넣었으므로, 대역의 지표 전송은 모두 성공하고 DB에 모든 스텝이 있어야 한다.
        "trainer_sends_all_ok": len(metric_items) == n_metrics and all(i["result"] == 201 for i in metric_items),
        "db_has_every_step": db == expected,
    }
    observed: dict[str, Any] = {
        "run_id": run_id,
        "metrics_per_sec": metrics_per_sec,
        "db_count": len(db),
        "chart_count": len(chart),
        "chart_missing": len(missing),
        "chart_duplicates": len(chart) - len(chart_set),
        "chart_extra": sorted(chart_set - db_set),
        "chart_sorted": chart == sorted(chart),
        "missing_timesteps": missing,
        "page_final_status": store["status"],
        "page_noticed_finish_ms": page_final_ms,
    }
    summary = sse_summary(sse_log, window)

    if fault is None:
        checks["chart_equals_db"] = chart == db
    else:
        kind, sec = fault
        t0, t1 = window
        sent_during = [i for i in metric_items if t0 <= at_ms(i) <= t1]
        sent_at = {i["timesteps"]: at_ms(i) for i in metric_items}
        observed.update({
            "fault": kind,
            "fault_sec_requested": sec,
            "fault_sec_actual": round((t1 - t0) / 1000, 1),
            "metrics_sent_during_fault": len(sent_during),
            "missing_sent_during_fault": sum(1 for s in missing if t0 <= sent_at.get(s, 0) <= t1),
        })
        if kind == "drop":
            # 프록시를 끈 순간 브라우저의 SSE 연결이 실제로 끊겼는가
            checks["fault_broke_sse_connection"] = summary["first_error_after_fault_ms"] is not None \
                and summary["first_error_after_fault_ms"] <= sec * 1000
        else:
            # 데이터가 실제로 멈췄는가: 장애 중에 대역은 지표를 보냈지만 브라우저는 하나도 받지 못해야 한다.
            checks["fault_stopped_sse_data"] = len(sent_during) > 0 and summary["metric_messages_during_fault"] == 0
    observed["sse"] = summary
    return {"checks": checks, "observed": observed}


def cleanup(cfg: Cfg) -> None:
    try:
        proxy_ready(cfg)
    except Exception as e:
        print(f"  정리 중 경고(toxiproxy): {type(e).__name__}: {e}")
    rs.cleanup(cfg.ctx)


def short(observed: dict[str, Any]) -> dict[str, Any]:
    """출력용 요약. 긴 목록은 결과 파일에만 남긴다."""
    o = {k: v for k, v in observed.items() if k not in ("missing_timesteps", "sse")}
    o["sse"] = {k: v for k, v in observed.get("sse", {}).items() if k != "timeline"}
    return o


# ---------------------------------------------------------------- 실행

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("scenario", choices=["s0-normal", "s1-short-drop", "s2-long-drop", "s3-half-open", "all"])
    p.add_argument("--bff", default="http://api-server:8080")
    p.add_argument("--fake", default="http://mlagents-gym:8000")
    p.add_argument("--toxiproxy", default="http://toxiproxy:8474", help="Toxiproxy API")
    p.add_argument("--web", default="http://toxiproxy:8080", help="브라우저가 여는 주소 (Toxiproxy 경유)")
    p.add_argument("--upstream", default="nginx:80", help="Toxiproxy가 넘겨 줄 웹 서버")
    p.add_argument("--drop-sec", type=float, default=10.0)
    p.add_argument("--long-drop-sec", type=float, default=20.0)
    p.add_argument("--stall-sec", type=float, default=90.0)
    p.add_argument("--out", help="결과 JSON 경로. 같은 폴더에 시나리오별 SSE 기록과 콘솔 로그를 남긴다.")
    args = p.parse_args()
    cfg = Cfg(args)

    def case(name: str, rate: int, fault: tuple[str, float] | None) -> Any:
        sec = fault[1] if fault else 0
        # 장애 전 지표 + 장애 동안 쌓일 지표 + 복구 뒤 AFTER_FAULT_SEC 동안의 지표
        n = BEFORE_FAULT_METRICS + int(rate * (sec + AFTER_FAULT_SEC)) if fault else 40
        return lambda browser: run_case(cfg, browser, name, metrics_per_sec=rate, n_metrics=n, fault=fault)

    runners = {
        "s0-normal": case("s0-normal", 4, None),
        "s1-short-drop": case("s1-short-drop", 4, ("drop", args.drop_sec)),
        "s2-long-drop": case("s2-long-drop", 4, ("drop", args.long_drop_sec)),
        # 90초 동안 멈추므로 학습이 너무 길어지지 않게 초당 1개로 보낸다.
        "s3-half-open": case("s3-half-open", 1, ("half-open", args.stall_sec)),
    }
    names = list(runners) if args.scenario == "all" else [args.scenario]
    results: dict[str, Any] = {}
    failed = False
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        results["_env"] = {"playwright_browser": f"chromium {browser.version}"}
        for name in names:
            print(f"\n=== {name} ===", flush=True)
            t0 = time.monotonic()
            try:
                res = runners[name](browser)
            except Exception as e:
                res = {"error": f"{type(e).__name__}: {e}", "checks": {"completed": False}}
            finally:
                cleanup(cfg)
            res["elapsed_sec"] = round(time.monotonic() - t0, 1)
            results[name] = res
            for check, passed in res["checks"].items():
                print(f"  [{'PASS' if passed else 'FAIL'}] {check}")
                failed |= not passed
            if "error" in res:
                print(f"  error: {res['error']}")
            print("  observed:", json.dumps(short(res.get("observed", {})), ensure_ascii=False, default=str), flush=True)
        browser.close()

    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    print(f"\n결과: {'실패한 판정 있음' if failed else '모든 판정 통과'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
