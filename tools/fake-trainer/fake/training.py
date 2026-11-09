"""학습 루프·추론 루프 대역.

실제 코드의 대응 위치 (README의 대응표 참고)
- 학습 진입:      unity/train/train_runner.py run_training
- 환경 생성·init: unity/train/env_factory.py make_env, make_env_inference
- 로그 CSV:       unity/train_util/logger_callback.py EpisodeCSVCallback
- 일시정지·중지:  unity/train_util/training_state.py PauseResumeCallback._on_step
- 지표 전송:      unity/train_util/real_time_log_callback.py StreamTrainMetricsCallback
- 완료 신호:      unity/train_util/training_state.py PauseResumeCallback._on_training_end
- 추론:           unity/train/train_runner.py test_model

SB3 학습 대신 스텝 수만 늘리고, 지표 값은 진행률로 만든 가짜 값이다.
BFF와 주고받는 요청(주소, 본문 형식, timeout, 실패 처리)은 실제 코드와 같게 둔다.
"""

from __future__ import annotations

import json
import math
import os
import random
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from fake.faults import FAULTS, PROGRESS, SENT_LOG

MODELS_PATH = Path("/app/models")
LOGS_PATH = Path("/app/train_logs")

# 실제 서버는 StreamTrainMetricsCallback의 _is_off_policy로 나눈다.
# (isinstance(model, (DQN, SAC, DDPG, TD3)) — tsc·hf-llm·srl도 DQN 기반이라 off-policy)
ON_POLICY = {"ppo", "a2c"}
# SB3 기본 n_steps (BFF가 hyperparams에 n_steps를 보내지 않았을 때)
DEFAULT_N_STEPS = {"ppo": 2048, "a2c": 5}
# StreamTrainMetricsCallback(log_interval_steps=1000) 기본값
OFF_POLICY_LOG_INTERVAL = 1000
# 실제 코드의 timeout 값 (real_time_log_callback.py:191, training_state.py:169, train_runner.py:106)
METRICS_TIMEOUT_SEC = 2
COMPLETION_TIMEOUT_SEC = 5
# PauseResumeCallback(check_interval=0.3)
PAUSE_CHECK_SEC = 0.3
# 진행을 나누는 단위 시간 (대역 전용)
TICK_SEC = 0.05
# 추론 1에피소드 길이 (대역 전용)
INFERENCE_EPISODE_SEC = 1.0


class FakeCrash(Exception):
    """장애 스위치로 일으킨 학습 스레드 비정상 종료."""


def _api_base() -> str | None:
    return os.getenv("API_SERVER_URL")


def _post(kind: str, url: str, payload: dict[str, Any], timeout: float,
          run_id: str, **extra: Any) -> None:
    """실제 코드처럼 응답 코드는 보지 않고, 네트워크 오류만 잡아 로그를 남긴다.
    대역은 결과를 전송 기록에 함께 남긴다."""
    t0 = time.monotonic()
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        result: Any = resp.status_code
    except requests.RequestException as e:
        result = f"error:{type(e).__name__}"
        print(f"[fake][HTTP][ERROR] {kind} 전송 실패: {e}")
    SENT_LOG.add(kind=kind, run_id=run_id, result=result,
                 elapsed_ms=round((time.monotonic() - t0) * 1000), **extra)


def _format_metric_value(key: str, value: Any) -> Any:
    # real_time_log_callback.py _format_metric_value 와 같은 규칙
    if isinstance(value, float):
        if "time" in key:
            return int(value)
        return round(value, 3)
    return value


