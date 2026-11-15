"""학습 서버 이미지에서 앱 모듈과 학습 스택이 실제로 동작하는지 확인한다. (이슈 #3)

- FastAPI 앱과 학습 실행 모듈을 import한다.
- SB3 2.6.0 + torch 2.1.2(의도적으로 유지한 버전 충돌) 조합으로 PPO를 짧게 학습·저장·로드한다.
- Unity 없이 gymnasium 기본 환경(CartPole)을 쓴다. 이미지의 entrypoint를 거치지 않고 실행한다.
- 실제 지표 콜백이 BFF 전송 대기열로 지표를 보내고, 실패하면 다시 보내는지 확인한다. (이슈 #16)
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


# 실제 지표 콜백 → 전송 대기열 → HTTP 전송 확인 (이슈 #16)
# 받는 쪽은 첫 요청에 503을 돌려준다. 전송 대기열이 같은 지표를 다시 보내 결국 모두 도착해야 한다.
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from unity.train_util.real_time_log_callback import StreamTrainMetricsCallback

received = []  # (응답 코드, timesteps)


class _Stub(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        code = 503 if not received else 201
        received.append((code, body["timesteps"]))
        self.send_response(code)
        self.end_headers()

    def log_message(self, *args):
        pass


server = HTTPServer(("127.0.0.1", 0), _Stub)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{server.server_port}/training-metrics"

model = PPO("MlpPolicy", gym.make("CartPole-v1"), n_steps=64, batch_size=32, n_epochs=1, verbose=0)
model.learn(total_timesteps=128, callback=StreamTrainMetricsCallback(run_id="ci", http_endpoint_url=url))
deadline = time.monotonic() + 15
while time.monotonic() < deadline and sorted(t for c, t in received if c == 201) != [64, 128]:
    time.sleep(0.2)
server.shutdown()
assert sorted(t for c, t in received if c == 201) == [64, 128], received
assert received[0] == (503, 64), received
print(f"METRIC_RESEND=ok (503 응답 뒤 같은 지표 재전송, 요청 {len(received)}개 {received})")
