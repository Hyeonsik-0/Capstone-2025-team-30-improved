"""학습 서버 이미지에서 Unity 환경이 실제로 뜨는지 확인한다. (이슈 #3)

- 앱의 gym_wrapper.py와 같은 인자로 UnityEnvironment를 띄우고 handshake와 첫 reset까지 걸린 시간을 잰다.
- 컨테이너 entrypoint(Xvfb 기동 포함)를 거쳐 실행되며, 작업 디렉터리는 /app이다.
- 사용법: python unity_smoke.py <환경 이름> <대기 시간(초)>
"""
import sys
import time

from mlagents_envs.environment import UnityEnvironment

env_name = sys.argv[1] if len(sys.argv) > 1 else "car"
timeout_wait = int(sys.argv[2]) if len(sys.argv) > 2 else 180

t0 = time.monotonic()
env = UnityEnvironment(
    file_name=f"unity/envs/{env_name}/env.x86_64",
    base_port=5005,
    no_graphics=False,
    worker_id=0,
    timeout_wait=timeout_wait,
    additional_args=["-screen-width", "640", "-screen-height", "360", "-logFile", f"/ci-out/unity-{env_name}.log"],
)
t1 = time.monotonic()
env.reset()
t2 = time.monotonic()
print(f"UNITY_ENV={env_name}")
print(f"UNITY_HANDSHAKE_SECONDS={t1 - t0:.1f}")
print(f"UNITY_FIRST_RESET_SECONDS={t2 - t1:.1f}")
print(f"UNITY_BEHAVIORS={','.join(env.behavior_specs.keys())}")
env.close()
