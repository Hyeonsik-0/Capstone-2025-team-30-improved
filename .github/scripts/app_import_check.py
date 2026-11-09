"""학습 서버 이미지에서 앱 모듈과 학습 스택이 실제로 동작하는지 확인한다. (이슈 #3)

- FastAPI 앱과 학습 실행 모듈을 import한다.
- SB3 2.6.0 + torch 2.1.2(의도적으로 유지한 버전 충돌) 조합으로 PPO를 짧게 학습·저장·로드한다.
- Unity 없이 gymnasium 기본 환경(CartPole)을 쓴다. 이미지의 entrypoint를 거치지 않고 실행한다.
"""
import tempfile

import gymnasium as gym
import stable_baselines3
import torch
from stable_baselines3 import PPO

import app.main  # noqa: F401  FastAPI 앱 전체 import (라우터·서비스 포함)
import unity.train.train_runner  # noqa: F401  학습 실행 모듈

model = PPO("MlpPolicy", gym.make("CartPole-v1"), n_steps=64, batch_size=32, n_epochs=1, verbose=0)
model.learn(total_timesteps=128)
with tempfile.TemporaryDirectory() as d:
    model.save(f"{d}/ppo")
    PPO.load(f"{d}/ppo")

print(f"APP_IMPORT=ok")
print(f"SB3_PPO_LEARN_SAVE_LOAD=ok (sb3 {stable_baselines3.__version__}, torch {torch.__version__})")
