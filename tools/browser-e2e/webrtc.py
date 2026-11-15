"""시뮬레이터 영상(WebRTC) 연결·재연결 측정 시나리오 (이슈 #18)

가짜 Unity 송출기(fake_unity.js)를 별도 브라우저 페이지로 띄워 시그널링 서버에 붙이고,
대시보드 페이지에서 사용자가 '재연결' 버튼을 눌러 영상을 연결한다.
(페이지에 다시 들어오면 브라우저 자동 재생 정책 때문에 버튼으로 연결한다)

시나리오
  w0-normal            Unity가 먼저 붙어 있는 정상 연결
  w1-unity-late        Unity가 붙기 전에 연결을 요청하고, --unity-late-sec 뒤에 Unity가 붙음
  w2-unity-restart     영상이 나오는 중 Unity가 종료(시그널링·영상 모두 끊김)되고 --unity-away-sec 뒤 다시 붙음
  w3-media-drop        영상이 나오는 중 영상 경로만 끊김 (Unity는 시그널링에 그대로 붙어 있음)
  w4-unity-signaling-drop  영상이 나오는 중 Unity 쪽 시그널링 연결만 --signaling-down-sec 동안 끊김 (영상 경로는 그대로)
  w5-web-drop          영상이 나오는 중 브라우저–nginx 사이가 --web-down-sec 동안 끊김 (화면 쪽 시그널링·API 포함)

시그널링 서버 자체의 재시작(서버가 죽어 양쪽에 아무것도 알리지 못하는 상황)은 재현하지 않는다.
측정 컨테이너는 Docker를 다룰 수 없고, 서버가 살아 있으면 한쪽 연결이 끊길 때 다른 쪽에 disconnect를 알리기 때문이다.

측정값은 대시보드의 연결 상태 문구와 영상 재생 여부(디코딩 프레임 증가)를 250ms마다 기록한 것에서 계산한다.
판정은 측정 도구와 장애 주입이 의도대로 동작했는지만 본다. 화면의 반응은 관찰값으로 기록한다.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import Browser, Page

from common import SIGNALING_PROXY, WEB_PROXY, Cfg, now_ms, proxies_ready, rs, set_proxy

HERE = Path(__file__).resolve().parent
RTC_HOOK = (HERE / "rtc_hook.js").read_text(encoding="utf-8")
FAKE_UNITY = (HERE / "fake_unity.js").read_text(encoding="utf-8")
RECONNECT_BUTTON = re.compile(r"^재연결")
VIDEO_WAIT_MS = 20_000


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--unity-late-sec", type=float, default=10.0)
    p.add_argument("--unity-away-sec", type=float, default=10.0)
    p.add_argument("--signaling-down-sec", type=float, default=10.0)
    p.add_argument("--web-down-sec", type=float, default=10.0)
    p.add_argument("--observe-sec", type=float, default=45.0, help="장애·복구 뒤 화면을 지켜보는 시간")


# ---------------------------------------------------------------- 준비

def start_long_run(cfg: Cfg, name: str) -> str:
    """영상 측정 동안 '진행 중'으로 남아 있을 실험을 시작한다. 끝나면 cleanup이 중지한다."""
    ctx = cfg.ctx
    rs.fake_faults(ctx, {"steps_per_sec": 2048}, reset=True)
    proxies_ready(cfg)
    run_id = rs.start_run(ctx, f"e2e-{name}", "ppo", {"n_steps": 2048}, 2048 * 600)
    rs.wait_until(lambda: rs.run_status(ctx, run_id) == "RUNNING", 30, what="실험 RUNNING")
    return run_id


def open_unity(browser: Browser) -> tuple[Any, Page]:
    context = browser.new_context()
    page = context.new_page()
    page.set_content("<html><body></body></html>")
    page.add_script_tag(content=FAKE_UNITY)
    return context, page


def open_dashboard(cfg: Cfg, browser: Browser, run_id: str, console: list[str]) -> tuple[Any, Page]:
    context = browser.new_context()
    context.add_init_script(RTC_HOOK)
    page = context.new_page()
    page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
    page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
    page.goto(f"{cfg.web}/?runId={run_id}")
    page.wait_for_function("""(runId) => {
      const s = window.__E2E__ && window.__E2E__.experimentStore.getState();
      return s && s.runId === runId && s.trainingStatus === 'running';
    }""", arg=run_id, timeout=30_000)
    page.get_by_role("button", name=RECONNECT_BUTTON).wait_for(timeout=15_000)
    page.wait_for_timeout(1000)
    return context, page


def click_connect(page: Page) -> int:
    """사용자가 '재연결' 버튼을 누른다. 페이지에 다시 들어왔을 때 영상 연결을 시작하는 방법이다."""
    t = now_ms()
    page.get_by_role("button", name=RECONNECT_BUTTON).click()
    return t


def unity_log(unity: Page) -> list[dict[str, Any]]:
    return unity.evaluate("() => window.fakeUnity.log")


def wait_unity(unity: Page, kind: str, since: int, timeout_ms: int = 15_000) -> int | None:
    end = now_ms() + timeout_ms
    while now_ms() < end:
        hit = next((e["t"] for e in unity_log(unity) if e["kind"] == kind and e["t"] >= since), None)
        if hit:
            return hit
        unity.wait_for_timeout(200)
    return None


def wait_playing(page: Page, since: int, timeout_ms: int = VIDEO_WAIT_MS) -> int | None:
    end = now_ms() + timeout_ms
    while now_ms() < end:
        t = first_ui(page.evaluate("() => window.__E2E_RTC__"), since, lambda e: e["playing"])
        if t is not None:
            return t
        page.wait_for_timeout(250)
    return None


# ---------------------------------------------------------------- 분석

def first_ui(log: list[dict[str, Any]], since: int, cond: Callable[[dict[str, Any]], bool]) -> int | None:
    return next((e["t"] for e in log if e["kind"] == "ui" and e["t"] >= since and cond(e)), None)


def ui_at(log: list[dict[str, Any]], t: int) -> dict[str, Any]:
    """시각 t의 화면 상태 (그 직전에 기록된 ui 이벤트)"""
    last = {"status": None, "playing": False}
    for e in log:
        if e["kind"] == "ui" and e["t"] <= t:
            last = e
    return last


def connected_without_video_ms(log: list[dict[str, Any]], t_from: int, t_to: int) -> int:
    """'연결됨'이라고 표시했지만 영상이 나오지 않은 시간의 합"""
    total, cur, cur_t = 0, ui_at(log, t_from), t_from
    for e in log:
        if e["kind"] != "ui" or e["t"] <= t_from or e["t"] > t_to:
            continue
        if cur.get("status") == "연결됨" and not cur.get("playing"):
            total += e["t"] - cur_t
        cur, cur_t = e, e["t"]
    if cur.get("status") == "연결됨" and not cur.get("playing"):
        total += t_to - cur_t
    return total


def rel(t: int | None, base: int) -> float | None:
    return None if t is None else round((t - base) / 1000, 1)


def summarize(log: list[dict[str, Any]], t_ref: int, t_end: int) -> dict[str, Any]:
    end = ui_at(log, t_end)
    return {
        "connections_created": sum(1 for e in log if e["kind"] == "pc-new" and t_ref <= e["t"] <= t_end),
        "status_at_end": end.get("status"),
        "playing_at_end": bool(end.get("playing")),
        # 기준 시각 이후 화면에 보인 상태 변화: [기준 기준 초, 상태 문구, 영상 재생 여부]
        "ui_timeline": [[rel(e["t"], t_ref), e["status"], e["playing"]]
                        for e in log if e["kind"] == "ui" and t_ref <= e["t"] <= t_end],
        "rtc_timeline": [[rel(e["t"], t_ref), e["kind"], e.get("state") or e.get("code")]
                         for e in log if e["kind"] in ("pc-new", "pc-ice", "pc-close", "ws-new", "ws-close")
                         and t_ref <= e["t"] <= t_end],
    }


# ---------------------------------------------------------------- 시나리오

def run_case(cfg: Cfg, browser: Browser, name: str, args: argparse.Namespace) -> dict[str, Any]:
    run_id = start_long_run(cfg, name)
    console: list[str] = []
    unity_ctx, unity = open_unity(browser)
    dash_ctx, page = open_dashboard(cfg, browser, run_id, console)
    checks: dict[str, bool] = {}
    observed: dict[str, Any] = {"run_id": run_id}
    observe_ms = int(args.observe_sec * 1000)
    try:
        if name == "w1-unity-late":
            t_click = click_connect(page)
            page.wait_for_timeout(int(args.unity_late_sec * 1000))
            t_join = now_ms()
            unity.evaluate("(url) => window.fakeUnity.join(url)", cfg.unity_signaling)
            checks["unity_joined_late"] = wait_unity(unity, "ws-open", t_join) is not None
            page.wait_for_timeout(observe_ms)
            t_end = now_ms()
            log = page.evaluate("() => window.__E2E_RTC__")
            observed.update({
                "unity_late_sec": args.unity_late_sec,
                "video_after_unity_join_sec": rel(first_ui(log, t_join, lambda e: e["playing"]), t_join),
                "connected_shown_after_click_sec": rel(first_ui(log, t_click, lambda e: e["status"] == "연결됨"), t_click),
                "connected_without_video_sec": round(connected_without_video_ms(log, t_click, t_end) / 1000, 1),
                **summarize(log, t_click, t_end),
            })
        else:
            t_join = now_ms()
            unity.evaluate("(url) => window.fakeUnity.join(url)", cfg.unity_signaling)
            checks["unity_joined"] = wait_unity(unity, "ws-open", t_join) is not None
            t_click = click_connect(page)
            t_video = wait_playing(page, t_click)
            log = page.evaluate("() => window.__E2E_RTC__")
            observed.update({
                "video_after_click_sec": rel(t_video, t_click),
                "connected_shown_after_click_sec": rel(first_ui(log, t_click, lambda e: e["status"] == "연결됨"), t_click),
                "connected_shown_before_video_sec": round(
                    connected_without_video_ms(log, t_click, t_video or now_ms()) / 1000, 1),
            })
            # 측정 도구 확인: 가짜 송출기의 영상이 화면에 나오는가 (장애 시나리오의 전제)
            checks["video_before_fault"] = t_video is not None

            if name != "w0-normal" and t_video is not None:
                page.wait_for_timeout(3000)
                t_fault = now_ms()
                if name == "w2-unity-restart":
                    unity.evaluate("() => window.fakeUnity.leave()")
                    checks["unity_left"] = wait_unity(unity, "ws-close", t_fault) is not None
                    page.wait_for_timeout(int(args.unity_away_sec * 1000))
                    t_back = now_ms()
                    unity.evaluate("(url) => window.fakeUnity.join(url)", cfg.unity_signaling)
                    checks["unity_rejoined"] = wait_unity(unity, "ws-open", t_back) is not None
                elif name == "w3-media-drop":
                    unity.evaluate("() => window.fakeUnity.dropMedia()")
                    t_back = t_fault
                    checks["unity_media_dropped"] = wait_unity(unity, "pc-close", t_fault) is not None
                elif name == "w4-unity-signaling-drop":
                    set_proxy(cfg, SIGNALING_PROXY, False)
                    checks["unity_signaling_closed"] = wait_unity(unity, "ws-close", t_fault) is not None
                    page.wait_for_timeout(int(args.signaling_down_sec * 1000))
                    set_proxy(cfg, SIGNALING_PROXY, True)
                    t_back = now_ms()
                elif name == "w5-web-drop":
                    set_proxy(cfg, WEB_PROXY, False)
                    page.wait_for_timeout(int(args.web_down_sec * 1000))
                    set_proxy(cfg, WEB_PROXY, True)
                    t_back = now_ms()
                page.wait_for_timeout(observe_ms)
                t_end = now_ms()
                log = page.evaluate("() => window.__E2E_RTC__")
                if name == "w5-web-drop":
                    # 측정 도구 확인: 브라우저 쪽 시그널링 연결이 실제로 끊겼는가
                    checks["dashboard_signaling_closed"] = any(
                        e["kind"] == "ws-close" and t_fault <= e["t"] <= t_back for e in log)
                observed.update({
                    "fault": name.split("-", 1)[1],
                    # 장애 뒤 화면 상태가 처음 바뀐 시각, 영상이 멈춘 시각, 다시 나온 시각
                    "status_changed_after_fault_sec": rel(first_ui(
                        log, t_fault + 1, lambda e: e["status"] != ui_at(log, t_fault)["status"]), t_fault),
                    "video_stopped_after_fault_sec": rel(first_ui(log, t_fault + 1, lambda e: not e["playing"]), t_fault),
                    "video_back_after_recovery_sec": rel(first_ui(
                        log, max(t_back, first_ui(log, t_fault + 1, lambda e: not e["playing"]) or t_back),
                        lambda e: e["playing"]), t_back),
                    "connected_without_video_sec": round(connected_without_video_ms(log, t_fault, t_end) / 1000, 1),
                    **summarize(log, t_fault, t_end),
                })
            else:
                page.wait_for_timeout(3000)
                log = page.evaluate("() => window.__E2E_RTC__")
                observed.update(summarize(log, t_click, now_ms()))
        unity_events = unity_log(unity)
        # 가짜 송출기가 받은 화면의 연결 요청(offer) 수. 시그널링 서버가 전달하지 못한 요청은 세지 않는다.
        observed["unity_offers_received"] = sum(1 for e in unity_events if e["kind"] == "offer-received")
    finally:
        dash_ctx.close()
        unity.evaluate("() => window.fakeUnity.leave()") if not unity.is_closed() else None
        unity_ctx.close()
        if cfg.out_dir:
            (cfg.out_dir / f"{name}.console.log").write_text("\n".join(console) + "\n")
    if cfg.out_dir:
        (cfg.out_dir / f"{name}.rtc.json").write_text(
            json.dumps({"dashboard": log, "fake_unity": unity_events}, ensure_ascii=False))
    return {"checks": checks, "observed": observed}


NAMES = ["w0-normal", "w1-unity-late", "w2-unity-restart", "w3-media-drop", "w4-unity-signaling-drop", "w5-web-drop"]


def runners(cfg: Cfg, args: argparse.Namespace) -> dict[str, Callable[[Browser], dict[str, Any]]]:
    return {n: (lambda browser, n=n: run_case(cfg, browser, n, args)) for n in NAMES}


def short(observed: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in observed.items() if k not in ("ui_timeline", "rtc_timeline")}