def _fake_kvs(algorithm: str, num: int, total: int, interval: int, elapsed: float,
              hp: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """알고리즘별로 실제 SB3 로거에 남는 키와 같은 이름의 가짜 값을 만든다.

    키 이름은 SB3 2.6.0 소스의 logger.record 호출(ppo.py, a2c.py, dqn.py, sac.py,
    common/base_class.py의 train/learning_rate)과 저장소의 실제 DQN 학습 로그
    (backend/fast_unity/train_logs/teacher_cnn_car_dqn_run1/progress.csv)로 확인했다.
    train/* 값은 train()이 한 번이라도 돈 뒤에만 남긴다.

    실제와 다른 점: 실제 콜백은 SB3 로거의 현재 값(name_to_value)을 읽는데, 로거가 주기적으로
    dump하면서 값을 비우므로 off-policy에서는 전송 시점에 따라 train/*·exploration_rate가 빠질 수 있다.
    대역은 그런 누락 없이 항상 같은 키를 보낸다. (BFF는 빠진 키를 null로 처리한다)
    """
    p = min(num / max(total, 1), 1.0)
    noise = lambda s: rng.uniform(-s, s)  # noqa: E731
    kvs: dict[str, Any] = {
        "rollout/ep_rew_mean": -1.0 + 2.0 * p + noise(0.1),
        "rollout/ep_len_mean": 100.0 + 50.0 * p + noise(5),
    }
    learning_starts = int(hp.get("learning_starts", 100))
    if algorithm in ON_POLICY:
        # on-policy: collect_rollouts(→ 지표 전송) 다음에 train()이 돌므로 첫 롤아웃에는 train/* 값이 없다.
        if num > interval:
            common = {
                "train/entropy_loss": -1.0 + 0.5 * p + noise(0.05),
                "train/explained_variance": 0.5 * p + noise(0.05),
                "train/learning_rate": float(hp.get("learning_rate", 0.0003 if algorithm == "ppo" else 0.0007)),
                "train/value_loss": 2.0 - 1.5 * p + noise(0.1),
            }
            kvs.update(common)
            if algorithm == "ppo":
                kvs.update({
                    "train/approx_kl": 0.01 + noise(0.005),
                    "train/clip_fraction": 0.1 + noise(0.05),
                    "train/clip_range": float(hp.get("clip_range", 0.2)),
                    "train/loss": 1.0 - 0.8 * p + noise(0.05),
                    "train/n_updates": (num // interval - 1) * int(hp.get("n_epochs", 10)),
                    "train/policy_gradient_loss": -0.01 + noise(0.005),
                })
            else:  # a2c
                kvs.update({
                    "train/n_updates": num // interval - 1,
                    "train/policy_loss": -0.01 + noise(0.005),
                })
    elif algorithm == "sac":
        if num > learning_starts:
            kvs.update({
                "train/actor_loss": -5.0 * p + noise(0.5),
                "train/critic_loss": 1.0 - 0.5 * p + noise(0.1),
                "train/ent_coef": 0.1 + noise(0.01),
                "train/ent_coef_loss": noise(0.1),
                "train/learning_rate": float(hp.get("learning_rate", 0.0003)),
                "train/n_updates": num - learning_starts,
            })
    else:  # dqn, tsc, hf-llm, srl (DQN 기반)
        frac = float(hp.get("exploration_fraction", 0.1))
        final_eps = float(hp.get("exploration_final_eps", 0.05))
        eps = max(final_eps, 1.0 - (1.0 - final_eps) * (p / frac if frac > 0 else 1.0))
        kvs["rollout/exploration_rate"] = eps  # DQN._on_step에서 매 스텝 기록
        if num > learning_starts:
            kvs["train/learning_rate"] = float(hp.get("learning_rate", 0.0001))
            kvs["train/loss"] = 0.5 - 0.4 * p + noise(0.05)
            kvs["train/n_updates"] = (num - learning_starts) // int(hp.get("train_freq", 4))
    # 실제 콜백이 마지막에 덮어쓰는 time/* 값
    kvs["time/time_elapsed"] = elapsed
    kvs["time/total_timesteps"] = num
    return kvs


def _build_metrics_payload(run_id: str, phase: str, num: int, kvs: dict[str, Any]) -> dict[str, Any]:
    # real_time_log_callback.py _build_metrics_payload 와 같은 형식
    metrics = []
    for key, value in kvs.items():
        parts = key.split("/", 1)
        group, name = parts if len(parts) == 2 else ("general", key)
        metrics.append({"name": name, "value": _format_metric_value(key, value), "group": group})
    return {
        "runId": run_id,
        "phase": phase,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "timesteps": int(num),
        "metrics": metrics,
    }


def _metric_interval(req) -> int:
    if req.algorithm in ON_POLICY:
        return int(req.hyperparams.get("n_steps", DEFAULT_N_STEPS[req.algorithm]))
    return OFF_POLICY_LOG_INTERVAL


def _open_fake_csv(model_name: str) -> Path:
    """unity/train_util/logger_callback.py EpisodeCSVCallback._on_training_start 처럼
    학습을 시작할 때 로그 CSV를 헤더와 함께 만든다. 학습 중·비정상 종료 후에도 산출물 확인 API가
    실제와 같이 로그가 있다고 답하게 하는 용도다.
    (실제는 에피소드마다 한 줄을 쓰지만, 대역에는 에피소드가 없어 지표를 보낼 때마다 한 줄을 쓴다.)"""
    log_dir = LOGS_PATH / model_name
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{model_name}.csv"
    path.write_text("ts,episode_index,env_index,episode_reward,episode_length,wall_time\n", encoding="utf-8")
    return path


def _save_fake_model(model_name: str) -> None:
    """PauseResumeCallback._on_training_end 의 model.save 대역. 내용은 가짜다."""
    MODELS_PATH.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(MODELS_PATH / f"{model_name}.zip", "w") as zf:
        zf.writestr("FAKE_MODEL.txt", "학습 서버 대역이 만든 가짜 모델 파일입니다.\n")


def run_fake_training(req, state, run_id: str, service) -> None:
    """train_runner.run_training 대역. 실제와 같이 run_in_executor 스레드에서 실행된다."""
    try:
        interval = _metric_interval(req)
        on_policy = req.algorithm in ON_POLICY
        # on-policy는 롤아웃 단위로 진행하므로 total을 넘겨 롤아웃 경계까지 간다.
        target = math.ceil(req.total_timesteps / interval) * interval if on_policy else req.total_timesteps
        phase = "rollout" if on_policy else "step"
        base = _api_base()
        metrics_endpoint = f"{base}/training-metrics" if base else None
        rng = random.Random(run_id)
        PROGRESS.reset(run_id=run_id, num_timesteps=0, target=target, interval=interval,
                       phase=phase, stalled=False, loop_active=True, finished=False)

        # env_factory.make_env: 환경을 만든 뒤 사이드 채널로 환경 파라미터를 보낸다.
        service.side_channel.send_command("init", json.dumps(req.envparams, ensure_ascii=False))
        csv_path = _open_fake_csv(req.model_name)

        start = time.time()
        num = 0
        next_metric = interval
        triggered = False
        episode = 0

        while num < target:
            # PauseResumeCallback._on_step: 일시정지 중에는 진행하지 않고, 중지 신호를 확인한다.
            while state.is_paused:
                if state.is_stopped:
                    triggered = True
                    break
                if FAULTS.crash_pending():  # [장애] 일시정지 중에도 비정상 종료는 바로 적용
                    break
                time.sleep(PAUSE_CHECK_SEC)
            if state.is_stopped and not FAULTS.crash_pending():
                triggered = True
                break

            # [장애] 학습 루프 멈춤: 진행·지표·완료 신호가 모두 멈춘다.
            # (Unity가 응답하지 않아 env.step에서 막힌 상황 등. 멈춘 동안에는 중지 신호도 확인하지 못한다.)
            if FAULTS.get().stall:
                PROGRESS.set(stalled=True)
                while FAULTS.get().stall and not FAULTS.crash_pending():
                    time.sleep(0.1)
                PROGRESS.set(stalled=False)
            # [장애] 학습 스레드 비정상 종료: 완료 신호 없이 끝난다.
            # (학습 중 예외가 나면 실제 서버도 _on_training_end 없이 finish_train_callback만 실행된다.)
            if FAULTS.consume_crash():
                raise FakeCrash("장애 스위치로 학습 스레드를 종료했습니다.")

            sps = FAULTS.get().steps_per_sec
            steps = min(max(1, int(sps * TICK_SEC)), next_metric - num)
            time.sleep(steps / sps)
            num += steps
            PROGRESS.set(num_timesteps=num)

            if num >= next_metric:
                kvs = _fake_kvs(req.algorithm, num, req.total_timesteps, interval,
                                time.time() - start, req.hyperparams, rng)
                payload = _build_metrics_payload(run_id, phase, num, kvs)
                episode += 1
                with csv_path.open("a", encoding="utf-8") as f:
                    f.write(f"{num},{episode},0,{kvs['rollout/ep_rew_mean']:.3f},"
                            f"{kvs['rollout/ep_len_mean']:.0f},{time.time():.3f}\n")
                if metrics_endpoint:
                    # 실제와 같이 학습 스레드에서 동기 호출, 실패하면 버린다. (재시도 없음)
                    sends = 2 if FAULTS.get().duplicate_metrics else 1
                    for _ in range(sends):
                        _post("metric", metrics_endpoint, payload, METRICS_TIMEOUT_SEC,
                              run_id, timesteps=num, phase=phase)
                next_metric += interval

        # 루프가 끝났으므로 이후의 비정상 종료 요청은 받지 않는다. (/_fake/crash가 확인)
        PROGRESS.set(loop_active=False)
        # PauseResumeCallback._on_training_end: 모델 저장 → 완료 신호 1회
        _save_fake_model(req.model_name)
        if base and run_id:
            status = "STOPPED" if triggered else "COMPLETED"
            _post("experiment-completed", f"{base}/callbacks/{run_id}/experiment-completed",
                  {"status": status}, COMPLETION_TIMEOUT_SEC, run_id, status=status)
    except FakeCrash as e:
        print(f"[fake] {e}")
    finally:
        # 처리되지 않은 비정상 종료 요청이 다음 학습으로 넘어가지 않게 지운다.
        FAULTS.consume_crash()
        PROGRESS.set(loop_active=False, finished=True)
        service.finish_train_callback()


def run_fake_inference(req, state, run_id: str, side_channel) -> None:
    """train_runner.test_model 대역."""
    try:
        # env_factory.make_env_inference: 환경 생성 후 init 명령 → 모델 로드 순서.
        # 실제 서버는 모델 파일을 불러오지 못하면 예외가 나고, finally에서 완료 신호를 보낸다.
        side_channel.send_command("init", json.dumps(req.envparams, ensure_ascii=False))
        model_path = MODELS_PATH / f"{req.model_name}.zip"
        if not model_path.exists():
            raise FileNotFoundError(f"모델 파일이 없습니다: {model_path}")
        for _ in range(req.episodesnum):
            if state.is_stopped:
                break
            end = time.monotonic() + INFERENCE_EPISODE_SEC
            while time.monotonic() < end:
                while state.is_paused:
                    if state.is_stopped:
                        break
                    time.sleep(0.1)
                if state.is_stopped:
                    break
                time.sleep(TICK_SEC)
    finally:
        base = _api_base()
        if base and run_id:
            status = "STOPPED" if state.is_stopped else "COMPLETED"
            _post("test-completed", f"{base}/callbacks/{run_id}/test-completed",
                  {"status": status}, COMPLETION_TIMEOUT_SEC, run_id, status=status)
