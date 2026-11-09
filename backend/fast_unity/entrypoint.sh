#!/usr/bin/env bash
set -euo pipefail

# 안전한 런타임 디렉터리
: "${XDG_RUNTIME_DIR:=/tmp/runtime-root}"
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"

# 1) Xvfb 시작
Xvfb :1 -screen 0 640x360x24 -ac +extension GLX +render -noreset >/tmp/xvfb.log 2>&1 &

# 소켓 대기
for i in {1..30}; do
  [ -S "/tmp/.X11-unix/X1" ] && break
  sleep 0.2
done

# 모든 프로세스가 동일 디스플레이 사용
export DISPLAY=:1
export LIBGL_ALWAYS_SOFTWARE=1

# 2) VNC 비밀번호
mkdir -p /root/.vnc
if [ ! -f /root/.vnc/passwd ]; then
  x11vnc -storepasswd "${VNC_PASS:-changeme}" /root/.vnc/passwd
fi

# 3) Unity 실행파일 권한 (이슈 #3)
#    git에 실행 권한(755)이 기록되어 있어 보통은 할 일이 없고, 권한이 빠진 경우의 안전망이다.
#    실행 파일은 envs/<환경>/env.x86_64 깊이에 있으므로 -maxdepth 2로 찾는다.
#    읽기 전용 마운트 등으로 chmod가 실패해도 기동은 계속한다.
if [ -d /app/unity/envs ]; then
  find /app/unity/envs -maxdepth 2 -type f -name "*.x86_64" ! -perm -u+x -print \
    -exec chmod +x {} + 2>/dev/null \
    || echo "[entrypoint] WARN: Unity 실행 권한 부여 실패" >&2
fi

# 4) 윈도우 매니저 (선택)
fluxbox >/tmp/fluxbox.log 2>&1 &

# 5) x11vnc
x11vnc \
  -display :1 \
  -rfbport 5900 \
  -listen 0.0.0.0 \
  -rfbauth /root/.vnc/passwd \
  -forever -shared -noxdamage -nolookup \
  -o /tmp/x11vnc.log -verbose &

# 6) noVNC (옵션)
websockify --web=/usr/share/novnc/ 6080 localhost:5900 >/tmp/novnc.log 2>&1 &

# 7) 커맨드 실행: 인자 있으면 그걸, 없으면 uvicorn
if [ "$#" -gt 0 ]; then
  exec "$@"
else
  exec python -m uvicorn app.main:app \
    --host 0.0.0.0 --port 8000 \
    --proxy-headers --forwarded-allow-ips="*"
fi
