import * as Logger from "./logger";
import { WebSocketLogger } from "./logger";
import { getWebSocketUrl } from "@/shared/utils/apiUrl";

// 시그널링 메시지 인터페이스들
interface SignalingMessage {
  type: string;
  connectionId?: string;
  sdp?: string;
  candidate?: string;
  sdpMid?: string;
  sdpMLineIndex?: number;
  polite?: boolean;
}

interface SessionResponse {
  sessionId: string;
}

interface MessagesResponse {
  datetime?: number;
  messages: SignalingMessage[];
}

interface ConnectionResponse {
  connectionId: string;
  polite: boolean;
}

interface WebSocketMessage {
  type: string;
  from?: string;
  connectionId?: string;
  data?: {
    sdp?: string;
    candidate?: string;
    sdpMid?: string;
    sdpMLineIndex?: number;
    polite?: boolean;
  };
}

export class Signaling extends EventTarget {
  private running: boolean = false;
  private interval: number;
  private sessionId?: string;
  private sleep: (msec: number) => Promise<void>;

  constructor(interval: number = 1000) {
    super();
    this.interval = interval;
    this.sleep = (msec: number) => new Promise(resolve => setTimeout(resolve, msec));
  }

  private headers(): Record<string, string> {
    if (this.sessionId !== undefined) {
      return { 'Content-Type': 'application/json', 'Session-Id': this.sessionId };
    }
    else {
      return { 'Content-Type': 'application/json' };
    }
  }

  private url(method?: string, parameter: string = ''): string {
    let ret = location.origin + '/signaling';
    if(method)
      ret += '/' + method;
    if(parameter)
      ret += '?' + parameter;
    return ret;
  }

  async start(): Promise<void> {
    if (this.running) {
      return;
    }

    this.running = true;
    while (!this.sessionId) {
      const createResponse = await fetch(this.url(''), { method: 'PUT', headers: this.headers() });
      const session: SessionResponse = await createResponse.json();
      this.sessionId = session.sessionId;

      if (!this.sessionId) {
        await this.sleep(this.interval);
      }
    }

    this.loopGetAll();
  }

  private async loopGetAll(): Promise<void> {
    let lastTimeRequest = Date.now() - 30000;
    while (this.running) {
      const res = await this.getAll(lastTimeRequest);
      const data: MessagesResponse = await res.json();
      lastTimeRequest = data.datetime ? data.datetime : Date.now();

      const messages = data.messages;

      for (const msg of messages) {
        switch (msg.type) {
          case "connect":
            break;
          case "disconnect":
            this.dispatchEvent(new CustomEvent('disconnect', { detail: msg }));
            break;
          case "offer":
            this.dispatchEvent(new CustomEvent('offer', { detail: msg }));
            break;
          case "answer":
            this.dispatchEvent(new CustomEvent('answer', { detail: msg }));
            break;
          case "candidate":
            this.dispatchEvent(new CustomEvent('candidate', { detail: msg }));
            break;
          default:
            break;
        }
      }
      await this.sleep(this.interval);
    }
  }

  async stop(): Promise<void> {
    this.running = false;
    await fetch(this.url(''), { method: 'DELETE', headers: this.headers() });
    this.sessionId = undefined;
  }

  async createConnection(connectionId: string): Promise<void> {
    const data = { 'connectionId': connectionId };
    const res = await fetch(this.url('connection'), { method: 'PUT', headers: this.headers(), body: JSON.stringify(data) });
    const json: ConnectionResponse = await res.json();
    Logger.log(`Signaling: HTTP create connection, connectionId: ${json.connectionId}, polite:${json.polite}`);

    this.dispatchEvent(new CustomEvent('connect', { detail: json }));
  }

  async deleteConnection(connectionId: string): Promise<void> {
    const data = { 'connectionId': connectionId };
    const res = await fetch(this.url('connection'), { method: 'DELETE', headers: this.headers(), body: JSON.stringify(data) });
    const json = await res.json();
    this.dispatchEvent(new CustomEvent('disconnect', { detail: json }));
  }

  async sendOffer(connectionId: string, sdp: string): Promise<void> {
    const data = { 'sdp': sdp, 'connectionId': connectionId };
    Logger.log('sendOffer:' + JSON.stringify(data));
    await fetch(this.url('offer'), { method: 'POST', headers: this.headers(), body: JSON.stringify(data) });
  }

  async sendAnswer(connectionId: string, sdp: string): Promise<void> {
    const data = { 'sdp': sdp, 'connectionId': connectionId };
    Logger.log('sendAnswer:' + JSON.stringify(data));
    await fetch(this.url('answer'), { method: 'POST', headers: this.headers(), body: JSON.stringify(data) });
  }

  async sendCandidate(connectionId: string, candidate: string, sdpMid: string, sdpMLineIndex: number): Promise<void> {
    const data = {
      'candidate': candidate,
      'sdpMLineIndex': sdpMLineIndex,
      'sdpMid': sdpMid,
      'connectionId': connectionId
    };
    Logger.log('sendCandidate:' + JSON.stringify(data));
    await fetch(this.url('candidate'), { method: 'POST', headers: this.headers(), body: JSON.stringify(data) });
  }

