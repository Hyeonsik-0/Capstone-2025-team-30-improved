import 'dotenv/config';
import express from 'express';
import cors from "cors";
import { connectMongo } from './db/mongoose';
import { RunModel } from './models/Run';
import { logInfo, logError } from './utils/apiHelpers';
import { startRunWatchdog } from './utils/runWatchdog';

import runsRoutes from './routes/runs';
import callbacksRoutes from './routes/callbacks';
import modelsRoutes from './routes/models';
import experimentTemplateRoutes from './routes/experimentTemplate';
import algorithmRoutes from './routes/algorithms';
import environmentsRoutes from './routes/environments';
import trainingMetricsRoutes from './routes/traingMetrics';
import artifactsRoutes from './routes/artifacts';
import http from 'http';

const app = express();

// 미들 웨어
app.use(cors());
app.use(express.json());

// 프론트엔드가 사용하는 엔드포인트
app.get("/api/health", (req, res) => res.send("ok"));
app.use("/api/runs", runsRoutes);
app.use("/api/models", modelsRoutes);
app.use("/api/experiment-templates", experimentTemplateRoutes);
app.use("/api/algorithms", algorithmRoutes);
app.use("/api/environments", environmentsRoutes);
app.use("/api/artifacts", artifactsRoutes);

// FastAPI가 사용하는 엔드포인트
app.use("/", trainingMetricsRoutes);
app.use("/callbacks", callbacksRoutes);

const server = http.createServer(app);

/**
 * 서버 시작 시 테스트 중인 모델들을 IDLE로 변경
 * 진행 중(RUNNING·PAUSED)인 실험은 여기서 정리하지 않는다. BFF만 재시작됐다면 학습은 계속되고 있으므로,
 * 학습 서버의 생존 신호로 판단한다. (utils/runWatchdog.ts, 이슈 #21)
 */
const cleanupTestingRuns = async (): Promise<void> => {
  try {
    // TESTING 상태인 모델들을 IDLE로 변경 (status가 RUNNING이 아닌 경우)
    const testingModelsResult = await RunModel.updateMany(
      { 
        status: { $ne: "RUNNING" },
        testStatus: { $in: ["TESTING", "PAUSED"] }
      },
      { 
        $set: { 
          testStatus: "IDLE"
        } 
      }
    );

    if (testingModelsResult.modifiedCount > 0) {
      logInfo("Server Startup", `Reset ${testingModelsResult.modifiedCount} testing models to IDLE`, {
        modifiedCount: testingModelsResult.modifiedCount
      });
    }

    if (testingModelsResult.modifiedCount === 0) {
      logInfo("Server Startup", "No testing models found to cleanup");
    }
  } catch (error) {
    logError("Server Startup", error, { message: "Failed to cleanup testing models" });
  }
};

const port = Number(process.env.PORT || 8080);
connectMongo().then(async () => {
  // 서버 시작 시 테스트 중인 모델 정리
  await cleanupTestingRuns();
  
  server.listen(port, () => console.log(`API & SSE server running on :${port}`));
  // 학습 서버 응답 없음 판정 (이슈 #21)
  startRunWatchdog();
});