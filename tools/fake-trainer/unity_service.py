"""app/services/unity_service.py 대역 (이슈 #7).

이미지 빌드 때 실제 파일 자리에 덮어써진다. 실제 라우트(app/api/routes/train.py)는
이 파일의 UnityService를 그대로 import해서 쓴다.

실제 UnityService와 같게 둔 것
- 메서드 이름·인자·반환값, 상태 확인 조건, RuntimeError 메시지
  (라우트가 예외를 잡지 않으므로 실제와 같이 500으로 응답한다)
- run_in_executor로 학습·추론을 별도 스레드에서 실행하는 방식
- 학습 종료 시 finish_train_callback으로 상태를 초기화하는 순서

바꾼 것
- 학습·추론 루프 → fake.training (Unity·SB3 없음)
- Unity 사이드 채널 → 받은 명령을 기록만 하는 FakeSideChannel
- LLM 피드백 → OpenAI를 호출하지 않고, 실제 AddMessage처럼 메시지 저장 후 재개만 한다
- [장애 스위치] start·pause·resume·stop의 실패·응답 지연 (기본값 꺼짐)
"""

import asyncio
import json
import threading
from collections import deque
from functools import partial

from app.schemas.training import SimSpeed, TestRequest, TrainRequest
from fake.faults import FAULTS, FakeSideChannel
from fake.training import run_fake_inference, run_fake_training


class UnityTrainingState:
    # unity/train_util/training_state.py UnityTrainingState 와 같다.
    def __init__(self):
        self.is_running = False
        self.is_paused = False
        self.is_stopped = False
        self._lock = threading.Lock()

    def set_paused(self, pause: bool):
        with self._lock:
            self.is_paused = pause

    def set_running(self, running: bool):
        with self._lock:
            self.is_running = running

    def set_stop(self, stop: bool):
        with self._lock:
            self.is_stopped = stop

    def reset(self):
        self.is_running = False
        self.is_paused = False
        self.is_stopped = False


UnityInferenceState = UnityTrainingState


class FakeLLMFeedback:
    # unity/train_util/sentiment_feedback_wrapper.py SentimentLLMFeedback.AddMessage 와 같은 동작.
    # (실제 OpenAI 호출은 학습 루프 안에서 메시지를 꺼낼 때 일어나므로 여기서는 없다)
    def __init__(self, unpause_callback=None):
        self._message_queue = deque(maxlen=1)
        self._lock = threading.Lock()
        self.unpause_callback = unpause_callback

    def AddMessage(self, message: str) -> bool:
        with self._lock:
            self._message_queue.append(message)
        if self.unpause_callback:
            self.unpause_callback()
        return True


async def _control_fault_before(action: str) -> None:
    if FAULTS.get().control[action].fail:
        raise RuntimeError(f"[fake] 장애 스위치로 {action} 요청을 실패시켰습니다.")


async def _control_fault_after(action: str) -> None:
    delay = FAULTS.get().control[action].delay_sec
    if delay > 0:
        await asyncio.sleep(delay)


