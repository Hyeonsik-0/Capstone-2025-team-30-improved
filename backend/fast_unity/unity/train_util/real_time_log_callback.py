import time, json, numpy as np
from typing import Any, Dict, Optional
from datetime import datetime, timezone
from collections import deque
import requests
from stable_baselines3.common.callbacks import BaseCallback
from stable_baselines3 import DQN, SAC, DDPG, TD3 # Off-policy 알고리즘
from unity.train_util.bff_sender import get_sender

def _py_scalar(x: Any):
    if isinstance(x, np.generic):
        return np.asarray(x).item()
    return x

def _safe_kvs(logger) -> Dict[str, Any]:
    raw = dict(getattr(logger, "name_to_value", {}))
    safe = {}
    for k, v in raw.items():
        try:
            safe[k] = _py_scalar(v)
        except Exception:
            safe[k] = str(v)
    return safe

class StreamTrainMetricsCallback(BaseCallback):
    def __init__(
        self,
        run_id: str,
        log_interval_steps: int = 1000,
        http_endpoint_url: str = None,
        verbose: int = 0
    ):
        super().__init__(verbose)
        self.run_id = run_id
        # PPO의 n_steps나 DQN의 train_freq와 유사한 값으로 설정
        self.log_interval_steps = log_interval_steps
        self.http_endpoint_url = http_endpoint_url

        # 내부 상태
        self.start_time = 0
        self._last_log_step = 0
        self._is_off_policy = False
        
        # 피드백 래퍼(TFW) 통계를 위한 버퍼
        self.tfw_feedback_buffer = deque(maxlen=log_interval_steps)
        self.tfw_shaped_reward_buffer = deque(maxlen=log_interval_steps)

    def _on_training_start(self) -> None:
        """학습 시작 시 호출되어 시작 시간을 기록합니다."""
        self.start_time = time.time()
        
        # 현재 모델이 Off-policy 계열인지 확인 (DQN, SAC 등)
        self._is_off_policy = isinstance(self.model, (DQN, SAC, DDPG, TD3))

    def _on_step(self) -> bool:
        # 매 스텝마다 info 딕셔너리에서 TFW 관련 통계를 수집합니다.
        if "infos" in self.locals:
            for info in self.locals["infos"]:
                if "tfw_feedback" in info:
                    self.tfw_feedback_buffer.append(info["tfw_feedback"])
                if "tfw_shaped_reward" in info:
                    self.tfw_shaped_reward_buffer.append(info["tfw_shaped_reward"])

        # Off-policy 알고리즘(DQN 등)은 스텝 기반으로 로그를 남깁니다.
        if self._is_off_policy and self.num_timesteps >= self._last_log_step + self.log_interval_steps:
            self._last_log_step = self.num_timesteps
            kvs = self._get_extended_kvs()
            if kvs:
                self.handle_metrics("step", kvs)
        return True # 학습 계속

    def _get_extended_kvs(self) -> Dict[str, Any]:
        """
        SB3 로거의 기본 값과 함께, 버퍼에만 존재하는 rollout/*, time/* 통계를 수동으로 집계하여 반환합니다.
        """
        # 1. train/* 과 같은 기본 로그를 가져옵니다.
        kvs = _safe_kvs(self.logger)

        # 2. rollout/* 통계를 집계합니다. (ep_info_buffer 사용)
        ep_info_buffer: Optional[deque] = getattr(self.model, "ep_info_buffer", None)
        if ep_info_buffer and len(ep_info_buffer) > 0:
            if "rollout/ep_rew_mean" not in kvs:
                kvs["rollout/ep_rew_mean"] = np.mean([ep_info["r"] for ep_info in ep_info_buffer]).item()
            if "rollout/ep_len_mean" not in kvs:
                kvs["rollout/ep_len_mean"] = np.mean([ep_info["l"] for ep_info in ep_info_buffer]).item()

        # 3. time/* 통계를 집계합니다. (self.start_time 사용)
        time_elapsed = time.time() - self.start_time
        # SB3 로거의 초기 시간 값을 콜백의 정확한 시간으로 항상 덮어씁니다.
        kvs["time/time_elapsed"] = time_elapsed
        kvs["time/total_timesteps"] = self.model.num_timesteps
        
        # 4. TFW 통계를 집계합니다.
        if len(self.tfw_feedback_buffer) > 0:
            kvs["teacher/feedback_mean"] = np.mean(self.tfw_feedback_buffer).item()
        if len(self.tfw_shaped_reward_buffer) > 0:
            kvs["teacher/shaped_reward_mean"] = np.mean(self.tfw_shaped_reward_buffer).item()
            
        self.tfw_feedback_buffer.clear()
        self.tfw_shaped_reward_buffer.clear()

        return kvs

    def _format_metric_value(self, key: str, value: Any) -> Any:
        """
        메트릭 키와 값의 타입에 따라 포맷팅합니다.
        - 'time'이 포함된 키의 값은 정수로 변환합니다.
        - 그 외 모든 부동소수점 값은 소수점 3자리로 반올림합니다.
        """
        # 부동소수점 값에 대한 포맷팅 규칙
        if isinstance(value, (float, np.floating)):
            # 시간 관련 값은 정수로 변환
            if "time" in key:
                return int(value)
            # 그 외 모든 float 값은 소수점 3자리로 반올림
            return round(value, 3)

        # 그 외 타입(정수, 문자열 등)은 원래 값 그대로 반환
        return value

    def _build_metrics_payload(self, phase: str, kvs: Dict[str, Any]) -> Dict[str, Any]:
        """
        SB3 로거 KVS를 표준 메트릭 페이로드 형식으로 변환합니다.
        """
        metrics = []
        for key, value in kvs.items():
            parts = key.split("/", 1)
            if len(parts) == 2:
                group, name = parts
            else:
                group, name = "general", key

            # 값 포맷팅 로직 적용
            formatted_value = self._format_metric_value(key, value)

            metrics.append({"name": name, "value": formatted_value, "group": group})

        return {
            "runId": self.run_id,
            "phase": phase,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "timesteps": int(self.num_timesteps),
            "metrics": metrics,
        }

    # 필요한 지표 전송
    def handle_metrics(self, phase: str, kvs: Dict[str, Any]):
        payload = self._build_metrics_payload(phase, kvs)
        # 기계가 파싱하기 위한 기존 JSON 출력
        print(payload)

        # 외부 엔드포인트로 데이터 전송
        self._send_http(payload)

        # 사람이 읽기 쉬운 로그 출력
        elapsed_time = time.time() - self.start_time
        avg_reward = kvs.get("rollout/ep_rew_mean", "N/A") # SB3 로거의 평균 리워드 키

        if avg_reward == "N/A":
            avg_reward = kvs.get("train/reward", "N/A")

        log_message = (
            f"  >> 진행 상황: 스텝 {self.num_timesteps}, 경과 시간 {elapsed_time:.2f}초, 평균 리워드 {avg_reward}"
        )
        print(log_message)
        print("[METRICS]", json.dumps(payload, ensure_ascii=False))

    def _on_rollout_end(self) -> None:
        # On-policy 알고리즘(PPO 등)은 롤아웃이 끝날 때마다 로그를 남깁니다.
        if not self._is_off_policy:
            kvs = self._get_extended_kvs()
            if kvs:
                self.handle_metrics("rollout", kvs)

    def _on_training_end(self) -> None:
        pass

    def _send_http(self, payload: Dict[str, Any]):
        """지정된 HTTP 엔드포인트로 메트릭을 전송합니다."""
        """
        지정된 HTTP 엔드포인트로 메트릭을 전송합니다.

        `requests` 라이브러리의 `json` 파라미터를 사용하면
        payload 딕셔너리가 자동으로 JSON 문자열로 변환되고,
        'Content-Type: application/json' 헤더가 설정됩니다.
        """
        if not self.http_endpoint_url:
            return
        # 전송 대기열에 넣기만 한다. 전송·재시도는 전송 스레드가 순서대로 하므로 학습 루프는 기다리지 않는다. (이슈 #16)
        get_sender().send("metric", self.http_endpoint_url, payload, timeout=2,
                          run_id=self.run_id, timesteps=payload["timesteps"])
