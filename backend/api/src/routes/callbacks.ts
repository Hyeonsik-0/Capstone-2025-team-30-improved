import { Router, Request, Response } from "express";
import { RunModel } from "../models/Run";
import { TrainingMetricModel } from "../models/TrainingMetric";
import { 
  validateId, 
  sendErrorResponse, 
  sendSuccessResponse, 
  asyncHandler, 
  logError, 
  logInfo 
} from "../utils/apiHelpers";
import { ACTIVE_STATUSES } from "../utils/runWatchdog";

const router = Router();
const FASTAPI_BASE = process.env.FASTAPI_BASE || 'http://localhost:8000';

/**
 * 실험 완료 콜백 핸들러
 * POST /callbacks/:id/experiment-completed
 */
const experimentCompletedHandler = async (req: Request, res: Response): Promise<void> => {
  const runId = validateId(req, res);
  if (!runId) return;

  try {
    const run = await RunModel.findById(runId).lean();

    if (!run) {
      logError("Experiment Completed", "Run not found", { runId });
      sendErrorResponse(res, 404, "Run not found");
      return;
    }

    // FastAPI에 모델 및 로그 파일 존재 여부 확인
    let modelPath = null;
    let logPath = null;

    try {
      const response = await fetch(`${FASTAPI_BASE}/api/artifacts/${run.runName}/check-all`, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        }
      });

      if (response.ok) {
        const artifactResult = await response.json();
        
        // 모델 파일 존재 여부 확인
        if (artifactResult.model && artifactResult.model.exists) {
          modelPath = artifactResult.model.file_name;
        }
        
        // 로그 파일 존재 여부 확인
        if (artifactResult.log && artifactResult.log.exists) {
          logPath = artifactResult.log.file_name;
        }
      }
    } catch (fastApiError) {
      logError("Experiment Completed - File Check Failed", fastApiError, { runId, runName: run.runName });
      // 파일 확인 실패시도 실험은 완료로 처리
    }

    // 진행률 확인해서 상태 결정 (30% 이상이면 COMPLETED, 아니면 STOPPED)
    let finalStatus = "STOPPED"; // 기본값
    
    try {
      // 해당 run의 최대 timesteps 확인
      const maxMetric = await TrainingMetricModel.findOne({ runId: run._id })
        .sort({ timesteps: -1 })
        .select('timesteps')
        .lean();
      
      if (maxMetric && run.totalSteps) {
        const progressRatio = maxMetric.timesteps / run.totalSteps;
        if (progressRatio >= 0.3) {
          finalStatus = "COMPLETED";
        }
        logInfo("Progress Check", `Training progress: ${(progressRatio * 100).toFixed(1)}%`, { 
          runId, 
          currentSteps: maxMetric.timesteps,
          totalSteps: run.totalSteps,
          finalStatus
        });
      }
    } catch (progressError) {
      logError("Progress Check Failed", progressError, { runId });
    }

    // 실험 상태 업데이트 및 artifacts 정보 저장
    const updateData: any = {
      status: finalStatus,
      testStatus: "IDLE",
      endTime: new Date()
    };

    if (modelPath) {
      updateData['artifacts.modelZip'] = modelPath;
    }
    if (logPath) {
      updateData['artifacts.logsCsv'] = logPath;
    }

    await RunModel.updateOne(
      { _id: run._id },
      { $set: updateData }
    );

    logInfo("Experiment Completed", `Experiment finished with status: ${finalStatus}`, { 
      runId, 
      runName: run.runName,
      status: finalStatus,
      modelPath,
      logPath
    });
    
    sendSuccessResponse(res, { 
      runId, 
      status: finalStatus,
      artifacts: {
        modelZip: modelPath,
        logsCsv: logPath
      }
    }, `Experiment finished with status: ${finalStatus}`);
  } catch (error) {
    logError("Experiment Completed", error, { runId });
    sendErrorResponse(res, 500, "Failed to handle experiment completion");
  }
};

/**
 * 테스트 완료 콜백 핸들러
 * POST /callbacks/:id/test-completed
 */
export const testCompletedHandler = async (req: Request, res: Response): Promise<void> => {
  const runId = validateId(req, res);
  if (!runId) return;

  try {
    const run = await RunModel.findById(runId).lean();

    if (!run) {
      logError("Test Completed", "Run not found", { runId });
      sendErrorResponse(res, 404, "Run not found");
      return;
    }

    // 먼저 상태를 COMPLETED로 업데이트
    await RunModel.updateOne(
      { _id: run._id },
      { $set: { testStatus: "COMPLETED" } }
    );

    logInfo("Test Completed", "Test completed successfully", { runId, runName: run.runName });

    // 5초 후 IDLE로 변경
    setTimeout(async () => {
      try {
        await RunModel.updateOne(
          { _id: run._id },
          { $set: { testStatus: "IDLE" } }
        );
        logInfo("Test Status Reset", "Test status reset to IDLE after 5 seconds", { runId });
      } catch (error) {
        logError("Test Status Reset", error, { runId });
      }
    }, 5000);

    sendSuccessResponse(res, { runId, testStatus: "COMPLETED" }, "Test completed successfully");
  } catch (error) {
    logError("Test Completed", error, { runId });
    sendErrorResponse(res, 500, "Failed to handle test completion");
  }
};

/**
 * 학습 서버 생존 신호 핸들러 (이슈 #21)
 * POST /callbacks/:id/heartbeat
 *
 * 학습 스레드가 도는 동안 10초마다 온다. 진행 중(RUNNING·PAUSED)인 실험의 마지막 신호 시각만 갱신한다.
 * 이미 끝난 실험은 바꾸지 않으므로, 응답 없음으로 FAILED가 된 실험도 되돌아가지 않는다.
 * 10초마다 오므로 성공은 로그로 남기지 않는다.
 */
const heartbeatHandler = async (req: Request, res: Response): Promise<void> => {
  const runId = validateId(req, res);
  if (!runId) return;

  try {
    const result = await RunModel.updateOne(
      { _id: runId, status: { $in: ACTIVE_STATUSES } },
      { $set: { lastHeartbeatAt: new Date() } }
    );
    if (result.matchedCount === 0) {
      // 없는 실험이거나 이미 끝난 실험. 학습 서버는 응답을 보지 않고 다음 신호를 보낸다.
      sendErrorResponse(res, 409, "Run is not running or paused");
      return;
    }
    res.status(204).end();
  } catch (error) {
    logError("Heartbeat", error, { runId });
    sendErrorResponse(res, 500, "Failed to record heartbeat");
  }
};

// ===== 라우트 정의 =====

router.post("/:id/experiment-completed", asyncHandler(experimentCompletedHandler));
router.post("/:id/test-completed", asyncHandler(testCompletedHandler));
router.post("/:id/heartbeat", asyncHandler(heartbeatHandler));

export default router;