  private async getAll(fromTime: number = 0): Promise<Response> {
    return await fetch(this.url(``, `fromtime=${fromTime}`), { method: 'GET', headers: this.headers() });
  }
}

export class WebSocketSignaling extends EventTarget {
  private sleep: (msec: number) => Promise<void>;
  private websocket: WebSocket;
  private isWsOpen: boolean = false;

  constructor() {
    super();
    this.sleep = (msec: number) => new Promise<void>(resolve => setTimeout(resolve, msec));

    const websocketUrl = getWebSocketUrl();
    WebSocketLogger.connection(`연결 시도: ${websocketUrl}`);
    this.websocket = new WebSocket(websocketUrl);

    this.websocket.onopen = () => {
      WebSocketLogger.connection('연결 성공');
      this.isWsOpen = true;
    };

    this.websocket.onclose = (event) => {
      WebSocketLogger.error(`연결 끊어짐: code=${event.code}, reason=${event.reason}, wasClean=${event.wasClean}`);
      this.isWsOpen = false;
      this.dispatchEvent(new Event('close')); // 연결 관리 쪽에서 실패로 처리한다 (이슈 #18)
    };

    this.websocket.onmessage = (event: MessageEvent) => {
      WebSocketLogger.message(`메시지 수신: ${event.data}`);
      const msg: WebSocketMessage = JSON.parse(event.data);
      if (!msg || !this) {
        return;
      }

      Logger.log(msg);

      switch (msg.type) {
        case "connect":
          WebSocketLogger.event('connect 이벤트');
          this.dispatchEvent(new CustomEvent('connect', { detail: msg }));
          break;
        case "disconnect":
          WebSocketLogger.event('disconnect 이벤트');
          this.dispatchEvent(new CustomEvent('disconnect', { detail: msg }));
          break;
        case "offer":
          WebSocketLogger.event('offer 이벤트');
          this.dispatchEvent(new CustomEvent('offer', { 
            detail: { 
              connectionId: msg.from, 
              sdp: msg.data?.sdp, 
              polite: msg.data?.polite 
            } 
          }));
          break;
        case "answer":
          WebSocketLogger.event('answer 이벤트');
          this.dispatchEvent(new CustomEvent('answer', { 
            detail: { 
              connectionId: msg.from, 
              sdp: msg.data?.sdp 
            } 
          }));
          break;
        case "candidate":
          WebSocketLogger.event('candidate 이벤트');
          this.dispatchEvent(new CustomEvent('candidate', { 
            detail: { 
              connectionId: msg.from, 
              candidate: msg.data?.candidate, 
              sdpMLineIndex: msg.data?.sdpMLineIndex, 
              sdpMid: msg.data?.sdpMid 
            } 
          }));
          break;
        default:
          WebSocketLogger.error(`알 수 없는 메시지 타입: ${msg.type}`);
          break;
      }
    };
  }

  async start(): Promise<void> {
    while (!this.isWsOpen) {
      // 열리기 전에 닫히면 끝없이 기다리지 않고 실패한다. (이슈 #18)
      if (this.websocket.readyState === WebSocket.CLOSED) {
        throw new Error('시그널링 WebSocket이 열리기 전에 닫힘');
      }
      await this.sleep(100);
    }
  }

  async stop(): Promise<void> {
    WebSocketLogger.connection('연결 종료');
    this.websocket.close();
    while (this.isWsOpen) {
      await this.sleep(100);
    }
  }

  // Promise<void>로 변경
  async createConnection(connectionId: string): Promise<void> {
    const sendJson = JSON.stringify({ type: "connect", connectionId: connectionId });
    WebSocketLogger.message(`createConnection 전송: ${sendJson}`);
    Logger.log(sendJson);
    this.websocket.send(sendJson);
  }

  // Promise<void>로 변경
  async deleteConnection(connectionId: string): Promise<void> {
    const sendJson = JSON.stringify({ type: "disconnect", connectionId: connectionId });
    WebSocketLogger.message(`deleteConnection 전송: ${sendJson}`);
    Logger.log(sendJson);
    this.websocket.send(sendJson);
  }

  sendOffer(connectionId: string, sdp: string): void {
    const data = { 'sdp': sdp, 'connectionId': connectionId };
    const sendJson = JSON.stringify({ type: "offer", from: connectionId, data: data });
    WebSocketLogger.message('sendOffer 전송');
    Logger.log(sendJson);
    this.websocket.send(sendJson);
  }

  sendAnswer(connectionId: string, sdp: string): void {
    const data = { 'sdp': sdp, 'connectionId': connectionId };
    const sendJson = JSON.stringify({ type: "answer", from: connectionId, data: data });
    WebSocketLogger.message('sendAnswer 전송');
    Logger.log(sendJson);
    this.websocket.send(sendJson);
  }

  sendCandidate(connectionId: string, candidate: string, sdpMLineIndex: number, sdpMid: string): void {
    const data = {
      'candidate': candidate,
      'sdpMLineIndex': sdpMLineIndex,
      'sdpMid': sdpMid,
      'connectionId': connectionId
    };
    const sendJson = JSON.stringify({ type: "candidate", from: connectionId, data: data });
    WebSocketLogger.message('sendCandidate 전송');
    Logger.log(sendJson);
    this.websocket.send(sendJson);
  }
}