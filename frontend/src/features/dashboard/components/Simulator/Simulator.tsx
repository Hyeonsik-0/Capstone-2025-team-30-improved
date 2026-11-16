import { VideoPlayer } from '@/core/streaming/videoplayer';
import { RenderStreaming } from '@/core/webrtc/renderstreaming';
import { Signaling, WebSocketSignaling } from '@/core/webrtc/signaling';
import { getRTCConfiguration } from '@/core/config/config';
import { useEffect, useRef, useState } from 'react';
import { useExperimentStore } from '@/store/experimentStore';
import { useServerConfig } from '@/shared/hooks';
import { useSimulationSpeed } from '../../hooks/useSimulationSpeed';
import { WebRTCLogger, UILogger } from '@/shared/utils/logger';
import FeedbackArea from './components/FeedbackArea';
import {
  SimulatorContainer,
  VideoContainer,
  ControlsContainer,
  ControlGroup,
  SpeedControl,
  SpeedLabel,
  SpeedSlider,
  SpeedLabels,
  SpeedValue,
  /*MouseLockControl,*/
  StatusIndicator,
  ActionButton
} from '@/shared/components/styles';
import { FlatCard } from '@/shared/components/ui/Card';

/**
 * 영상(WebRTC) 연결 재시도 (이슈 #18)
 * - 연결 요청 후 CONNECT_TIMEOUT_MS 안에 영상 트랙과 ICE 연결이 모두 오지 않으면 실패로 본다.
 * - 시그널링 서버의 disconnect, ICE failed·disconnected 5초 지속(peer.ts), 시그널링 WebSocket 끊김도 실패다.
 * - 실패하면 정리하고, 학습 중(진행·일시정지)에는 횟수 제한 없이 1·2·4·8·16초, 이후 30초 간격으로 다시 연결한다.
 * - 연결 시작 조건은 그대로다: 이 화면에서 새 실험을 시작했을 때(자동), 재연결 버튼(즉시).
 */
const CONNECT_TIMEOUT_MS = 15000;
const RETRY_BASE_MS = 1000;
const RETRY_MAX_MS = 30000;

type ConnectionPhase = 'idle' | 'connecting' | 'connected' | 'waiting';

/**
 * 시뮬레이터 컴포넌트
 * 실험 실행 중 Unity 시뮬레이터의 비디오 스트림을 표시하고 제어
 * WebRTC를 통한 실시간 스트리밍과 피드백 기능을 제공
 */
