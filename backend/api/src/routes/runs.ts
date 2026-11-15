import { Router, Request, Response } from "express";
import { RunModel } from "../models/Run";
import { TrainingMetricModel } from "../models/TrainingMetric";
import { 
  validateId, 
  sendErrorResponse, 
  sendSuccessResponse, 
  logError, 
  logInfo 
} from "../utils/apiHelpers";
import { deleteArtifacts } from "./artifacts";

const router = Router();
const FASTAPI_BASE = process.env.FASTAPI_BASE || 'http://localhost:8000';

// 런 목록 조회 핸들러
export const getRunsHandler = async (req: Request, res: Response) => {
  try {
    const runs = await RunModel.find()
      .select("_id runName status envName algName createdAt")
      .sort({ createdAt: -1 })
      .lean();

    const transformedRuns = runs.map(run => ({
      runId: run._id,
      runName: run.runName,
      status: run.status,
      envName: run.envName,
      algName: run.algName,
      createdAt: run.createdAt,
    }));

    logInfo("Get Runs", "Successfully fetched runs list", { count: transformedRuns.length });
    sendSuccessResponse(res, { runs: transformedRuns });
  } catch (error) {
    logError("Get Runs", error);
    sendErrorResponse(res, 500, "Failed to get runs");
  }
};

// 런 상세 조회 핸들러
export const getRunDetailHandler = async (req: Request, res: Response) => {
  const runId = validateId(req, res);
  if (!runId) return;

  try {
    const run = await RunModel.findById(runId).lean();
    if (!run) {
      sendErrorResponse(res, 404, "Run not found");
      return;
    }
    
    const transformedRuns = {
      runId: run._id,
      runName: run.runName,
      templateId: run.templateId,
      status: run.status,
      testStatus: run.testStatus,
      envName: run.envName,
      algName: run.algName,
      envConfig: run.configSnapshot?.env?.params,
      algConfig: run.configSnapshot?.algo?.params,
      createdAt: run.createdAt,
      startTime: run.startTime,
      endTime: run.endTime,
      configSnapshot: run.configSnapshot,
      configHash: run.configHash,
    };
    
    logInfo("Get Run Detail", "Successfully fetched run detail", { runId });
    sendSuccessResponse(res, { run: transformedRuns });

  } catch (error) {
    logError("Get Run Detail", error, { runId });
    sendErrorResponse(res, 500, "Failed to get run");
  }
};

// 런 삭제 핸들러
export const deleteRunHandler = async (req: Request, res: Response) => {
  const runId = validateId(req, res);
  if (!runId) return;

  try {
    // 1. 런 존재 여부 및 상태 확인
    const run = await RunModel.findById(runId).select("runName status").lean();
    
    if (!run) {
      sendErrorResponse(res, 404, "Run not found");
      return;
    }

    // 실행 중인 런은 삭제 불가
    if (["RUNNING", "PAUSED"].includes(run.status)) {
      sendErrorResponse(res, 400, "Cannot delete running or paused experiment. Please stop it first.", {
        currentStatus: run.status
      });
      return;
    }

    // 2. 런 데이터 삭제
    const runDeleteResult = await RunModel.deleteOne({ _id: runId });
    
    if (runDeleteResult.deletedCount === 0) {
      sendErrorResponse(res, 404, "Run not found or already deleted");
      return;
    }

    // 3. 해당 런의 메트릭 데이터 삭제 (있을 경우에만)
    let deletedMetricsCount = 0;
    try {
      const metricsDeleteResult = await TrainingMetricModel.deleteMany({ runId });
      deletedMetricsCount = metricsDeleteResult.deletedCount;
    } catch (metricsError) {
      // 메트릭 데이터가 없거나 삭제 실패해도 런 삭제는 성공으로 처리
      logError("Delete Metrics", metricsError, { runId, message: "Metrics deletion failed, but run was deleted" });
    }

    // 4. FastAPI에 모델과 로그 파일 삭제 요청
    let artifactsDeleteResult = { success: true, message: 'No artifacts to delete' };
    try {
      artifactsDeleteResult = await deleteArtifacts(run.runName);
      logInfo("Delete Artifacts", "FastAPI artifacts deletion result", {
        runId,
        runName: run.runName,
        success: artifactsDeleteResult.success,
        message: artifactsDeleteResult.message
      });
    } catch (artifactsError) {
      logError("Delete Artifacts", artifactsError, { runId, runName: run.runName });
      // 아티팩트 삭제 실패해도 전체 삭제는 성공으로 처리
    }

    logInfo("Delete Run", "Successfully deleted run, metrics and artifacts", {
      runId,
      runName: run.runName,
      deletedMetricsCount,
      artifactsDeleted: artifactsDeleteResult.success
    });

    sendSuccessResponse(res, {
      message: "Run and all associated data deleted successfully",
      deletedRunId: runId,
      deletedRunName: run.runName,
      deletedMetricsCount,
      artifactsDeleted: artifactsDeleteResult.success,
      artifactsMessage: artifactsDeleteResult.message
    });

  } catch (error) {
    logError("Delete Run", error, { runId });
    sendErrorResponse(res, 500, "Failed to delete run");
  }
};

