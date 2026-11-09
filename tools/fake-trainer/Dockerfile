# 학습 서버 대역 이미지 (이슈 #7)
#
# 빌드에는 두 개의 컨텍스트가 필요하다.
#   - 기본 컨텍스트: tools/fake-trainer (대역 코드)
#   - realapp:       backend/fast_unity/app (실제 학습 서버의 HTTP 계층)
# compose 오버라이드(compose.fake.yml)가 둘 다 지정한다. 직접 빌드할 때:
#   docker build --build-context realapp=backend/fast_unity/app -t rl-fake-trainer tools/fake-trainer
#
# 실제 학습 서버와 같은 Python 버전을 쓴다. Unity·Xvfb·VNC가 없으므로 시스템 패키지는 설치하지 않는다.
FROM python:3.10.12-slim

WORKDIR /app
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements.lock

# 1) 실제 학습 서버의 app 패키지(라우트·스키마·산출물 API)를 그대로 복사한다.
COPY --from=realapp . /app/app
# 2) Unity·SB3가 필요한 서비스 계층만 대역 구현으로 덮어쓴다.
COPY unity_service.py /app/app/services/unity_service.py
COPY fake /app/fake
# 로컬에서 생긴 바이트코드 캐시가 섞이지 않게 지운다.
RUN find /app -name __pycache__ -type d -prune -exec rm -rf {} + \
 && mkdir -p /app/models /app/train_logs

EXPOSE 8000

# 실제 entrypoint.sh 마지막 줄과 같은 uvicorn 옵션. 앱만 대역 진입점(fake.main)으로 바꾼다.
CMD ["python", "-m", "uvicorn", "fake.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
