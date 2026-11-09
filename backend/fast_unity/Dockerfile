FROM python:3.10.12-slim

# 필수 패키지 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates wget git \
    xvfb x11vnc fluxbox \
    libstdc++6 libgcc-s1 zlib1g \
    libglib2.0-0 libgdk-pixbuf-2.0-0 libgtk-3-0 libatk1.0-0 \
    libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libfreetype6 \
    libx11-6 libxext6 libxrender1 libxrandr2 libxinerama1 libxcursor1 libxi6 \
    libxcomposite1 libxdamage1 libxfixes3 libxss1 libxtst6 libsm6 libxft2 \
    libasound2 libnss3 \
    libgl1-mesa-glx libglu1-mesa libgl1-mesa-dri libdrm2 libgbm1 libxkbcommon0 libxshmfence1 libxxf86vm1 libvulkan1 \
    fonts-dejavu-core \
    x11-utils \
 && rm -rf /var/lib/apt/lists/*

 RUN apt-get update && apt-get install -y --no-install-recommends \
    libx11-dev libxext-dev libxrandr-dev libxi-dev libxrender-dev libxfixes-dev \
 && rm -rf /var/lib/apt/lists/*
 
WORKDIR /app

ENV PYTHONPATH=/app



# Python 의존성: 해시까지 고정한 lock 파일 하나로 설치한다. (이슈 #3)
# - torch는 CPU 빌드를 PyTorch CPU 저장소에서 받는다.
# - lock에 하위 의존성까지 모두 고정되어 있으므로 --no-deps로 다시 해석하지 않는다.
#   (SB3 2.6.0과 torch 2.1.2의 버전 충돌은 overrides.txt 참고)
# - pip는 베이스 이미지에 포함된 버전을 그대로 쓴다. (버전 미지정 업그레이드 제거)
COPY fast_unity/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes --no-deps \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      -r /tmp/requirements.lock

COPY fast_unity/app /app/app
COPY fast_unity/unity  /app/unity
COPY fast_unity/entrypoint.sh /app/

RUN chmod +x /app/entrypoint.sh


ENV DISPLAY=:1 \
    XDG_RUNTIME_DIR=/tmp/runtime-root \
    LIBGL_ALWAYS_SOFTWARE=1
RUN mkdir -p /tmp/runtime-root /app/logs && chmod 700 /tmp/runtime-root


EXPOSE 8000 6080 5900

ENTRYPOINT ["/app/entrypoint.sh"]
