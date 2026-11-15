import { Router, Request, Response } from 'express';
import { TrainingMetricModel } from '../models/TrainingMetric';
import { RunModel } from '../models/Run';
import mongoose from 'mongoose';
import { 
  validateId, 
  sendErrorResponse, 
  sendSuccessResponse, 
  logError, 
  logInfo 
} from '../utils/apiHelpers';

const router = Router();

// runId별로 SSE 클라이언트 관리
// 끊긴 구간은 화면이 다시 연결할 때 DB에서 채운다(이슈 #10). 그래서 서버는 이벤트 ID와 재전송 버퍼를 두지 않는다.
const runClients: { [runId: string]: Set<Response> } = {};

// ObjectId 유효성 검사 헬퍼 함수
const isValidObjectId = (id: string): boolean => {
  return mongoose.Types.ObjectId.isValid(id);
};

// SSE 엔드포인트 - runId별 실시간 메트릭 스트림
router.get('/api/training-metrics/stream/:runId', (req: Request, res: Response) => {
  const { runId } = req.params;

  // runId 유효성 검사
  if (!runId || !isValidObjectId(runId)) {
    return res.status(400).json({ error: 'Invalid runId format.' });
  }

  // SSE 헤더 설정 (표준 준수)
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    'Connection': 'keep-alive',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Cache-Control',
    'X-Accel-Buffering': 'no' // Nginx 버퍼링 비활성화
  });

  // 클라이언트를 runId별로 관리
  if (!runClients[runId]) {
    runClients[runId] = new Set();
  }
  runClients[runId]!.add(res);

  console.log(`SSE client connected for runId: ${runId}`);

  // 연결 확인 메시지 전송
  // 반드시 클라이언트를 등록한 뒤에 보낸다. 화면은 connected를 받은 뒤 마지막 스텝 이후를 DB에서 채우므로,
  // 그전에 저장된 지표는 DB 조회에, 그 뒤 지표는 이 연결로 들어와 빈틈이 생기지 않는다. (이슈 #10)
  res.write(`data: ${JSON.stringify({ type: 'connected', runId })}\n\n`);

  // 하트비트 메커니즘 (30초마다 ping 전송). 화면은 65초 동안 신호가 없으면 다시 연결한다.
  const heartbeatInterval = setInterval(() => {
    try {
      res.write(`data: ${JSON.stringify({ type: 'ping', timestamp: Date.now() })}\n\n`);
    } catch (error) {
      console.error('Heartbeat error:', error);
      clearInterval(heartbeatInterval);
    }
  }, 30000);

  // 클라이언트 연결 해제 처리
  req.on('close', () => {
    clearInterval(heartbeatInterval);
    if (runClients[runId]) {
      runClients[runId]!.delete(res);
      console.log(`SSE client disconnected for runId: ${runId}`);
    }
  });

  req.on('error', (err) => {
    console.error('SSE connection error:', err);
    clearInterval(heartbeatInterval);
    if (runClients[runId]) {
      runClients[runId]!.delete(res);
    }
  });
});

// 단일 TrainingMetric 데이터 저장
router.post("/training-metrics", async (req, res) => {
  try {
    const { runId, phase, timesteps, metrics } = req.body;
    console.log("received metrics", req.body);
    
    // 필수 필드 검증
    if (!runId || !phase || !timesteps || !metrics || !Array.isArray(metrics)) {
      return res.status(400).json({ 
        error: 'Missing required fields: runId, phase, timesteps, metrics' 
      });
    }

    // runId 유효성 검사
    if (!isValidObjectId(runId)) {
      return res.status(400).json({ error: 'Invalid runId format.' });
    }

    // Run에서 알고리즘 정보 조회
    const run = await RunModel.findById(runId).select('algName totalSteps').lean();
    
    if (!run) {
      return res.status(404).json({ error: 'Run not found.' });
    }

    const algorithm = run.algName;

    // TrainingMetric 생성
    const trainingMetric = new TrainingMetricModel({
      runId,
      phase,
      timesteps,
      algorithm,
      rawMetrics: metrics
      // indexedMetrics, chartMetrics는 pre-save 미들웨어에서 자동 설정
    });

    // 저장
    const savedMetric = await trainingMetric.save();

    // runId별로 연결된 SSE 클라이언트에게만 실시간 알림 전송
    if (runClients[runId]) {
      // undefined 값들을 null로 변환하여 프론트엔드에서 필터링 가능하게 함
      const cleanedChartMetrics = Object.fromEntries(
        Object.entries(savedMetric.chartMetrics || {}).map(([key, value]) => [
          key, 
          value === undefined ? null : value
        ])
      );
      
      const payload = { 
        timesteps, 
        chartMetrics: cleanedChartMetrics 
      };
      
      console.log('payload', payload);
      console.log(`📤 Sending SSE event to ${runClients[runId]!.size} clients for runId: ${runId}`);
      
      runClients[runId]!.forEach((res: Response) => {
        try {
          res.write(`data: ${JSON.stringify(payload)}\n\n`);
        } catch (error) {
          console.error('❌ Error sending to SSE client:', error);
          // 연결이 끊어진 클라이언트 제거
          runClients[runId]!.delete(res);
        }
      });
    } else {
      console.log('❌ No SSE clients found for runId:', runId);
    }

    res.status(201).json({
      message: 'Training metric saved successfully',
      data: {
        _id: savedMetric._id,
        runId: savedMetric.runId,
        phase: savedMetric.phase,
        timesteps: savedMetric.timesteps,
        algorithm: savedMetric.algorithm,
        chartMetrics: savedMetric.chartMetrics
      }
    });

  } catch (error: any) {
    console.error('Save training metric error:', error);
    
    // 중복 키 오류 처리
    if (error.code === 11000) {
      return res.status(409).json({ 
        error: 'Training metric for this run, phase and timestep already exists',
        details: error.keyValue
      });
    }
    
    res.status(500).json({ error: 'Failed to save training metric.' });
  }
});


export default router;