// 런 상태 조회 핸들러
export const getRunStatusHandler = async (req: Request, res: Response) => {
  const runId = validateId(req, res);
  if (!runId) return;

  try {
    const run = await RunModel.findById(runId).select("status testStatus").lean();

    if (!run) {
      sendErrorResponse(res, 404, "Run not found");
      return;
    }

    // logInfo("Get Run Status", "Successfully fetched run status", { runId, status: run.status });
    sendSuccessResponse(res, {
      status: run.status,
      testStatus: run.testStatus
    });

  } catch (error) {
    logError("Get Run Status", error, { runId });
    sendErrorResponse(res, 500, "Failed to get run status");
  }
};

// 특정 환경에서 모든 런 목록 조회 핸들러
export const getRunsByEnvironmentHandler = async (req: Request, res: Response) => {
  const { envName } = req.params;

  if (!envName) {
    sendErrorResponse(res, 400, "Environment name is required");
    return;
  }

  try {
    const runs = await RunModel.find({
      "envName": envName
    })
      .select("_id runName status createdAt envName algName")
      .sort({ createdAt: -1 })
      .lean();

    const transformedRuns = runs.map(run => ({
      runId: run._id,
      runName: run.runName,
      algName: run.algName,
      status: run.status,
      createdAt: run.createdAt
    }));

    logInfo("Get Runs by Environment", "Successfully fetched all runs by environment", { 
      envName, 
      count: transformedRuns.length 
    });
    
    sendSuccessResponse(res, {
      envName: envName,
      count: transformedRuns.length,
      runs: transformedRuns
    });

  } catch (error) {
    logError("Get Runs by Environment", error, { envName });
    sendErrorResponse(res, 500, "Failed to get runs by environment");
  }
};

