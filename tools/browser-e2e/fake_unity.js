// 가짜 Unity 송출기 (이슈 #18)
//
// 측정 컨테이너의 브라우저 페이지에서 실행된다. 실제 Unity Render Streaming처럼 시그널링 서버(public 모드)에
// WebSocket으로 붙고, 화면이 보낸 offer에 답한 뒤 영상 트랙을 붙여 다시 협상한다.
//   1) 화면 offer(데이터 채널만) 수신 → answer 전송
//   2) 영상 트랙 추가 → 송출기가 offer 전송 → 화면 answer 수신
//   3) ICE 후보는 양쪽이 시그널링으로 주고받는다
// 메시지 형식은 frontend/src/core/webrtc/signaling.ts, backend/signaling-server/src/class/websockethandler.ts 기준.
//
// 측정 스크립트가 쓰는 조작
//   join(url)   시그널링에 붙는다. WebSocket이 끊기면 3초마다 다시 붙는다 (Unity가 준비된 상태)
//   leave()     영상 연결과 WebSocket을 모두 닫고 다시 붙지 않는다 (Unity 프로세스 종료)
//   dropMedia() 영상 연결만 닫고 WebSocket은 유지한다 (영상 경로만 끊김)
(() => {
  const RECONNECT_MS = 3000;
  const log = [];
  const rec = (kind, extra) => log.push(Object.assign({ t: Date.now(), kind }, extra || {}));

  // 영상: 캔버스에 프레임 번호를 그려 15fps로 보낸다.
  const canvas = document.createElement('canvas');
  canvas.width = 320;
  canvas.height = 180;
  document.body.appendChild(canvas);
  const g = canvas.getContext('2d');
  let frame = 0;
  setInterval(() => {
    frame += 1;
    g.fillStyle = `hsl(${(frame * 7) % 360}, 60%, 40%)`;
    g.fillRect(0, 0, canvas.width, canvas.height);
    g.fillStyle = '#fff';
    g.font = '28px sans-serif';
    g.fillText(`fake unity ${frame}`, 20, 100);
  }, 1000 / 15);
  const stream = canvas.captureStream(15);

  let url = null;
  let ws = null;
  let joined = false;
  const peers = new Map(); // connectionId -> RTCPeerConnection

  function send(msg) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
  }

  function closePeer(connectionId, why) {
    const pc = peers.get(connectionId);
    if (!pc) return;
    peers.delete(connectionId);
    pc.close();
    rec('pc-close', { connectionId, why });
  }

  function newPeer(connectionId) {
    const pc = new RTCPeerConnection();
    peers.set(connectionId, pc);
    rec('pc-new', { connectionId });
    pc.onicecandidate = (e) => {
      if (!e.candidate) return;
      send({ type: 'candidate', from: connectionId, data: {
        candidate: e.candidate.candidate, sdpMLineIndex: e.candidate.sdpMLineIndex,
        sdpMid: e.candidate.sdpMid, connectionId } });
    };
    pc.oniceconnectionstatechange = () => rec('pc-ice', { connectionId, state: pc.iceConnectionState });
    pc.ondatachannel = () => rec('pc-datachannel', { connectionId });
    pc.onnegotiationneeded = async () => {
      // 영상 트랙을 붙인 뒤의 재협상: 송출기가 offer를 보낸다 (실제 Unity와 같은 방향)
      try {
        await pc.setLocalDescription(await pc.createOffer());
        send({ type: 'offer', from: connectionId, data: { sdp: pc.localDescription.sdp, connectionId } });
        rec('offer-sent', { connectionId });
      } catch (err) {
        rec('error', { where: 'negotiation', message: String(err) });
      }
    };
    return pc;
  }

  async function onMessage(msg) {
    const connectionId = msg.from || msg.connectionId;
    if (msg.type === 'offer') {
      // 화면이 새 연결로 offer를 보냈다. 이전 연결이 남아 있으면 정리한다.
      closePeer(connectionId, 'new-offer');
      const pc = newPeer(connectionId);
      rec('offer-received', { connectionId });
      await pc.setRemoteDescription({ type: 'offer', sdp: msg.data.sdp });
      await pc.setLocalDescription(await pc.createAnswer());
      send({ type: 'answer', from: connectionId, data: { sdp: pc.localDescription.sdp, connectionId } });
      rec('answer-sent', { connectionId });
      pc.addTrack(stream.getVideoTracks()[0], stream); // → negotiationneeded
    } else if (msg.type === 'answer') {
      const pc = peers.get(connectionId);
      if (pc && pc.signalingState === 'have-local-offer') {
        await pc.setRemoteDescription({ type: 'answer', sdp: msg.data.sdp });
        rec('answer-received', { connectionId });
      }
    } else if (msg.type === 'candidate') {
      const pc = peers.get(connectionId);
      if (pc && msg.data.candidate) {
        try {
          await pc.addIceCandidate({ candidate: msg.data.candidate, sdpMid: msg.data.sdpMid,
                                     sdpMLineIndex: msg.data.sdpMLineIndex });
        } catch (err) {
          rec('error', { where: 'candidate', message: String(err) });
        }
      }
    } else if (msg.type === 'disconnect') {
      closePeer(msg.connectionId, 'signaling-disconnect');
    }
  }

  function connect() {
    if (!joined) return;
    ws = new WebSocket(url);
    rec('ws-new');
    ws.onopen = () => rec('ws-open');
    ws.onmessage = (e) => onMessage(JSON.parse(e.data)).catch((err) =>
      rec('error', { where: 'message', message: String(err) }));
    ws.onclose = () => {
      rec('ws-close');
      if (joined) setTimeout(connect, RECONNECT_MS);
    };
  }

  window.fakeUnity = {
    log,
    join(signalingUrl) {
      url = signalingUrl;
      joined = true;
      rec('join');
      connect();
    },
    leave() {
      joined = false;
      rec('leave');
      for (const id of [...peers.keys()]) closePeer(id, 'leave');
      if (ws) ws.close();
    },
    dropMedia() {
      rec('drop-media');
      for (const id of [...peers.keys()]) closePeer(id, 'drop-media');
    },
    peerCount: () => peers.size,
  };
})();
