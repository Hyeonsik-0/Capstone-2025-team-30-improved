import { useEffect } from 'react';
import { useExperimentStore, toChartPoint } from '@/store/experimentStore';
import { buildApiUrl } from '@/shared/utils/apiUrl';
import { trainingMetricsApi } from '@/shared/api';
import { SSELogger } from '@/core/webrtc/logger';

/**
 * 실시간 지표 SSE 연결 관리 (이슈 #10)
 *
 * - 연결될 때마다(서버의 connected 이벤트) 차트의 마지막 스텝 이후 지표를 DB에서 받아 스텝 기준으로 병합한다.
 *   서버는 클라이언트를 등록한 뒤 connected를 보내므로, 그전에 저장된 지표는 DB 조회에, 그 뒤 지표는 SSE에 들어온다.
 *   둘이 겹쳐도 병합에서 한 번만 남는다.
 * - 오류가 나면 브라우저 자동 재연결에 맡기지 않고 닫은 뒤 직접 다시 연결한다.
 *   학습 중에는 횟수 제한 없이, 1·2·4·8·16초 다음부터는 30초 간격으로 시도하고, 채우기까지 성공하면 간격을 처음으로 되돌린다.
 * - 서버 하트비트(30초)를 포함해 65초 동안 아무 신호가 없으면 죽은 연결로 보고 다시 연결한다.
 * - 학습 중이 아니게 되면(일시정지·완료·중지·실패) SSE를 닫고 DB에서 한 번 더 채운다.
 *   재연결을 기다리는 사이에 학습이 끝나도 빠진 구간이 남지 않게 하기 위해서다.
 */
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
const WATCHDOG_MS = 65000;
const FINAL_FILL_STATUSES = ['paused', 'completed', 'stopped', 'failed'];

/** 연속 실패 횟수에 따른 재연결 대기 시간: 1, 2, 4, 8, 16초, 이후 30초 */
function reconnectDelay(failures: number): number {
  return Math.min(RECONNECT_BASE_MS * 2 ** failures, RECONNECT_MAX_MS);
}

/** 차트의 마지막 스텝 이후 지표를 DB에서 받아 병합한다. 그사이 다른 실험으로 바뀌었으면 버린다. */
async function fillFromDb(runId: string): Promise<number> {
  const current = useExperimentStore.getState().trainingMetrics;
  const after = current.reduce((max, point) => Math.max(max, point.timesteps), 0);
  const response = await trainingMetricsApi.getRunMetrics(runId, after);
  const metrics = response.data?.metrics ?? [];
  const store = useExperimentStore.getState();
  if (store.runId === runId) {
    store.mergeTrainingMetrics(metrics.map((m) => toChartPoint(m.timesteps, m.chartMetrics)));
  }
  return metrics.length;
}

export default function StreamingManager() {
  const runId = useExperimentStore((s) => s.runId);
  const status = useExperimentStore((s) => s.trainingStatus);

  useEffect(() => {
    if (!runId) return;

    if (status !== 'running') {
      if (FINAL_FILL_STATUSES.includes(String(status).toLowerCase())) {
        fillFromDb(runId)
          .then((n) => SSELogger.connection(`학습 중 아님(${status}): DB에서 ${n}개 채움`))
          .catch((error) => SSELogger.error(`학습 종료 후 채우기 실패: ${error}`));
      }
      return;
    }

    let disposed = false;
    let source: EventSource | null = null;
    let generation = 0; // 연결마다 증가. 이전 연결의 이벤트·응답을 무시하는 데 쓴다.
    let failures = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let watchdogTimer: ReturnType<typeof setTimeout> | null = null;

    const clearWatchdog = () => {
      if (watchdogTimer) clearTimeout(watchdogTimer);
      watchdogTimer = null;
    };

    const armWatchdog = (gen: number) => {
      clearWatchdog();
      watchdogTimer = setTimeout(() => {
        SSELogger.error(`${WATCHDOG_MS / 1000}초 동안 신호 없음: 연결을 닫고 다시 연결`);
        fail(gen);
      }, WATCHDOG_MS);
    };

    // 연결 실패·끊김·무응답: 닫고 간격을 두고 새로 연결한다.
    const fail = (gen: number) => {
      if (disposed || gen !== generation) return;
      generation++;
      source?.close();
      source = null;
      clearWatchdog();
      const delay = reconnectDelay(failures);
      failures++;
      SSELogger.connection(`${delay}ms 후 재연결 (연속 실패 ${failures}회)`);
      reconnectTimer = setTimeout(connect, delay);
    };

    const fill = (gen: number) => {
      fillFromDb(runId)
        .then((n) => {
          SSELogger.connection(`연결됨: DB에서 ${n}개 채움`);
          if (gen === generation) failures = 0;
        })
        .catch((error) => {
          // 채우지 못했으면 다시 연결해서 다시 채운다.
          SSELogger.error(`채우기 실패: ${error}`);
          fail(gen);
        });
    };

    const connect = () => {
      if (disposed) return;
      reconnectTimer = null;
      const gen = ++generation;
      const es = new EventSource(buildApiUrl(`/api/training-metrics/stream/${runId}`));
      source = es;
      SSELogger.connection(`연결 시도: runId=${runId}`);
      armWatchdog(gen); // 연결 시도가 응답 없이 멈춰 있는 경우도 잡는다

      es.onopen = () => {
        if (gen === generation) armWatchdog(gen);
      };

      es.onmessage = (e) => {
        if (gen !== generation) return;
        armWatchdog(gen);
        let data;
        try {
          data = JSON.parse(e.data);
        } catch (error) {
          SSELogger.error(`데이터 파싱 오류: ${error}`);
          return;
        }
        if (data.type === 'connected') {
          fill(gen);
          return;
        }
        if (data.type === 'ping') return;
        if (typeof data.timesteps === 'number' && data.chartMetrics) {
          useExperimentStore.getState().appendTrainingMetric(data);
        } else {
          SSELogger.error(`잘못된 데이터 형식: ${String(e.data).substring(0, 50)}...`);
        }
      };

      es.onerror = () => {
        if (gen !== generation) return;
        SSELogger.error(`연결 오류 (readyState=${es.readyState})`);
        fail(gen);
      };
    };

    connect();

    return () => {
      disposed = true;
      generation++;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      clearWatchdog();
      source?.close();
      source = null;
    };
  }, [runId, status]);

  return null;
}
