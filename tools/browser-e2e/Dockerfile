# 브라우저 측정 컨테이너 (이슈 #9)
# 공식 이미지에는 브라우저(Chromium 등)와 시스템 의존성만 있고 Playwright Python 패키지는 없다.
# 패키지는 이미지의 브라우저 버전과 같은 버전을 해시 포함 lock으로 설치한다. (requirements.in 참고)
FROM mcr.microsoft.com/playwright/python:v1.64.1-noble@sha256:330d401ae23c905896a0c26c66c36fc971b621519e7b11b435a8328d7bf6e031

COPY requirements.lock /tmp/requirements.lock
# 측정 전용 컨테이너라 가상 환경 없이 시스템 Python에 설치한다. (이미지에 python3-venv 없음)
RUN pip install --no-cache-dir --break-system-packages --require-hashes -r /tmp/requirements.lock \
    && rm /tmp/requirements.lock