export default function Simulator() {
  // WebRTC 관련 참조
  const renderstreamingRef = useRef<RenderStreaming | null>(null);
  const videoPlayerRef = useRef<VideoPlayer>(null);
  
  // DOM 참조
  const playerDivRef = useRef<HTMLDivElement>(null);
  const lockMouseCheckRef = useRef<HTMLInputElement>(null);
  
  // 상태 관리
  const [phase, setPhase] = useState<ConnectionPhase>('idle');
  const [retry, setRetry] = useState<{ failures: number; delaySec: number } | null>(null);
  const prevTrainingStatusRef = useRef<string | null>(null);
  const isConnected = phase === 'connected';

  // 연결 관리 (이슈 #18)
  const wantRef = useRef(false); // 연결을 유지하려는 중인가 (자동 시작·재연결 버튼 뒤 true, 학습 종료·언마운트 시 false)
  const genRef = useRef(0); // 연결 시도마다 증가. 지난 시도에서 늦게 온 이벤트를 무시한다.
  const failuresRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const connectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const progressRef = useRef({ track: false, ice: false });
  
  // 실험 관련 상태
  const trainingStatus = useExperimentStore(state => state.trainingStatus);
  const canConnect = useExperimentStore(state => state.canConnect);
  const setCanConnect = useExperimentStore(state => state.setCanConnect);
  const speedLevel = useExperimentStore(state => state.trainingSpeedLevel);
  const algName = useExperimentStore(state => state.templateConfig?.algName);

  // 시뮬레이션 속도 조절
  const currentRunId = useExperimentStore(state => state.runId);
  const { handleSpeedChange, SPEED_SETTINGS } = useSimulationSpeed();
  


  // 서버 설정 훅 사용
  const { useWebSocket } = useServerConfig();

  /**
   * VideoPlayer 초기화
   * 컴포넌트 마운트 시 VideoPlayer 인스턴스를 생성
   */
  useEffect(() => {
    videoPlayerRef.current = new VideoPlayer();
    return () => {
      void stopConnecting();
    };
  }, []);


  /**
   * WebRTC 연결 자동 시작
   * 이 화면에서 새 실험을 시작해 canConnect가 true가 되면, 실험이 진행 중일 때 2초 뒤 연결을 시작한다.
   * 이후 끊기면 재시도 규칙으로 다시 연결한다.
   */
  useEffect(() => {
    if (
      (trainingStatus === 'running' || trainingStatus === 'paused') &&
      useWebSocket !== null &&
      !wantRef.current &&
      playerDivRef.current &&
      videoPlayerRef.current &&
      canConnect === true
    ) {
      const connectionTimeoutId = setTimeout(() => {
        startConnecting();
      }, 2000);
      return () => clearTimeout(connectionTimeoutId);
    }
  }, [trainingStatus, useWebSocket, canConnect]);

  /**
   * 훈련 상태 변화 감지
   * 훈련이 종료되면 WebRTC 연결을 정리하고 재시도도 멈춘다
   */
  useEffect(() => {
    const previousStatus = prevTrainingStatusRef.current;
    if ((previousStatus === 'running' || previousStatus === 'paused') && 
        !(trainingStatus === 'running' || trainingStatus === 'paused')) {
      void stopConnecting();
      setCanConnect(false);
    }
    prevTrainingStatusRef.current = trainingStatus ?? null;
  }, [trainingStatus]);

  /**
   * 연결 유지 시작 (자동 시작·재연결 버튼). 재시도 간격을 처음으로 되돌리고 바로 연결한다.
   */
  function startConnecting() {
    wantRef.current = true;
    failuresRef.current = 0;
    setRetry(null);
    void attempt();
  }

  /**
   * 연결 유지 중단 (학습 종료·언마운트)
   */
  async function stopConnecting() {
    wantRef.current = false;
    genRef.current += 1;
    if (retryTimerRef.current) {
      clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
    setRetry(null);
    setPhase('idle');
    await teardown();
  }

  /**
   * 현재 연결 정리 (VideoPlayer와 RenderStreaming)
   */
  async function teardown() {
    if (connectTimerRef.current) {
      clearTimeout(connectTimerRef.current);
      connectTimerRef.current = null;
    }
    if (videoPlayerRef.current) {
      videoPlayerRef.current.deletePlayer();
    }
    const renderStreaming = renderstreamingRef.current;
    renderstreamingRef.current = null;
    if (renderStreaming) {
      try {
        await renderStreaming.stop();
      } catch (error) {
        WebRTCLogger.error(`RenderStreaming 정지 실패: ${error}`);
      }
    }
  }

  /**
   * 연결 시도 한 번
   * VideoPlayer를 만들고 RenderStreaming으로 연결을 요청한다.
   */
  async function attempt() {
    const gen = ++genRef.current;
    if (retryTimerRef.current) {
      clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
    await teardown();
    if (gen !== genRef.current || !wantRef.current || !playerDivRef.current || !videoPlayerRef.current) {
      return;
    }
    setPhase('connecting');
    progressRef.current = { track: false, ice: false };

    const lockMouseElement = lockMouseCheckRef.current || document.createElement('input');
    if (!lockMouseCheckRef.current) {
      lockMouseElement.type = 'checkbox';
      lockMouseElement.checked = false;
    }
    videoPlayerRef.current.createPlayer(playerDivRef.current, lockMouseElement as HTMLInputElement);

    const signaling = useWebSocket ? new WebSocketSignaling() : new Signaling();
    const config = getRTCConfiguration();
    const renderStreaming = new RenderStreaming(signaling, config);
    renderstreamingRef.current = renderStreaming;

    renderStreaming.onConnect = () => {
      if (gen === genRef.current) onConnect();
    };
    renderStreaming.onDisconnect = (reason: string) => fail(gen, reason);
    renderStreaming.onTrackEvent = ({ track }) => {
      if (gen !== genRef.current) return;
      if (videoPlayerRef.current) {
        videoPlayerRef.current.addTrack(track);
      }
      progressRef.current.track = true;
      markConnected(gen);
    };
    renderStreaming.onIceConnected = () => {
      if (gen !== genRef.current) return;
      progressRef.current.ice = true;
      markConnected(gen);
    };

    connectTimerRef.current = setTimeout(() => {
      fail(gen, `${CONNECT_TIMEOUT_MS / 1000}초 안에 영상이 연결되지 않음`);
    }, CONNECT_TIMEOUT_MS);

    try {
      await renderStreaming.start();
      await renderStreaming.createConnection();
    } catch (error) {
      fail(gen, `연결 시작 실패: ${error}`);
    }
  }

  /**
   * 영상 트랙을 받고 ICE가 연결되면 '연결됨'으로 표시하고 재시도 간격을 처음으로 되돌린다.
   */
  function markConnected(gen: number) {
    if (gen !== genRef.current || !progressRef.current.track || !progressRef.current.ice) return;
    if (connectTimerRef.current) {
      clearTimeout(connectTimerRef.current);
      connectTimerRef.current = null;
    }
    failuresRef.current = 0;
    setRetry(null);
    setPhase('connected');
  }

  /**
   * 연결 실패·끊김: 정리하고 간격을 두고 다시 연결한다.
   */
  function fail(gen: number, reason: string) {
    if (gen !== genRef.current || !wantRef.current) return;
    genRef.current += 1; // 이 시도에서 늦게 오는 이벤트는 무시한다
    const delay = Math.min(RETRY_BASE_MS * 2 ** failuresRef.current, RETRY_MAX_MS);
    failuresRef.current += 1;
    WebRTCLogger.error(`영상 연결 실패(${failuresRef.current}회): ${reason}. ${delay}ms 뒤 다시 연결`);
    setRetry({ failures: failuresRef.current, delaySec: delay / 1000 });
    setPhase('waiting');
    void teardown();
    retryTimerRef.current = setTimeout(() => {
      void attempt();
    }, delay);
  }

  /**
   * WebRTC 연결 성공 핸들러
   * 데이터 채널을 생성하고 입력을 설정
   */
  function onConnect() {
    if (renderstreamingRef.current && videoPlayerRef.current) {
      const dataChannel = renderstreamingRef.current.createDataChannel('input');
      videoPlayerRef.current.setupInput(dataChannel);
    }
  }

  /**
   * 재연결 핸들러
   * 기다리지 않고 바로 다시 연결한다. 재시도 간격도 처음으로 되돌린다.
   */
  const handleReconnect = () => {
    UILogger.action('WebRTC 재연결 시도');
    startConnecting();
  };

  const statusText =
    phase === 'connected' ? '연결됨'
    : phase === 'connecting' ? '연결 중'
    : phase === 'waiting' && retry ? `연결 중 (실패 ${retry.failures}회, ${retry.delaySec}초 뒤 재시도)`
    : '연결 안됨';

  const currentSpeed = SPEED_SETTINGS[speedLevel as keyof typeof SPEED_SETTINGS];

  return (
    <FlatCard style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <SimulatorContainer>
        <VideoContainer ref={playerDivRef} />
        
        <ControlsContainer>
          <ControlGroup>
            <SpeedControl>
              <SpeedLabel>속도</SpeedLabel>
              <div>
                <SpeedSlider
                  min="1"
                  max="4"
                  step="1"
                  value={speedLevel}
                  onChange={(e) => handleSpeedChange(currentRunId, parseInt(e.target.value))}
                  disabled={!isConnected}
                />
                <SpeedLabels>
                  <span>느림</span>
                  <span>보통</span>
                  <span>빠름</span>
                  <span>매우빠름</span>
                </SpeedLabels>
              </div>
              <SpeedValue>{currentSpeed.label}</SpeedValue>
            </SpeedControl>
          </ControlGroup>

          <ControlGroup>
            {(trainingStatus === 'running' || trainingStatus === 'paused') && (
              <ActionButton 
                onClick={handleReconnect}
              >
                재연결
              </ActionButton>
            )}
            {/*<MouseLockControl>
              <input
                type="checkbox"
                ref={lockMouseCheckRef}
              />
              마우스 잠금
            </MouseLockControl>*/}
            
            <StatusIndicator $connected={isConnected}>
              {statusText}
            </StatusIndicator>
          </ControlGroup>
        </ControlsContainer>
      </SimulatorContainer>
        {algName === 'hf-llm' && (
          <FeedbackArea />
        )}
    </FlatCard>
  );
}