// 런 일시정지 핸들러
export const pauseRunHandler = async (req: Request, res: Response) => {
  try {
    const { id : runId } = req.params;

    if (runId && !validateId(req, res)) {
      return;
    }

    // 상태를 RUNNING에서 PAUSED로 변경
    const result = await RunModel.updateOne(
      { _id: runId, status: "RUNNING" },
      { status: "PAUSED" }
    );

    if (result.matchedCount === 0) {
      const run = await RunModel.findById(runId).select("status").lean();
      
      if (!run) {
        return res.status(404).json({ error: "Run not found." });
      }
      
      return res.status(400).json({ 
        error: "Run is not currently running.",
        currentStatus: run.status 
      });
    }

    if (result.modifiedCount === 0) {
      return res.status(500).json({ error: "Failed to pause run." });
    }

    // FastAPI 호출
    try {
      const response = await fetch(`${FASTAPI_BASE}/runs/${runId}:pause`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        }
      });

      if (!response.ok) {
        throw new Error(`FastAPI error: ${response.status} ${response.statusText}`);
      }
      
      const data = await response.json();
      console.log('FastAPI training paused successfully:', data);

    } catch (error) {
      console.error('FastAPI call failed:', error);

      // FastAPI 호출 실패 시 상태를 FAILED로 업데이트
      await RunModel.updateOne(
        { _id: runId },
        { 
          $set: { 
            status: "FAILED", 
            endTime: new Date(),
            error: 'FastAPI call failed'
          } 
        }
      );
    }

    res.json({
      runId: runId,
      status: "PAUSED",
      message: "Run paused successfully."
    });

  } catch (error) {
    console.error("Pause run error:", error);
    res.status(500).json({ error: "Failed to pause run." });
  }
};

// 런 재개 핸들러
export const resumeRunHandler = async (req: Request, res: Response) => {
  try {
    const { id : runId } = req.params;

    if (runId && !validateId(req, res)) {
      return;
    }

    // 상태를 PAUSED에서 RUNNING으로 변경
    const result = await RunModel.updateOne(
      { _id: runId, status: "PAUSED" },
      { status: "RUNNING" }
    );

    if (result.matchedCount === 0) {
      const run = await RunModel.findById(runId).select("status").lean();

      if (!run) {
        return res.status(404).json({ error: "Run not found." });
      }

      return res.status(400).json({
        error: "Run is not currently paused.",
        currentStatus: run.status
      });
    }

    if (result.modifiedCount === 0) {
      return res.status(500).json({ error: "Failed to resume run." });
    }

    // FastAPI 호출
    try {
      const response = await fetch(`${FASTAPI_BASE}/runs/${runId}:resume`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        }
      });

      if (!response.ok) {
        throw new Error(`FastAPI error: ${response.status} ${response.statusText}`);
      }
      
      const data = await response.json();
      console.log('FastAPI training resumed successfully:', data);

    } catch (error) {
      console.error('FastAPI call failed:', error);

      // FastAPI 호출 실패 시 상태를 FAILED로 업데이트
      await RunModel.updateOne(
        { _id: runId },
        {
          $set: {
            status: "FAILED",
            endTime: new Date(),
            error: 'FastAPI call failed'
          } 
        }
      );
    }

    res.json({
      runId: runId,
      status: "RUNNING",
      message: "Run resumed successfully."
    });
  } catch (error) {
    console.error("Resume run error:", error);
    res.status(500).json({ error: "Failed to resume run." });
  }
};

// 런 종료 핸들러
export const stopRunHandler = async (req: Request, res: Response) => {
  try {
    const { id: runId } = req.params;

    if (runId && !validateId(req, res)) {
      return;
    }

    // RUNNING 또는 PAUSED 상태에서만 STOPPED로 변경 가능
    const result = await RunModel.updateOne(
      { 
        _id: runId, 
        status: { $in: ["RUNNING", "PAUSED"] } 
      },
      { status: "STOPPED" }
    );

    if (result.matchedCount === 0) {
      const run = await RunModel.findById(runId).select("status").lean();

      if (!run) {
        return res.status(404).json({ error: "Run not found." });
      }

      return res.status(400).json({
        error: "Run cannot be stopped in current state.",
        currentStatus: run.status
      });
    }

    if (result.modifiedCount === 0) {
      return res.status(500).json({ error: "Failed to stop run." });
    }

    // FastAPI 호출
    try {
      const response = await fetch(`${FASTAPI_BASE}/runs/${runId}:stop`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        }
      });

      if (!response.ok) {
        throw new Error(`FastAPI error: ${response.status} ${response.statusText}`);
      }
      
      const data = await response.json();
      console.log('FastAPI training stopped successfully:', data);

    } catch (error) {
      console.error('FastAPI call failed:', error);

      // FastAPI 호출 실패 시 상태를 FAILED로 업데이트
      await RunModel.updateOne(
        { _id: runId },
        {
          $set: {
            status: "FAILED",
            endTime: new Date(),
            error: 'FastAPI call failed'
          } 
        }
      );
    }

    res.json({
      runId: runId,
      status: "STOPPED",
      message: "Run stopped successfully."
    });
  } catch (error) {
    console.error("Stop run error:", error);
    res.status(500).json({ error: "Failed to stop run." });
  }
};

