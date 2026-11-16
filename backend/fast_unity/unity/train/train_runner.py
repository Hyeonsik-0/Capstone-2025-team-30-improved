import os
import time
import requests

from app.schemas.training import  TrainRequest, TestRequest
from unity.train_util.sidechannel import RLSideChannel
from unity.train_util.training_state import UnityTrainingState, UnityInferenceState, PauseResumeCallback,CustomExplorationCallback
from unity.train_util.logger_callback import EpisodeCSVCallback
from unity.train_util.real_time_log_callback import StreamTrainMetricsCallback
from unity.train_util.bff_sender import start_heartbeat
from unity.train_util.gym_wrapper import MLAgentsGymWrapper
from unity.train_util.sentiment_feedback_wrapper import SentimentLLMFeedback
from unity.train_util.custom_extractor import AdvancedCombinedExtractorMultipleVectors
from unity.train.env_factory import make_env, make_env_inference
from unity.train.inference import load_model
from unity.train.algo_registry import ALGORITHM_REGISTRY, AlgoAdapter
from typing import Literal, Optional
from contextlib import suppress


from stable_baselines3.common.logger import configure

def run_training(req:TrainRequest , state: UnityTrainingState, side_channel :RLSideChannel, experiment_id : str,service, llm_handler: Optional[SentimentLLMFeedback] = None):
    
    
    env = None
    model_save_path = "models/"+ req.model_name+".zip"
    heartbeat = None
    
    try :
        # 학습 스레드가 도는 동안(Unity 기동·일시정지 포함) BFF에 생존 신호를 보낸다. (이슈 #21)
        heartbeat = start_heartbeat(os.getenv("API_SERVER_URL"), experiment_id)
        env = make_env(req = req, side_channel = side_channel)
        adapter : AlgoAdapter= ALGORITHM_REGISTRY[req.algorithm]
        
        # 알고리즘에 따라 build 메서드 호출 방식 분기
        if req.algorithm == "hf-llm":
            model = adapter.build(req, env, req.hyperparams, llm_handler)
        else:
            model = adapter.build(req, env, req.hyperparams)
        log_path = "train_logs/"+ req.model_name+"/"
        # Docker Compose에서 설정한 환경 변수에서 API 서버의 기본 URL을 가져옵니다.
        base_api_server_url = os.getenv("API_SERVER_URL")
        metrics_endpoint = f"{base_api_server_url}/training-metrics" if base_api_server_url else None
        
        callbacks =[PauseResumeCallback(state, side_channel, verbose=1, save_path=model_save_path, run_id = service.current_run_id), 
                EpisodeCSVCallback(log_path, filename= req.model_name + ".csv"),
                StreamTrainMetricsCallback(run_id= experiment_id, http_endpoint_url=metrics_endpoint, verbose=1)]
        
        #---콜백 추가때문에 어쩔 수 없이---
        exploration_callback = CustomExplorationCallback(
        total_timesteps=req.total_timesteps,
        warmup_fraction=0.05,    # 웜업 기간 (5%)
        warmup_eps=0.05,          # 웜업 동안의 모험율 (100%)
        post_warmup_eps=0.08      # 웜업 이후부터 끝까지 유지할 모험율 (10%)
        )
        if(req.algorithm == "tsc"):
            callbacks.append(exploration_callback)
        #----
        logger = configure(log_path, ["stdout","csv","tensorboard"])
        model.set_logger(logger)
        model.learn(total_timesteps= req.total_timesteps, callback = callbacks)
        # init sidechannel message
    
    finally:
        # 신호를 멈추면 BFF는 완료 신호가 오지 않는 한 일정 시간 뒤 이 실험을 실패로 판정한다.
        if heartbeat is not None:
            heartbeat.stop()
        service.finish_train_callback()

        if 'env' in locals():
            with suppress(Exception):
                env.close()  # 이미 종료됐으면 조용히 무시


def test_model(req: TestRequest, state : UnityInferenceState, side_channel: RLSideChannel, run_id:str):
    env = None
    try: 
        env = make_env_inference(req = req,side_channel=side_channel)
        model = load_model(req, env)
        done = False
        for _ in range(req.episodesnum):
            if state.is_stopped:
                break
            done = False
            obs, info = env.reset()
            while not done:
                while state.is_paused:
                    print("[SB3] 추론 일시정지 중...") 
                    if state.is_stopped:
                        break
                    time.sleep(0.1)
                
                if state.is_stopped:
                    break

                action, _states = model.predict(obs, deterministic=False)
                obs, reward, terminated, truncated, info = env.step(action)
                done = terminated or truncated
                if state.is_stopped:
                    break
            else: # while 루프가 break 없이 정상 종료되었을 때만 실행
                print(f"에피소드 종료. 보상: {reward}")
            
    finally:
        base_api_server_url = os.getenv("API_SERVER_URL")
        if base_api_server_url and run_id:
            endpoint = f"{base_api_server_url}/callbacks/{run_id}/test-completed"
            try:
                # 중지되었는지, 정상 완료되었는지 상태를 구분하여 전송
                status_payload = {"status": "STOPPED" if state.is_stopped else "COMPLETED"}
                print(f"[test_model] 테스트 완료 신호({status_payload['status']})를 보냅니다 -> {endpoint}")
                requests.post(endpoint, json=status_payload, timeout=5)
            except requests.RequestException as e:
                print(f"[test_model][ERROR] Node.js API 서버로 완료 신호 전송 실패: {e}")

        try:
            if env:
                env.close()
        except Exception as e:
            print(f"[test_model][WARN] env.close() 실패: {e}")
        