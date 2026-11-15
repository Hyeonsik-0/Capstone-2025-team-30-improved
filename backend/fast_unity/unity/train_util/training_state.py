
from stable_baselines3.common.callbacks import BaseCallback
import time
import threading
import os
import requests
from unity.train_util.sidechannel import RLSideChannel
from unity.train_util.bff_sender import get_sender
from typing import Optional, Dict, Any
from contextlib import contextmanager
class UnityTrainingState:
     def __init__(self):
        self.is_running = False
        self.is_paused = False
        self.is_stopped = False

        # 멀티스레드 안전을 위한 락
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
    

class UnityInferenceState:
    def __init__(self):
        self.is_running = False
        self.is_paused = False
        self.is_stopped = False

        # 멀티스레드 안전을 위한 락 추가
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
        
# 학습 상태를 제어 할 수 있는 콜백
class PauseResumeCallback(BaseCallback):
    def __init__(
        self,
        state,
        side_channel,
        save_path: str,
        run_id : str,
        check_interval: float = 0.3,
        debug_log_freq: Optional[int] = 500,
        verbose: int = 0,
    ):
        super().__init__(verbose)
        self.state = state
        self.side_channel = side_channel
        self.save_path = save_path
        self.check_interval = check_interval
        self.debug_log_freq = debug_log_freq
        self.triggered = False
        self.run_id = run_id
        self._orig_train = None
        
    def __getstate__(self) -> Dict[str, Any]:
        state = self.__dict__.copy()
        # 피클 불가 가능성이 있는 참조는 저장 제외
        state["_orig_train"] = None
        return state
       
        
    @contextmanager
    def _clean_model_for_save(self):
        """
        저장 직전에 모델에서 피클 불가 참조를 잠시 떼고, 저장 후 원복
        """
        # 1) 몽키패치 복구 (사용 안 하면 None)
        if self._orig_train is not None:
            self.model.train = self._orig_train
            self._orig_train = None

        # 2) 모델에 붙어 있을 수 있는 커스텀 참조 제거
        removed = {}
        for attr in ("_loss_stream_callback", "_metrics_cb", "_streamer"):
            if hasattr(self.model, attr):
                removed[attr] = getattr(self.model, attr)
                setattr(self.model, attr, None)

        try:
            yield
        finally:
            # 3) 원복
            for k, v in removed.items():
                setattr(self.model, k, v)


    def _on_step(self) -> bool:
        # 외부 일시정지 신호 처리
        while (self.state.is_paused):
            if self.verbose > 0:
                print("[SB3] 학습 일시정지 중...")
            if (self.state.is_stopped):
                # 정지 신호가 들어오면 학습 루프 종료
                self.triggered = True
                return False
            time.sleep(self.check_interval)

        # 디버그용 로거나 출력(선택)
        # if self.debug_log_freq and self.num_timesteps > 0 and (self.num_timesteps % self.debug_log_freq == 0):
        #     try:
        #         print(dict(self.model.logger.name_to_value))
        #     except Exception:
        #         pass

        # 외부 정지 신호 즉시 중단
        if (self.state.is_stopped):
            self.triggered = True
            return False

        return True
    
    def _on_training_end(self) -> None:
        # 안전 저장
        if self.verbose:
            print("[PauseResumeCallback] saving on training end ->", self.save_path)

        try:
            with self._clean_model_for_save():
                self.model.save(self.save_path)
        except Exception as e:
            print("[PauseResumeCallback][WARN] model.save 실패:", e)

        # 환경 닫기 시도 (VecEnv 포함)
        try:
            env = self.model.get_env()
            if env is not None:
                env.close()
                if self.verbose:
                    print("[PauseResumeCallback] env closed")
        except Exception as e:
            print("[PauseResumeCallback][WARN] env.close() 실패:", e)
        # 여기서 신호 보내자
        base_api_server_url = os.environ.get("API_SERVER_URL")
        if base_api_server_url and self.run_id:
            endpoint = f"{base_api_server_url}/callbacks/{self.run_id}/experiment-completed"
            if self.verbose:
                print(f"[PauseResumeCallback] 학습 완료 신호를 보냅니다 -> {endpoint}")
                
            # is_stopped 상태에 따라 성공/중단 상태를 body에 담아 전송
            status_payload = {"status": "STOPPED" if self.triggered else "COMPLETED"}
            # 지표와 같은 전송 대기열의 끝에 넣는다. 먼저 넣은 지표를 다 보낸 뒤 보내고, 실패하면 다시 보낸다. (이슈 #16)
            get_sender().send("experiment-completed", endpoint, status_payload, timeout=5, run_id=self.run_id)

        # 로컬 상태 리셋
        try:
            self.state.reset()
        except Exception:
            pass

        if self.triggered and self.verbose:
            print("[PauseResumeCallback] training ended by external stop signal")
            
            
class CustomExplorationCallback(BaseCallback):
    """
    DQN의 모험율(exploration rate)을 단계적으로 조절하는 콜백.
    - 웜업 기간(warmup_fraction) 동안: 지정된 'warmup_eps' 값을 유지.
    - 웜업 이후: 지정된 'post_warmup_eps' 값을 학습 끝까지 유지.
    """
    def __init__(
        self,
        total_timesteps: int,
        warmup_fraction: float = 0.05,
        warmup_eps: float = 1.0,
        post_warmup_eps: float = 0.1,
        verbose: int = 0,
    ):
        super(CustomExplorationCallback, self).__init__(verbose)
        self.total_timesteps = total_timesteps
        self.warmup_end_step = int(total_timesteps * warmup_fraction)
        self.warmup_eps = warmup_eps
        self.post_warmup_eps = post_warmup_eps

    def _on_step(self) -> bool:
        if self.num_timesteps < self.warmup_end_step:
            # 웜업 기간: 모험율을 고정값으로 유지
            new_eps = self.warmup_eps
        else:
            # 웜업 이후: 다른 고정값으로 유지
            new_eps = self.post_warmup_eps
        
        self.model.exploration_rate = new_eps
        self.logger.record("rollout/exploration_rate", self.model.exploration_rate)

        # --- TeacherFeedbackWrapper의 커스텀 정보 로깅 추가 ---
        # self.locals에서 마지막 스텝의 info 딕셔너리를 가져옵니다.
        if "infos" in self.locals and len(self.locals["infos"]) > 0:
            info = self.locals["infos"][0]
            # info 딕셔너리에 있는 'tfw_'로 시작하는 값들을 로그에 기록합니다.
            for key, value in info.items():
                if key.startswith("tfw_"):
                    self.logger.record(f"custom/{key}", value)

        return True