// 런 시뮬레이터 속도 변경 핸들러
export const changeSimSpeedHandler = async (req: Request, res: Response) => {
  try {
    const { id: runId } = req.params;
    const { speed } = req.body;

    if (runId && !validateId(req, res)) {
      return;
    }

    if (typeof speed !== 'number' || !(speed > 0 && speed <= 10)) {
      return res.status(400).json({ error: "Invalid speed value. Must be a positive number between 0.1 and 10." });
    }

    try {
      const response = await fetch(`${FASTAPI_BASE}/runs/${runId}:simSpeed`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sim_speed: speed })
      });

      if (!response.ok) {
        throw new Error(`FastAPI error: ${response.status} ${response.statusText}`);
      }

      const data = await response.json();
      console.log('FastAPI simulation speed changed successfully:', data);

      res.json({
        runId: runId,
        speed: speed,
        message: "Simulation speed change request sent."
      });

    } catch (error) {
      console.error('FastAPI call failed:', error);
      res.status(500).json({ error: "Failed to change simulation speed (FastAPI error)." });
    }

  } catch (error) {
    console.error("Change simulation speed error:", error);
    res.status(500).json({ error: "Failed to change simulation speed." });
  }
};

// runId별 TrainingMetric 목록 조회 핸들러
// 쿼리 afterTimesteps(선택, 0 이상의 정수): 이 스텝보다 큰 지표만 돌려준다.
// SSE가 다시 연결될 때 끊긴 구간을 DB에서 채우는 용도다. (이슈 #10)
// 주면 metrics·chartData·total·currentProgress는 돌려준 구간 기준이고, 없으면 응답은 이전과 같다.
export const getRunTrainingMetricsHandler = async (req: Request, res: Response) => {
  const runId = validateId(req, res);
  if (!runId) return;

  const afterRaw = req.query.afterTimesteps;
  let afterTimesteps: number | undefined;
  if (afterRaw !== undefined) {
    if (typeof afterRaw !== 'string' || !/^\d+$/.test(afterRaw) || !Number.isSafeInteger(Number(afterRaw))) {
      sendErrorResponse(res, 400, 'Invalid afterTimesteps');
      return;
    }
    afterTimesteps = Number(afterRaw);
  }

  try {
    // Run 정보 조회 (알고리즘 및 총 스텝 수)
    const run = await RunModel.findById(runId).select('algName totalSteps').lean();
    if (!run) {
      sendErrorResponse(res, 404, 'Run not found');
      return;
    }

    // TrainingMetric 데이터 조회 (스텝 순서대로 정렬). (runId, timesteps) 인덱스를 쓴다.
    const filter = afterTimesteps === undefined
      ? { runId }
      : { runId, timesteps: { $gt: afterTimesteps } };
    const metrics = await TrainingMetricModel.find(filter)
      .sort({ timesteps: 1 }) // 스텝 번호 순으로 정렬
      .lean();

    // 차트용 간소화된 데이터 구성
    const chartData = metrics.map(metric => ({
      x: metric.timesteps, // 차트 x축은 현재 스텝 번호
      reward: metric.chartMetrics?.reward,
      loss: metric.chartMetrics?.loss,
      exploration: metric.chartMetrics?.exploration,
      efficiency: metric.chartMetrics?.efficiency,
      algorithm: metric.algorithm,
      timestamp: metric.createdAt
    }));

    const responseData = {
      runId,
      algorithm: run.algName,
      totalSteps: run.totalSteps, // 실험의 총 스텝 수
      currentProgress: metrics.length > 0 ? Math.max(...metrics.map(m => m.timesteps || 0)) : 0, // 현재까지 진행된 최대 스텝
      total: metrics.length, // 총 데이터 개수
      metrics, // 전체 데이터
      chartData // 차트용 데이터
    };

    logInfo("Get Run Training Metrics", "Successfully fetched training metrics", {
      runId,
      afterTimesteps,
      metricsCount: metrics.length
    });
    
    sendSuccessResponse(res, responseData);

  } catch (error) {
    logError("Get Run Training Metrics", error, { runId });
    sendErrorResponse(res, 500, 'Failed to get training metrics');
  }
};

