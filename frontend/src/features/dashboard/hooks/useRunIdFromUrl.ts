// frontend/src/features/dashboard/hooks/useRunIdFromUrl.ts
import { useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useExperimentStore, toChartPoint } from '@/store/experimentStore';
import { runsApi, templatesApi, trainingMetricsApi } from '@/shared/api';
import { useToastNotification } from '@/shared/hooks';
import { UILogger } from '@/shared/utils/logger';
import { useSimulationSpeed } from '../hooks/useSimulationSpeed';

/**
 * URL에서 runId를 추출하고 실험 상태를 복원하는 커스텀 훅
 * URL 파라미터와 전역 상태 간의 동기화를 관리
 */
export function useRunIdFromUrl(): {
  runIdFromUrl: string | null;
  updateUrlWithRunId: (newRunId: string | null) => void;
} {
  // URL 파라미터 관리
  const [searchParams, setSearchParams] = useSearchParams();
  
  // 실험 관련 상태
  const runId = useExperimentStore((state) => state.runId);
  const setRunId = useExperimentStore((state) => state.setRunId);
  const setRunName = useExperimentStore((state) => state.setRunName);
  const setTrainingStatus = useExperimentStore((state) => state.setTrainingStatus);
  
  // 템플릿 관련 상태
  const setTemplateId = useExperimentStore((state) => state.setTemplateId);
  const setTemplateData = useExperimentStore((state) => state.setTemplateData);
  
  // 메트릭 관련 상태
  const mergeTrainingMetrics = useExperimentStore((state) => state.mergeTrainingMetrics);
  const resetTrainingMetrics = useExperimentStore((state) => state.resetTrainingMetrics);
  
  // 토스트 알림 함수들
  const { showSuccess, showError } = useToastNotification();

  // 시뮬레이션 속도 조절
  const { handleSpeedChange } = useSimulationSpeed();

  /**
   * 템플릿 정보 복구 함수
   * @param templateId - 복구할 템플릿 ID
   */
  const restoreTemplate = async (templateId: string): Promise<void> => {
    try {
      UILogger.action('템플릿 복구 시작', { templateId });
      
      const response = await templatesApi.getTemplate(templateId);
      if (response.data?.template) {
        const template = response.data.template;
        
        // 전역 상태에 템플릿 정보 저장
        setTemplateId(templateId);
        setTemplateData({
          id: template.id,
          name: template.name,
          note: template.note || '',
          algName: template.algName,
          envName: template.envName,
          algConfig: template.algConfig,
          envConfig: template.envConfig,
          createdAt: template.createdAt
        });
        
        UILogger.action('템플릿 복구 성공', { templateId, templateName: template.name });
      } else {
        UILogger.error('useRunIdFromUrl', `템플릿 데이터 없음: ${templateId}`);
      }
    } catch (error) {
      UILogger.error('useRunIdFromUrl', `템플릿 복구 실패: ${error}`);
    }
  };

  /**
   * 실시간 메트릭 복구 함수
   * @param runId - 복구할 실험 ID
   */
  const restoreMetrics = async (runId: string): Promise<void> => {
    try {
      UILogger.action('메트릭 복구 시작', { runId });
      
      const response = await trainingMetricsApi.getRunMetrics(runId);
      if (response.data?.metrics) {
        const metrics = response.data.metrics;
        
        // 메트릭 데이터를 차트 형식으로 변환
        const chartData = metrics.map(metric => toChartPoint(metric.timesteps, metric.chartMetrics));
        
        // 덮어쓰지 않고 스텝 기준으로 병합한다. 응답을 기다리는 동안 SSE로 받은 지표를 지우지 않기 위해서다. (이슈 #10)
        // 응답이 늦게 와서 그사이 다른 실험으로 바뀌었으면 버린다.
        if (useExperimentStore.getState().runId === runId) {
          mergeTrainingMetrics(chartData);
        }
        
        UILogger.action('메트릭 복구 성공', { 
          runId, 
          metricsCount: metrics.length,
          chartDataCount: chartData.length 
        });
      } else {
        UILogger.action('메트릭 데이터 없음', { runId });
      }
    } catch (error) {
      UILogger.error('useRunIdFromUrl', `메트릭 복구 실패: ${error}`);
    }
  };

  /**
   * URL과 전역 상태 간의 동기화를 관리하는 useEffect
   * URL에 runId가 있으면 실험 상태를 복원하고,
   * 전역 상태에 runId가 있으면 URL에 추가
   */
  useEffect(() => {
    const urlRunId = searchParams.get('runId');
    
    if (urlRunId && urlRunId !== runId) {
      // URL에 runId가 있고 현재 store의 runId와 다르면 복원
      checkAndRestoreRun(urlRunId);
    } else if (!urlRunId && runId) {
      // URL에 runId가 없는데 store에 runId가 있으면 URL에 추가
      setSearchParams({ runId });
    }
  }, [searchParams, runId, setRunId, setRunName, setTrainingStatus, setSearchParams]);

  /**
   * 실험 상태를 확인하고 복원하는 함수
   * URL에서 받은 runId로 실험 정보를 조회하고,
   * 실행 중인 실험이면 상태를 복원
   */
  const checkAndRestoreRun = async (targetRunId: string): Promise<void> => {
    try {
      // 1. runId로 실행 정보 조회
      const runResponse = await runsApi.getRun(targetRunId);
      if (!runResponse.data) {
        throw new Error('Run not found');
      }
      const runData = runResponse.data.run;

      if (runData) {
        // 2. 상태가 running 또는 paused인지 확인
        if (runData.status === 'RUNNING' || runData.status === 'PAUSED') {
          // 3. 기본 실험 정보 복원
          // 이전 실험의 차트 데이터는 실험을 바꾸기 전에 비운다. (이후 지표는 모두 병합으로 들어온다)
          resetTrainingMetrics();
          setRunId(targetRunId);
          setRunName(runData.runName || `Run ${targetRunId}`);
          setTrainingStatus(runData.status as 'idle' | 'running' | 'paused' | 'failed' | 'completed' | 'stopped');
          
          // 4. 템플릿 정보 복원 (templateId가 있는 경우)
          if (runData.templateId) {
            await restoreTemplate(runData.templateId);
          }
          
          // 5. 실시간 메트릭 복원
          await restoreMetrics(targetRunId);

          // 6. 시뮬레이션 속도 복교
          await handleSpeedChange(targetRunId, 2);

          showSuccess('실험이 복구되었습니다.');
          UILogger.action('URL에서 실험 복원 완료', { 
            runId: targetRunId, 
            status: runData.status,
            templateId: runData.templateId 
          });
        } else {
          // 7. 실행 중이 아니면 URL에서 제거
          setSearchParams({});
          UILogger.action('실험이 실행 중이 아니므로 URL에서 제거', { runId: targetRunId });
        }
      } else {
        // 8. 실행 정보가 없으면 URL에서 제거
        setSearchParams({});
        showError('실험을 찾을 수 없습니다.');
        UILogger.action('실험을 찾을 수 없어 URL에서 제거', { runId: targetRunId });
      }
    } catch (error) {
      UILogger.error('useRunIdFromUrl', `실험 상태 확인 중 오류: ${error}`);
      // 에러 발생 시 URL에서 제거
      setSearchParams({});
      
      // 네트워크 오류와 데이터 오류 구분
      if (error instanceof Error) {
        if (error.message.includes('fetch') || error.message.includes('network')) {
          showError('연결 오류가 발생했습니다.');
        } else {
          showError('실험을 찾을 수 없습니다.');
        }
      } else {
        showError('알 수 없는 오류가 발생했습니다.');
      }
    }
  };

  /**
   * URL에 runId를 업데이트하는 함수
   * 새로운 runId를 URL에 추가하거나 제거
   */
  const updateUrlWithRunId = (newRunId: string | null): void => {
    if (newRunId) {
      setSearchParams({ runId: newRunId });
    } else {
      setSearchParams({});
    }
  };

  return {
    /** URL에서 추출한 runId */
    runIdFromUrl: searchParams.get('runId'),
    /** URL에 runId를 업데이트하는 함수 */
    updateUrlWithRunId
  };
}