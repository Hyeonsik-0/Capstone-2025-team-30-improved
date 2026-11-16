/**
 * WebRTC Render Streaming 모듈
 * Signaling 서버와의 통신을 담당
 * Peer-to-Peer 연결 설정
 * Offer/Answer/ICE Candidate 교환
 * Unity 비디오 스트림 수신
 * 데이터 채널 관리
 */

import Peer from "./peer";
import { WebRTCLogger } from "@/shared/utils";

// UUID 생성 함수
function uuid4(): string {
  const temp_url = URL.createObjectURL(new Blob());
  const uuid = temp_url.toString();
  URL.revokeObjectURL(temp_url);
  return uuid.split(/[:/]/g).pop()?.toLowerCase() || '';
}

// Signaling 인터페이스 정의
interface SignalingInterface extends EventTarget {
  createConnection(connectionId: string): Promise<void>;
  deleteConnection(connectionId: string): Promise<void>;
  sendOffer(connectionId: string, sdp: string): void | Promise<void>;
  sendAnswer(connectionId: string, sdp: string): void | Promise<void>;
  sendCandidate(connectionId: string, candidate: string, sdpMid: string | number, sdpMLineIndex: number | string): void | Promise<void>;
  start(): Promise<void>;
  stop(): Promise<void>;
}

// 이벤트 핸들러 타입 정의
type ConnectionHandler = (connectionId: string) => void;
type TrackEventHandler = (data: RTCTrackEvent) => void;
type DataChannelHandler = (data: RTCDataChannelEvent) => void;

// 커스텀 이벤트 detail 타입 정의
interface ConnectEventDetail {
  connectionId: string;
  polite: boolean;
}

interface DisconnectEventDetail {
  connectionId: string;
}

interface OfferEventDetail {
  connectionId: string;
  sdp: string;
  polite: boolean;
}

interface AnswerEventDetail {
  connectionId: string;
  sdp: string;
}

interface CandidateEventDetail {
  connectionId: string;
  candidate: string;
  sdpMid: string | null;
  sdpMLineIndex: number | null;
}

export class RenderStreaming {
  private _peer: Peer | null = null;
  private _connectionId: string | null = null;
  private _config: RTCConfiguration;
  private _signaling: SignalingInterface;

  // 공개 이벤트 핸들러
  public onConnect: ConnectionHandler;
  public onDisconnect: ConnectionHandler;
  public onGotOffer: ConnectionHandler;
  public onGotAnswer: ConnectionHandler;
  public onTrackEvent: TrackEventHandler;
  public onAddChannel: DataChannelHandler;
  /** 영상 경로(ICE)가 열렸을 때 (이슈 #18) */
  public onIceConnected: ConnectionHandler;

  constructor(signaling: SignalingInterface, config: RTCConfiguration) {
    // 기본 이벤트 핸들러 설정
    this.onConnect = (connectionId: string) => {
      WebRTCLogger.connection(`Connect peer on ${connectionId}`);
    };
    
    this.onDisconnect = (connectionId: string) => {
      WebRTCLogger.connection(`Disconnect peer on ${connectionId}`);
    };
    
    this.onGotOffer = (connectionId: string) => {
      WebRTCLogger.message(`On got Offer on ${connectionId}`);
    };
    
    this.onGotAnswer = (connectionId: string) => {
      WebRTCLogger.message(`On got Answer on ${connectionId}`);
    };
    
    this.onTrackEvent = (data: RTCTrackEvent) => {
      WebRTCLogger.event(`OnTrack event peer with data: ${data}`);
    };
    
    this.onAddChannel = (data: RTCDataChannelEvent) => {
      WebRTCLogger.event(`onAddChannel event peer with data: ${data}`);
    };

    this.onIceConnected = (connectionId: string) => {
      WebRTCLogger.connection(`ICE connected on ${connectionId}`);
    };

    this._config = config;
    this._signaling = signaling;

    // Signaling 이벤트 리스너 설정
    this._signaling.addEventListener('connect', (e: Event) => {
      this._onConnect(e as CustomEvent<ConnectEventDetail>);
    });
    this._signaling.addEventListener('disconnect', (e: Event) => {
      this._onDisconnect(e as CustomEvent<DisconnectEventDetail>);
    });
    this._signaling.addEventListener('offer', (e: Event) => {
      this._onOffer(e as CustomEvent<OfferEventDetail>);
    });
    this._signaling.addEventListener('answer', (e: Event) => {
      this._onAnswer(e as CustomEvent<AnswerEventDetail>);
    });
    this._signaling.addEventListener('candidate', (e: Event) => {
      this._onIceCandidate(e as CustomEvent<CandidateEventDetail>);
    });
    // 시그널링 WebSocket이 닫히면 연결이 끊긴 것으로 알린다. (이슈 #18)
    this._signaling.addEventListener('close', () => {
      this.onDisconnect('Signaling connection closed');
    });
  }