// 피드백 전송 핸들러
export const sendFeedbackHandler = async (req: Request, res: Response) => {
  const { id: runId } = req.params;
  const { text } = req.body;

  if (!runId || !text) {
    return res.status(400).json({ error: "Invalid request data." });
  }

  // FastAPI 호출
  try {
    const response = await fetch(`${FASTAPI_BASE}/runs/${runId}/feedback`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        message: text
      })
    });

    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
    }

    const result = await response.json();
    logInfo("Send Feedback", "Feedback sent successfully", { runId, text });
    
    // 피드백 전달 성공 후 training status를 paused -> running으로 변경
    try {
      await RunModel.findByIdAndUpdate(runId, { status: 'RUNNING' });
      logInfo("Update Training Status", "Training status updated to running", { runId });
    } catch (updateError) {
      logError("Update Training Status", updateError, { runId });
      // 상태 업데이트 실패해도 피드백 전달은 성공으로 처리
    }
    
    sendSuccessResponse(res, { message: result.message || "Feedback sent successfully." });
    
  } catch (error) {
    logError("Send Feedback", error, { runId });
    sendErrorResponse(res, 500, "Failed to send feedback.");
  }
};

// 사용 가능한 모델 목록 조회 핸들러
export const getAvailableModelsHandler = async (req: Request, res: Response) => {
  try {
    const { environment } = req.query;
    
    // 쿼리 조건 구성
    const query: any = {
      status: "COMPLETED",
      "artifacts.modelZip": { $exists: true, $ne: "" }
    };
    
    // 환경이 지정된 경우 해당 환경의 모델만 조회
    if (environment) {
      query.envName = environment;
    }
    
    // 성공한 실험이고 모델 파일이 있는 런들만 조회
    const availableRuns = await RunModel.find(query)
      .select("runName algName envName createdAt artifacts.modelZip")
      .sort({ createdAt: -1 })
      .lean();

    const availableModels = availableRuns.map(run => ({
      runName: run.runName,
      algName: run.algName,
      envName: run.envName,
      createdAt: run.createdAt
    }));

    logInfo("Get Available Models", "Successfully fetched available models", { 
      count: availableModels.length,
      environment 
    });
    
    sendSuccessResponse(res, {
      count: availableModels.length,
      models: availableModels
    });

  } catch (error) {
    logError("Get Available Models", error);
    sendErrorResponse(res, 500, "Failed to get available models");
  }
};

router.get("/", getRunsHandler);
router.get("/available-models", getAvailableModelsHandler);
router.get("/:id", getRunDetailHandler);
router.delete("/:id", deleteRunHandler);
router.get("/:id/status", getRunStatusHandler);
router.get("/:id/training-metrics", getRunTrainingMetricsHandler);
router.get("/environments/:envName", getRunsByEnvironmentHandler);
router.post("/:id/pause", pauseRunHandler);
router.post("/:id/resume", resumeRunHandler);
router.post("/:id/stop", stopRunHandler);
router.post("/:id/sim-speed", changeSimSpeedHandler);
router.post("/:id/feedback", sendFeedbackHandler);

export default router;