class UnityService:
    def __init__(self):
        self.train_states = UnityTrainingState()
        self.inference_states = UnityInferenceState()
        self.llm_feedback_handlers: dict[str, FakeLLMFeedback] = {}
        self.side_channel = FakeSideChannel()
        self.current_run_id: str = None

    def finish_train_callback(self):
        """학습 종료 시 호출되는 콜백. 관련 리소스를 정리합니다."""
        if self.current_run_id and self.current_run_id in self.llm_feedback_handlers:
            del self.llm_feedback_handlers[self.current_run_id]
        self.current_run_id = None
        self.train_states.reset()
        print("[UnityService] 실험 상태 초기화 완료")

    async def start(self, req: TrainRequest, exeriment_id: str):
        await _control_fault_before("start")
        if self.train_states.is_running or self.current_run_id is not None:
            raise RuntimeError("다른 학습이 이미 실행 중입니다.")

        if self.inference_states.is_running:
            raise RuntimeError("모델 추론(테스트)이 실행 중이므로 학습을 시작할 수 없습니다.")

        def unpause_simulation():
            if self.train_states.is_paused:
                self.train_states.set_paused(False)

        llm_handler = FakeLLMFeedback(unpause_callback=unpause_simulation)
        self.llm_feedback_handlers[exeriment_id] = llm_handler
        self.current_run_id = exeriment_id

        self.train_states.is_running = True
        loop = asyncio.get_running_loop()
        task = partial(run_fake_training, req, self.train_states, self.current_run_id, self)
        loop.run_in_executor(None, task)
        await _control_fault_after("start")
        # 실제 start는 값을 돌려주지 않아 응답이 {"status": null} 이다.

    async def stop(self, exeriment_id: str):
        await _control_fault_before("stop")
        if self.current_run_id != exeriment_id:
            raise RuntimeError("요청한 ID와 일치하는 실행 중인 작업이 없습니다.")

        if self.train_states.is_running or self.train_states.is_paused:
            self.train_states.is_stopped = True
            self.side_channel.send_command("stop", "")
            await _control_fault_after("stop")
            return "학습 중지 신호를 보냈습니다."
        elif self.inference_states.is_running:
            self.inference_states.set_stop(True)
            await _control_fault_after("stop")
            return "추론(테스트) 중지 신호를 보냈습니다."

        raise RuntimeError("중지할 작업(학습 또는 테스트)이 실행 중이 아닙니다.")

    async def pause(self, exeriment_id: str):
        await _control_fault_before("pause")
        if self.current_run_id != exeriment_id:
            raise RuntimeError("요청한 ID와 일치하는 실행 중인 작업이 없습니다.")

        if self.train_states.is_running and not self.train_states.is_paused:
            self.train_states.set_paused(True)
            await _control_fault_after("pause")
            return "학습을 일시정지했습니다."
        elif self.inference_states.is_running and not self.inference_states.is_paused:
            self.inference_states.set_paused(True)
            await _control_fault_after("pause")
            return "추론(테스트)을 일시정지했습니다."

        raise RuntimeError("일시정지할 작업이 없거나 이미 일시정지 상태입니다.")

    async def resume(self, exeriment_id: str):
        await _control_fault_before("resume")
        if self.current_run_id != exeriment_id:
            raise RuntimeError("요청한 ID와 일치하는 실행 중인 작업이 없습니다.")

        if self.train_states.is_running and self.train_states.is_paused:
            self.train_states.set_paused(False)
            self.side_channel.send_command("resume", "")
            await _control_fault_after("resume")
            return "학습을 재개했습니다."
        elif self.inference_states.is_running and self.inference_states.is_paused:
            self.inference_states.set_paused(False)
            await _control_fault_after("resume")
            return "추론(테스트)을 재개했습니다."

        raise RuntimeError("재개할 작업이 없거나 이미 실행 중인 상태입니다.")

    async def set_speed(self, exeriment_id: str, sim_speed: SimSpeed):
        if self.current_run_id == exeriment_id:
            if self.train_states.is_running:
                msg = json.dumps({"simSpeed": sim_speed.sim_speed}, ensure_ascii=False)
                self.side_channel.send_command("simSpeed", msg)

    async def add_llm_feedback(self, run_id: str, message: str) -> bool:
        if run_id != self.current_run_id or not self.train_states.is_running:
            raise RuntimeError("피드백을 전달할 학습 세션이 실행 중이 아닙니다.")

        if not self.train_states.is_paused:
            raise RuntimeError("LLM 피드백은 학습이 '일시정지' 상태일 때만 가능합니다.")

        handler = self.llm_feedback_handlers.get(run_id)
        if not handler:
            raise RuntimeError("해당 학습 세션의 LLM 피드백 핸들러를 찾을 수 없습니다.")

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, handler.AddMessage, message)

    async def inference_model(self, req: TestRequest, run_id: str):
        if self.inference_states.is_running:
            raise RuntimeError("다른 모델 추론(테스트)이 이미 실행 중입니다.")

        if self.train_states.is_running:
            raise RuntimeError("학습이 실행 중이므로 모델 추론(테스트)을 시작할 수 없습니다.")

        self.current_run_id = run_id
        self.inference_states.is_running = True
        loop = asyncio.get_running_loop()

        def task_wrapper():
            try:
                run_fake_inference(req, self.inference_states, run_id)
            except Exception as e:
                print(f"[inference_model][ERROR] 추론 중 예외 발생: {e}")
            finally:
                self.inference_states.is_running = False
                self.current_run_id = None
                print("추론(테스트)이 종료되었습니다.")

        loop.run_in_executor(None, task_wrapper)
        return "모델 추론(테스트)을 시작했습니다."