  private async _onConnect(e: CustomEvent<ConnectEventDetail>): Promise<void> {
    const data = e.detail;
    WebRTCLogger.connection(`RenderStreaming - _onConnect: connectionId=${data.connectionId}, polite=${data.polite}`);
    
    if (this._connectionId === data.connectionId) {
      WebRTCLogger.connection('RenderStreaming - 연결 ID 일치, PeerConnection 준비');
      this._preparePeerConnection(this._connectionId, data.polite);
      this.onConnect(data.connectionId);
    } else {
      WebRTCLogger.error(`RenderStreaming - 연결 ID 불일치: expected=${this._connectionId}, received=${data.connectionId}`);
    }
  }

  private async _onDisconnect(e: CustomEvent<DisconnectEventDetail>): Promise<void> {
    const data = e.detail;
    WebRTCLogger.connection(`RenderStreaming - _onDisconnect: connectionId=${data.connectionId}`);
    
    if (this._connectionId === data.connectionId) {
      WebRTCLogger.connection('RenderStreaming - 연결 ID 일치, 연결 해제');
      this.onDisconnect(data.connectionId);
      if (this._peer) {
        WebRTCLogger.connection('RenderStreaming - PeerConnection 종료');
        this._peer.close();
        this._peer = null;
      }
    } else {
      WebRTCLogger.error(`RenderStreaming - 연결 ID 불일치: expected=${this._connectionId}, received=${data.connectionId}`);
    }
  }

  private async _onOffer(e: CustomEvent<OfferEventDetail>): Promise<void> {
    const offer = e.detail;
    
    if (!this._peer) {
      this._preparePeerConnection(offer.connectionId, offer.polite);
    }

    const desc = new RTCSessionDescription({ 
      sdp: offer.sdp, 
      type: "offer" 
    });

    try {
      if (this._peer) {
        await this._peer.onGotDescription(offer.connectionId, desc);
      }
    } catch (error) {
      WebRTCLogger.error(
        `Error happen on GotDescription that description.\n` +
        `Message: ${error}\n` +
        `RTCSdpType: ${desc.type}\n` +
        `sdp: ${desc.sdp}`
      );
      return;
    }
  }

  private async _onAnswer(e: CustomEvent<AnswerEventDetail>): Promise<void> {
    const answer = e.detail;
    const desc = new RTCSessionDescription({ 
      sdp: answer.sdp, 
      type: "answer" 
    });

    if (this._peer) {
      try {
        await this._peer.onGotDescription(answer.connectionId, desc);
      } catch (error) {
        WebRTCLogger.error(
          `Error happen on GotDescription that description.\n` +
          `Message: ${error}\n` +
          `RTCSdpType: ${desc.type}\n` +
          `sdp: ${desc.sdp}`
        );
        return;
      }
    }
  }

  private async _onIceCandidate(e: CustomEvent<CandidateEventDetail>): Promise<void> {
    const candidate = e.detail;
    const iceCandidate = new RTCIceCandidate({ 
      candidate: candidate.candidate, 
      sdpMid: candidate.sdpMid, 
      sdpMLineIndex: candidate.sdpMLineIndex 
    });

    if (this._peer) {
      await this._peer.onGotCandidate(candidate.connectionId, iceCandidate);
    }
  }

  /**
   * WebRTC 연결 생성
   * @param connectionId - 연결 ID (없으면 UUID 자동 생성)
   */
  async createConnection(connectionId?: string): Promise<void> {
    this._connectionId = connectionId || uuid4();
    await this._signaling.createConnection(this._connectionId);
  }

  /**
   * WebRTC 연결 삭제
   */
  async deleteConnection(): Promise<void> {
    if (this._connectionId) {
      await this._signaling.deleteConnection(this._connectionId);
    }
  }

