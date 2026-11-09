"""대역 진입점.

실제 학습 서버의 app.main(라우터 구성, CORS)을 그대로 불러오고,
대역 전용 제어 주소만 덧붙인다. app.services.unity_service는 이미지 빌드 때
대역 구현으로 교체되어 있다.
"""

import app.services.unity_service as _unity_service
from app.main import app
from fake.control import router as fake_router

# 이미지 구성이 잘못되어 실제 UnityService가 들어 있으면 바로 실패시킨다.
if not hasattr(_unity_service, "FakeLLMFeedback"):
    raise RuntimeError("app/services/unity_service.py가 대역 구현이 아닙니다. Dockerfile을 확인하세요.")

app.include_router(fake_router)
