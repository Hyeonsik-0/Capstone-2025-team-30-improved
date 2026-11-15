// 대시보드 페이지의 WebRTC 동작 기록 (이슈 #18)
// 페이지 코드보다 먼저 실행된다. 동작은 바꾸지 않고 기록만 한다.
// - RTCPeerConnection: 생성, ICE·연결 상태 변화, 트랙 수신, close
// - 시그널링 WebSocket(/ws): 생성, open, close
// - 화면: 연결 상태 문구와 영상 재생 여부를 250ms마다 확인해, 바뀔 때만 남긴다.
//   영상 재생 = video 요소의 디코딩된 프레임 수가 직전 확인 이후 늘었음
(() => {
  if (window.__E2E_RTC__) return;
  const log = [];
  window.__E2E_RTC__ = log;
  const rec = (kind, extra) => log.push(Object.assign({ t: Date.now(), kind }, extra || {}));

  const NativePC = window.RTCPeerConnection;
  if (NativePC) {
    let seq = 0;
    class RecordedPC extends NativePC {
      constructor(...args) {
        super(...args);
        const id = ++seq;
        rec('pc-new', { pc: id });
        this.addEventListener('iceconnectionstatechange', () => rec('pc-ice', { pc: id, state: this.iceConnectionState }));
        this.addEventListener('connectionstatechange', () => rec('pc-conn', { pc: id, state: this.connectionState }));
        this.addEventListener('track', (e) => rec('pc-track', { pc: id, kind: e.track.kind }));
        this.__e2eId = id;
      }
      close() {
        rec('pc-close', { pc: this.__e2eId });
        super.close();
      }
    }
    window.RTCPeerConnection = RecordedPC;
  }

  const NativeWS = window.WebSocket;
  class RecordedWS extends NativeWS {
    constructor(url, protocols) {
      super(url, protocols);
      if (String(url).includes('/ws')) {
        rec('ws-new');
        this.addEventListener('open', () => rec('ws-open'));
        this.addEventListener('close', (e) => rec('ws-close', { code: e.code }));
      }
    }
  }
  window.WebSocket = RecordedWS;

  let last = { status: null, playing: null };
  let lastFrames = 0;
  setInterval(() => {
    const statusEl = [...document.querySelectorAll('div, span')].find(
      (el) => el.children.length === 0 && /^(연결됨|연결 안됨|연결 중.*|연결 실패.*)$/.test(el.textContent.trim()));
    const status = statusEl ? statusEl.textContent.trim() : null;
    const video = document.querySelector('video');
    const frames = video && video.getVideoPlaybackQuality ? video.getVideoPlaybackQuality().totalVideoFrames : 0;
    const playing = !!video && frames > lastFrames;
    lastFrames = frames;
    if (status !== last.status || playing !== last.playing) {
      rec('ui', { status, playing, frames });
      last = { status, playing };
    }
  }, 250);
})();