  /**
   * Peer 연결 준비 및 이벤트 핸들러 설정
   */
  private _preparePeerConnection(connectionId: string, polite: boolean): Peer {
    if (this._peer) {
      WebRTCLogger.connection('Close current PeerConnection');
      this._peer.close();
      this._peer = null;
    }

    // 새로운 Peer 생성
    this._peer = new Peer(connectionId, polite, this._config);

    // Peer 이벤트 핸들러 설정
    this._peer.addEventListener('disconnect', () => {
      this.onDisconnect(`Receive disconnect message from peer. connectionId:${connectionId}`);
    });

    this._peer.addEventListener('iceconnected', () => {
      this.onIceConnected(connectionId);
    });

    this._peer.addEventListener('trackevent', (e: Event) => {
      const customEvent = e as CustomEvent;
      const data = customEvent.detail as RTCTrackEvent;
      this.onTrackEvent(data);
    });

    this._peer.addEventListener('adddatachannel', (e: Event) => {
      const customEvent = e as CustomEvent;
      const data = customEvent.detail as RTCDataChannelEvent;
      this.onAddChannel(data);
    });

    this._peer.addEventListener('ongotoffer', (e: Event) => {
      const customEvent = e as CustomEvent;
      const id = customEvent.detail.connectionId as string;
      this.onGotOffer(id);
    });

    this._peer.addEventListener('ongotanswer', (e: Event) => {
      const customEvent = e as CustomEvent;
      const id = customEvent.detail.connectionId as string;
      this.onGotAnswer(id);
    });

    this._peer.addEventListener('sendoffer', (e: Event) => {
      const customEvent = e as CustomEvent;
      const offer = customEvent.detail as { connectionId: string; sdp: string };
      this._signaling.sendOffer(offer.connectionId, offer.sdp);
    });

    this._peer.addEventListener('sendanswer', (e: Event) => {
      const customEvent = e as CustomEvent;
      const answer = customEvent.detail as { connectionId: string; sdp: string };
      this._signaling.sendAnswer(answer.connectionId, answer.sdp);
    });

    this._peer.addEventListener('sendcandidate', (e: Event) => {
      const customEvent = e as CustomEvent;
      const candidate = customEvent.detail as {
        connectionId: string;
        candidate: string;
        sdpMid: string | null;
        sdpMLineIndex: number | null;
      };
      
      this._signaling.sendCandidate(
        candidate.connectionId,
        candidate.candidate,
        candidate.sdpMid || '',
        candidate.sdpMLineIndex || 0
      );
    });

    return this._peer;
  }

  /**
   * WebRTC 통계 정보 가져오기
   */
  async getStats(): Promise<RTCStatsReport | null> {
    if (!this._peer || !this._connectionId) {
      return null;
    }
    return await this._peer.getStats(this._connectionId);
  }

  /**
   * 데이터 채널 생성 (Unity와 바이너리 통신용)
   */
  createDataChannel(label: string): RTCDataChannel | null {
    if (!this._peer || !this._connectionId) {
      return null;
    }
    return this._peer.createDataChannel(this._connectionId, label);
  }

  /**
   * 미디어 트랙 추가 (비디오 스트림 수신용)
   */
  addTrack(track: MediaStreamTrack): RTCRtpSender | null {
    if (!this._peer || !this._connectionId) {
      return null;
    }
    return this._peer.addTrack(this._connectionId, track);
  }

  /**
   * 트랜시버 추가 (송수신 설정)
   */
  addTransceiver(
    trackOrKind: MediaStreamTrack | string,
    init?: RTCRtpTransceiverInit
  ): RTCRtpTransceiver | null {
    if (!this._peer || !this._connectionId) {
      return null;
    }
    return this._peer.addTransceiver(this._connectionId, trackOrKind, init);
  }

  /**
   * 모든 트랜시버 가져오기
   */
  getTransceivers(): RTCRtpTransceiver[] | null {
    if (!this._peer || !this._connectionId) {
      return null;
    }
    return this._peer.getTransceivers(this._connectionId);
  }

  /**
   * 스트리밍 시작
   */
  async start(): Promise<void> {
    await this._signaling.start();
  }

  /**
   * 스트리밍 중지 및 정리
   */
  async stop(): Promise<void> {
    if (this._peer) {
      this._peer.close();
      this._peer = null;
    }

    if (this._signaling) {
      await this._signaling.stop();
    }
  }
}