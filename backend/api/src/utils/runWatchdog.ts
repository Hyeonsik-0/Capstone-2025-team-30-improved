// 학습 서버 응답 없음 판정 (이슈 #21)
//
// 학습 서버는 학습 스레드가 도는 동안(일시정지 포함) 10초마다 생존 신호를 보낸다.
// BFF는 진행 중(RUNNING·PAUSED)인 실험의 마지막 신호가 60초보다 오래되면 FAILED로 바꾼다.
// - 신호가 한 번도 오지 않았으면 시작 시각을 기준으로 본다.
// - BFF가 재시작했거나 멈춰 있던 직후에는 판정하지 않는다. 그동안 신호를 받지 못했을 뿐이므로,
//   학습 서버가 다시 신호를 보낼 시간(기준 시간 하나)을 준다.
// - FAILED로 바꾼 뒤 신호가 다시 와도 되돌리지 않는다. 늦게 온 완료 신호는 지금처럼 반영된다.
import { RunModel } from '../models/Run';
import { logError, logInfo } from './apiHelpers';

export const HEARTBEAT_TIMEOUT_MS = 60_000;
export const CHECK_INTERVAL_MS = 10_000;
export const ACTIVE_STATUSES = ['RUNNING', 'PAUSED'];
export const STALE_RUN_ERROR = '학습 서버 응답 없음: 60초 동안 생존 신호가 없음';

/** 마지막 신호(없으면 시작 시각)가 기준 시간보다 오래된 진행 중 실험을 FAILED로 바꾼다. */
export const failStaleRuns = async (now: Date = new Date()): Promise<number> => {
  const cutoff = new Date(now.getTime() - HEARTBEAT_TIMEOUT_MS);
  const filter = {
    status: { $in: ACTIVE_STATUSES },
    $or: [
      { lastHeartbeatAt: { $lt: cutoff } },
      { lastHeartbeatAt: null, startTime: { $lt: cutoff } },
    ],
  };
  const stale = await RunModel.find(filter).select('_id').lean();
  if (stale.length === 0) return 0;

  // 조건을 다시 걸어 바꾼다. 찾은 뒤 신호가 도착한 실험은 바뀌지 않는다.
  const ids = stale.map((r) => r._id);
  const result = await RunModel.updateMany(
    { ...filter, _id: { $in: ids } },
    { $set: { status: 'FAILED', testStatus: 'INVALID', endTime: now, error: STALE_RUN_ERROR } }
  );
  if (result.modifiedCount > 0) {
    logInfo('Run Watchdog', `Marked ${result.modifiedCount} unresponsive runs as FAILED`, {
      runIds: ids.map(String),
      timeoutMs: HEARTBEAT_TIMEOUT_MS,
    });
  }
  return result.modifiedCount;
};

/** 판정을 주기적으로 실행한다. 서버가 뜬 직후와 멈췄다 돌아온 직후에는 기준 시간 동안 건너뛴다. */
export const startRunWatchdog = (): NodeJS.Timeout => {
  let graceUntil = Date.now() + HEARTBEAT_TIMEOUT_MS;
  let lastTick = Date.now();
  let checking = false;

  return setInterval(async () => {
    const now = Date.now();
    // 타이머가 크게 늦었다면 프로세스가 멈춰 있었던 것이다. 그동안 신호를 받지 못했으므로 다시 유예한다.
    if (now - lastTick > CHECK_INTERVAL_MS * 3) {
      graceUntil = now + HEARTBEAT_TIMEOUT_MS;
    }
    lastTick = now;
    if (now < graceUntil || checking) return;

    checking = true;
    try {
      await failStaleRuns(new Date(now));
    } catch (error) {
      logError('Run Watchdog', error, { message: 'Failed to check unresponsive runs' });
    } finally {
      checking = false;
    }
  }, CHECK_INTERVAL_MS);
};
