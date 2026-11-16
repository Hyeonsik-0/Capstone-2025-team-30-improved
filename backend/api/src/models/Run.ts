// Run 스키마(전체)
import { Schema, model, Types } from 'mongoose';
import type { InferSchemaType } from 'mongoose';

const RunSchema = new Schema({
  /* 기본 식별자 */
  runName: { type: String, required: true, index: true },

  /* 실험/테스트 상태 */
  status: {
    type: String,
    enum: ["IDLE", "RUNNING", "PAUSED", "FAILED", "COMPLETED", "STOPPED"],
    required: true,
    index: true
  },
  testStatus: {
    type: String,
    enum: ["INVALID", "IDLE", "TESTING", "PAUSED", "COMPLETED"],
    required: true,
    index: true
  },

  /* 템플릿 참조 */
  templateId: { type: Types.ObjectId, ref: 'ExperimentTemplate', index: true },

  /* 스키마 버전 */
  schemaVersion: { type: String, default: "1.0", index: true },

  /* 레거시(호환 유지용) */
  envName:   { type: String, required: true },
  envConfig: { type: Schema.Types.Mixed, required: true },
  algName:   { type: String, required: true },
  algConfig: { type: Schema.Types.Mixed, required: true },

  /* 실행 시점 설정 스냅샷 및 스냅샷 해시 */
  configSnapshot: {
    env: {
      name: { type: String, required: true },
      params: {
        type: Map,
        of: Number,                    // ← 파라미터는 숫자형으로 저장
        required: true
      }
    },
    algo: {
      name: { type: String, required: true },
      params: {
        type: Map,
        of: Schema.Types.Mixed,        // ← 파라미터는 숫자/문자열 모두 저장 가능
        required: true
      }
    },
    versions: {
      type: Map,                       // ← 훈련 런타임/라이브러리 버전 맵
      of: Schema.Types.Mixed,          //    (문자열/불리언 등 자유롭게 수용)
      default: undefined
    }
  },
  configHash: { type: String, required: true },

  /* 산출물 경로 */
  artifacts: {
    modelZip: { type: String },
    logsCsv:  { type: String }
  },

  /* 실행 요약 */
  totalSteps: { type: Number, default: 100000 },
  startTime:  { type: Date },
  endTime:    { type: Date },

  /* 학습 서버가 마지막으로 생존 신호를 보낸 시각 (이슈 #21) */
  lastHeartbeatAt: { type: Date },
  /* 실패 이유. 스키마에 없으면 Mongoose가 갱신에서 빼서 저장되지 않는다. (이슈 #21) */
  error: { type: String }
}, { timestamps: true });

/* 인덱스 */
RunSchema.index({ templateId: 1, status: 1, createdAt: -1 });
RunSchema.index({ runName: 1 });

export type RunDoc = InferSchemaType<typeof RunSchema>;
export const RunModel = model("Run", RunSchema);
