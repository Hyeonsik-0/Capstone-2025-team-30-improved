"""대역 전용 제어 주소 /_fake/*. BFF는 호출하지 않고, 측정 시나리오만 쓴다."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException

from app.api.routes import train as train_routes
from fake.faults import FAULTS, PROGRESS, SENT_LOG

router = APIRouter(prefix="/_fake", tags=["fake-trainer"])


def _service():
    # 실제 라우트 모듈이 만든 UnityService 인스턴스를 그대로 본다.
    return train_routes.unity_service


@router.get("/state")
def get_state() -> dict[str, Any]:
    svc = _service()
    ts, ins = svc.train_states, svc.inference_states
    return {
        "current_run_id": svc.current_run_id,
        "train": {"is_running": ts.is_running, "is_paused": ts.is_paused, "is_stopped": ts.is_stopped},
        "inference": {"is_running": ins.is_running, "is_paused": ins.is_paused, "is_stopped": ins.is_stopped},
        "progress": PROGRESS.snapshot(),
        "faults": FAULTS.get().to_dict(),
        "crash_pending": FAULTS.crash_pending(),
        "side_channel": svc.side_channel.recent(),
    }


@router.get("/faults")
def get_faults() -> dict[str, Any]:
    return FAULTS.get().to_dict()


@router.put("/faults")
def put_faults(patch: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """바꿀 항목만 보낸다. 예: {"stall": true}, {"control": {"pause": {"fail": true}}}"""
    try:
        return FAULTS.update(patch).to_dict()
    except (ValueError, TypeError, AttributeError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/faults/reset")
def reset_faults() -> dict[str, Any]:
    return FAULTS.reset().to_dict()


@router.post("/crash")
def crash() -> dict[str, Any]:
    """진행 중인 학습 스레드를 완료 신호 없이 종료시킨다.

    학습 루프가 끝난 뒤(모델 저장·완료 신호 전송 중)에는 받지 않는다.
    받아 두면 쓰이지 않은 요청이 다음 학습을 시작하자마자 종료시키기 때문이다.
    """
    if not (_service().train_states.is_running and PROGRESS.snapshot().get("loop_active")):
        raise HTTPException(status_code=409, detail="진행 중인 학습 루프가 없습니다.")
    FAULTS.request_crash()
    return {"accepted": True}


@router.get("/sent")
def get_sent(run_id: Optional[str] = None) -> dict[str, Any]:
    return SENT_LOG.query(run_id)


@router.delete("/sent")
def clear_sent() -> dict[str, Any]:
    SENT_LOG.clear()
    return {"cleared": True}
