// WebRTC P2P 연결을 관리하는 모듈

import * as Logger from "./logger";

// 커스텀 이벤트 타입 정의
interface CandidateEventDetail {
  connectionId: string;
  candidate: string;
  sdpMLineIndex: number | null;
  sdpMid: string | null;
}

interface OfferAnswerEventDetail {
  connectionId: string;
  sdp: string;
}

interface ConnectionEventDetail {
  connectionId: string;
}

// Assert 함수 타입 정의
type AssertFunction = (a: any, b: any, msg: string) => void;

export default class Peer extends EventTarget {
  public connectionId: string | null;
  public polite: boolean;
  public config: RTCConfiguration;
  public pc: RTCPeerConnection | null;
  public makingOffer: boolean = false;
  public waitingAnswer: boolean = false;
  public ignoreOffer: boolean = false;
  public srdAnswerPending: boolean = false;
  public interval: number;

  private log: (str: string) => void;
  private warn: (str: string) => void;
  private assert_equals: AssertFunction;
  private sleep: (msec: number) => Promise<void>;
  // ICE disconnected는 잠깐 끊겼다 스스로 돌아오기도 하므로 바로 끊김으로 보지 않는다. (이슈 #18)
  private disconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private static readonly DISCONNECTED_GRACE_MS = 5000;

  constructor(
    connectionId: string,
    polite: boolean,
    config: RTCConfiguration,
    resendIntervalMsec: number = 5000
  ) {
    super();
    
    this.connectionId = connectionId;
    this.polite = polite;
    this.config = config;
    this.pc = new RTCPeerConnection(this.config);
    this.interval = resendIntervalMsec;

    // 로깅 함수 설정
    this.log = (str: string) => void Logger.log(`[${this.polite ? 'POLITE' : 'IMPOLITE'}] ${str}`);
    this.warn = (str: string) => void Logger.warn(`[${this.polite ? 'POLITE' : 'IMPOLITE'}] ${str}`);
    
    // Assert 함수 설정 (테스트용)
    this.assert_equals = (window as any).assert_equals || 
      ((a: any, b: any, msg: string) => {
        if (a === b) return;
        throw new Error(`${msg} expected ${b} but got ${a}`);
      });

    // Sleep 함수
    this.sleep = (msec: number) => new Promise(resolve => setTimeout(resolve, msec));

    this.setupEventHandlers();
    this.loopResendOffer();
  }

  private setupEventHandlers(): void {
    if (!this.pc) return;

    this.pc.ontrack = (e: RTCTrackEvent) => {
      this.log(`ontrack:${e}`);
      this.dispatchEvent(new CustomEvent('trackevent', { detail: e }));
    };

    this.pc.ondatachannel = (e: RTCDataChannelEvent) => {
      this.log(`ondatachannel:${e}`);
      this.dispatchEvent(new CustomEvent('adddatachannel', { detail: e }));
    };

    this.pc.onicecandidate = ({ candidate }: RTCPeerConnectionIceEvent) => {
      this.log(`send candidate:${candidate}`);
      if (candidate == null) {
        return;
      }
      
      const detail: CandidateEventDetail = {
        connectionId: this.connectionId!,
        candidate: candidate.candidate,
        sdpMLineIndex: candidate.sdpMLineIndex,
        sdpMid: candidate.sdpMid
      };
      
      this.dispatchEvent(new CustomEvent('sendcandidate', { detail }));
    };

    this.pc.onnegotiationneeded = this._onNegotiation.bind(this);

    this.pc.onsignalingstatechange = () => {
      this.log(`signalingState changed:${this.pc!.signalingState}`);
    };

    // 영상 경로 상태 (이슈 #18)
    // - connected·completed: 경로가 열림 → 'iceconnected'
    // - failed: 경로가 끊김 → 'disconnect'
    // - disconnected: 5초 안에 돌아오지 않으면 'disconnect'
    this.pc.oniceconnectionstatechange = () => {
      const state = this.pc!.iceConnectionState;
      this.log(`iceConnectionState changed:${state}`);
      if (state === 'connected' || state === 'completed') {
        this.clearDisconnectTimer();
        this.dispatchEvent(new Event('iceconnected'));
      } else if (state === 'failed') {
        this.clearDisconnectTimer();
        this.dispatchEvent(new Event('disconnect'));
      } else if (state === 'disconnected' && !this.disconnectTimer) {
        this.disconnectTimer = setTimeout(() => {
          this.disconnectTimer = null;
          if (this.pc?.iceConnectionState === 'disconnected') {
            this.dispatchEvent(new Event('disconnect'));
          }
        }, Peer.DISCONNECTED_GRACE_MS);
      }
    };

    this.pc.onicegatheringstatechange = () => {
      this.log(`iceGatheringState changed:${this.pc!.iceGatheringState}`);
    };
  }

