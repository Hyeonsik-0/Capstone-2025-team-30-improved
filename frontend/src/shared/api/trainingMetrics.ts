import { ApiClient } from './client';
import type { ApiResponse } from './types';

/**
 * 훈련 메트릭 관련 타입 정의
 */
export interface TrainingMetric {
  _id: string;
  runId: string;
  phase: string;
  timesteps: number;
  algorithm: string;
  rawMetrics: {
    name: string;
    value: any;
    group: string;
  }[];
  indexedMetrics: {
    [group: string]: {
      [metricName: string]: any;
    };
  };
  chartMetrics: {
    [metricName: string]: any;
  };
  createdAt: string;
  updatedAt: string;
  __v: number;
}

export interface ChartDataPoint {
  x: number;
  algorithm: string;
  timestamp: string;
  [metricName: string]: any; // 동적 메트릭 필드들
}

export interface TrainingMetricsResponse {
  runId: string;
  algorithm: string;
  totalSteps: number;
  currentProgress: number;
  total: number;
  metrics: TrainingMetric[];
  chartData: ChartDataPoint[];
}

/**
 * 훈련 메트릭 관련 API 함수들
 */
export const trainingMetricsApi = {
  /**
   * 실험의 훈련 메트릭 조회
   * GET /api/runs/:runId/training-metrics[?afterTimesteps=N]
   * @param afterTimesteps - 주면 이 스텝보다 큰 지표만 받는다 (SSE 재연결 때 끊긴 구간 채우기, 이슈 #10)
   */
  getRunMetrics: (runId: string, afterTimesteps?: number): Promise<ApiResponse<TrainingMetricsResponse>> => {
    const query = afterTimesteps === undefined ? '' : `?afterTimesteps=${afterTimesteps}`;
    return ApiClient.get(`/runs/${runId}/training-metrics${query}`);
  },

  /**
   * 런 상세 정보 조회 (훈련 메트릭)
   * 기존 apiGet과 동일한 형식으로 반환
   * 
   * @param runId - 조회할 런 ID
   * @returns 훈련 메트릭 데이터 (기존 apiGet과 동일한 형식으로 반환)
   */
  getRunDetail: async (runId: string): Promise<any> => {
    const response = await ApiClient.get(`/runs/${runId}/training-metrics`);
    
    if (!response.data) {
      throw new Error('Training metrics not found');
    }
    
    // 기존 apiGet과 동일한 형식으로 반환 (ResultHeader.tsx에서 .data로 접근하므로)
    return {
      data: response.data
    };
  },
};
