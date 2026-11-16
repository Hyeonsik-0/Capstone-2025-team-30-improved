"""브라우저 측정 공통 도우미 (이슈 #9, #18)

- 학습 서버 대역 측정 시나리오(run_scenarios.py)의 BFF·대역 조작 함수를 그대로 쓴다.
- Toxiproxy 프록시 두 개를 관리한다.
  - web:       브라우저 → nginx (대시보드, API, SSE, 화면 쪽 시그널링 /ws)
  - signaling: 가짜 Unity 송출기 → 시그널링 서버 (Unity 쪽 시그널링)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fake-trainer" / "scenarios"))
import run_scenarios as rs  # noqa: E402

WEB_PROXY = "web"
SIGNALING_PROXY = "signaling"


def now_ms() -> int:
    return int(time.time() * 1000)


class Cfg:
    def __init__(self, args: argparse.Namespace) -> None:
        self.ctx = rs.Ctx(argparse.Namespace(bff=args.bff, fake=args.fake, project="rl-e2e"))
        self.toxiproxy = args.toxiproxy.rstrip("/")
        self.web = args.web.rstrip("/")
        self.unity_signaling = args.unity_signaling
        self.proxies = {
            WEB_PROXY: ("0.0.0.0:8080", args.upstream),
            SIGNALING_PROXY: ("0.0.0.0:8081", args.signaling_upstream),
        }
        self.out_dir = Path(args.out).parent if args.out else None


def toxi(cfg: Cfg, method: str, path: str, body: Any = None) -> Any:
    return rs.must(rs.http(method, f"{cfg.toxiproxy}{path}", body, timeout=10), ok=(200, 201, 204))


def proxies_ready(cfg: Cfg) -> None:
    """프록시가 없으면 만들고, 모두 초기 상태(켜짐, toxic 없음)로 되돌린다."""
    for name, (listen, upstream) in cfg.proxies.items():
        status, _ = rs.http("GET", f"{cfg.toxiproxy}/proxies/{name}", timeout=10)
        if status == 404:
            toxi(cfg, "POST", "/proxies", {"name": name, "listen": listen, "upstream": upstream, "enabled": True})
    toxi(cfg, "POST", "/reset")


def set_proxy(cfg: Cfg, name: str, enabled: bool) -> None:
    """프록시를 끄면 열린 연결을 모두 닫고, 새 연결은 거부한다."""
    toxi(cfg, "POST", f"/proxies/{name}", {"enabled": enabled})


def add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--bff", default="http://api-server:8080")
    p.add_argument("--fake", default="http://mlagents-gym:8000")
    p.add_argument("--toxiproxy", default="http://toxiproxy:8474", help="Toxiproxy API")
    p.add_argument("--web", default="http://toxiproxy:8080", help="브라우저가 여는 주소 (Toxiproxy 경유)")
    p.add_argument("--upstream", default="nginx:80", help="web 프록시가 넘겨 줄 웹 서버")
    p.add_argument("--signaling-upstream", default="signaling-server:80",
                   help="signaling 프록시가 넘겨 줄 시그널링 서버")
    p.add_argument("--unity-signaling", default="ws://toxiproxy:8081",
                   help="가짜 Unity 송출기가 붙는 시그널링 주소 (Toxiproxy 경유)")