  private async _onNegotiation(): Promise<void> {
    try {
      this.log(`SLD due to negotiationneeded`);
      this.assert_equals(this.pc!.signalingState, 'stable', 'negotiationneeded always fires in stable state');
      this.assert_equals(this.makingOffer, false, 'negotiationneeded not already in progress');
      
      this.makingOffer = true;
      await this.pc!.setLocalDescription();
      
      this.assert_equals(this.pc!.signalingState, 'have-local-offer', 'negotiationneeded not racing with onmessage');
      this.assert_equals(this.pc!.localDescription!.type, 'offer', 'negotiationneeded SLD worked');
      
      this.waitingAnswer = true;
      
      const detail: OfferAnswerEventDetail = {
        connectionId: this.connectionId!,
        sdp: this.pc!.localDescription!.sdp
      };
      
      this.dispatchEvent(new CustomEvent('sendoffer', { detail }));
    } catch (e) {
      this.log(String(e));
    } finally {
      this.makingOffer = false;
    }
  }

  private async loopResendOffer(): Promise<void> {
    while (this.connectionId) {
      if (this.pc && this.waitingAnswer && this.pc.localDescription) {
        const detail: OfferAnswerEventDetail = {
          connectionId: this.connectionId,
          sdp: this.pc.localDescription.sdp
        };
        this.dispatchEvent(new CustomEvent('sendoffer', { detail }));
      }
      await this.sleep(this.interval);
    }
  }

  private clearDisconnectTimer(): void {
    if (this.disconnectTimer) {
      clearTimeout(this.disconnectTimer);
      this.disconnectTimer = null;
    }
  }

  close(): void {
    this.connectionId = null;
    this.clearDisconnectTimer();
    if (this.pc) {
      this.pc.close();
      this.pc = null;
    }
  }

  getTransceivers(connectionId: string): RTCRtpTransceiver[] | null {
    if (this.connectionId !== connectionId || !this.pc) {
      return null;
    }
    return this.pc.getTransceivers();
  }

  addTrack(connectionId: string, track: MediaStreamTrack): RTCRtpSender | null {
    if (this.connectionId !== connectionId || !this.pc) {
      return null;
    }
    return this.pc.addTrack(track);
  }

  addTransceiver(
    connectionId: string, 
    trackOrKind: MediaStreamTrack | string, 
    init?: RTCRtpTransceiverInit
  ): RTCRtpTransceiver | null {
    if (this.connectionId !== connectionId || !this.pc) {
      return null;
    }
    return this.pc.addTransceiver(trackOrKind, init);
  }

  createDataChannel(connectionId: string, label: string): RTCDataChannel | null {
    if (this.connectionId !== connectionId || !this.pc) {
      return null;
    }
    return this.pc.createDataChannel(label);
  }

  async getStats(connectionId: string): Promise<RTCStatsReport | null> {
    if (this.connectionId !== connectionId || !this.pc) {
      return null;
    }
    return await this.pc.getStats();
  }

  async onGotDescription(connectionId: string, description: RTCSessionDescriptionInit): Promise<void> {
    if (this.connectionId !== connectionId || !this.pc) {
      return;
    }

    const isStable =
      this.pc.signalingState === 'stable' ||
      (this.pc.signalingState === 'have-local-offer' && this.srdAnswerPending);
    
    this.ignoreOffer =
      description.type === 'offer' && !this.polite && (this.makingOffer || !isStable);

    if (this.ignoreOffer) {
      this.log(`glare - ignoring offer`);
      return;
    }

    this.waitingAnswer = false;
    this.srdAnswerPending = description.type === 'answer';
    this.log(`SRD(${description.type})`);
    
    await this.pc.setRemoteDescription(description);
    this.srdAnswerPending = false;

    if (description.type === 'offer') {
      const connectionDetail: ConnectionEventDetail = { connectionId: this.connectionId };
      this.dispatchEvent(new CustomEvent('ongotoffer', { detail: connectionDetail }));

      this.assert_equals(this.pc.signalingState, 'have-remote-offer', 'Remote offer');
      this.assert_equals(this.pc.remoteDescription!.type, 'offer', 'SRD worked');
      
      this.log('SLD to get back to stable');
      await this.pc.setLocalDescription();
      
      this.assert_equals(this.pc.signalingState, 'stable', 'onmessage not racing with negotiationneeded');
      this.assert_equals(this.pc.localDescription!.type, 'answer', 'onmessage SLD worked');
      
      const answerDetail: OfferAnswerEventDetail = {
        connectionId: this.connectionId,
        sdp: this.pc.localDescription!.sdp
      };
      this.dispatchEvent(new CustomEvent('sendanswer', { detail: answerDetail }));

    } else {
      const connectionDetail: ConnectionEventDetail = { connectionId: this.connectionId };
      this.dispatchEvent(new CustomEvent('ongotanswer', { detail: connectionDetail }));

      this.assert_equals(this.pc.remoteDescription!.type, 'answer', 'Answer was set');
      this.assert_equals(this.pc.signalingState, 'stable', 'answered');
      this.pc.dispatchEvent(new Event('negotiated'));
    }
  }

  async onGotCandidate(connectionId: string, candidate: RTCIceCandidateInit): Promise<void> {
    if (this.connectionId !== connectionId || !this.pc) {
      return;
    }

    try {
      await this.pc.addIceCandidate(candidate);
    } catch (e) {
      if (this.pc && !this.ignoreOffer) {
        this.warn(`${this.pc} this candidate can't accept current signaling state ${this.pc.signalingState}.`);
      }
    }
  }
}