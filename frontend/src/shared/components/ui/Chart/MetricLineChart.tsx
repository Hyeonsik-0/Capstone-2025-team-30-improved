import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid } from 'recharts';

interface LineConfig {
  dataKey: string;
  stroke: string;
  name: string;
  strokeWidth?: number;
  dot?: {
    r?: number;
    fill?: string;
    strokeWidth?: number;
  };
}

interface MetricLineChartProps {
  data: any[];
  lines: LineConfig[];
  title?: string;
  dataPointCount?: number;
  height?: number | string;
  showDataPointCount?: boolean;
  xAxisLabel?: string;
  yAxisLabel?: string;
}

/**
 * 메트릭 라인 차트 컴포넌트
 * 여러 라인을 표시할 수 있는 반응형 차트를 제공
 * @param data - 차트 데이터 배열
 * @param lines - 라인 설정 배열
 * @param title - 차트 제목 (선택사항)
 * @param dataPointCount - 데이터 포인트 개수 (선택사항)
 * @param height - 차트 높이 (기본값: 260)
 * @param showDataPointCount - 데이터 포인트 개수 표시 여부 (기본값: true)
 * @param xAxisLabel - X축 라벨 (기본값: 'Steps')
 * @param yAxisLabel - Y축 라벨 (선택사항)
 */

export function MetricLineChart({ 
  data, 
  lines, 
  title,
  dataPointCount,
  height = 260,
  showDataPointCount = true,
  xAxisLabel = 'Steps',
  yAxisLabel
}: MetricLineChartProps) {
  return (
    <div style={{ width: '100%', height: typeof height === 'string' ? height : `${height}px` }}>
      {title && (
        <div style={{ margin: '2px 0 6px 0', fontSize: '12px', color: '#666' }}>
          {showDataPointCount && dataPointCount && `총 ${dataPointCount}개 데이터 포인트`}
        </div>
      )}
      <ResponsiveContainer width="100%" height="100%">
        <LineChart margin={{ top: 10, right: 24, bottom: 28, left: 56 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e0e0e0" />
          {/* 스텝을 숫자 축으로 그려, 빠진 스텝이 간격으로 드러나게 한다. (이슈 #10) */}
          <XAxis 
            dataKey="x" 
            type="number"
            domain={['dataMin', 'dataMax']}
            allowDecimals={false}
            tickMargin={8}
            stroke="#666"
            fontSize={12}
            label={{ value: xAxisLabel, position: 'insideBottom', offset: -4, fill: '#666', fontSize: 12 }}
          />
          <YAxis 
            tickMargin={8}
            stroke="#666"
            fontSize={12}
            label={yAxisLabel ? { value: yAxisLabel, angle: -90, position: 'insideLeft', fill: '#666', fontSize: 12 } : undefined}
          />
          <Tooltip 
            labelFormatter={(label: any) => `Step: ${label}`}
            contentStyle={{
              backgroundColor: '#fff',
              border: '1px solid #ccc',
              borderRadius: '4px',
              fontSize: '12px',
              color: '#333'
            }}
            labelStyle={{ color: '#333' }}
            itemStyle={{ color: '#333' }}
          />
          {lines.map((line, index) => (
            <Line 
              key={line.dataKey || index}
              type="monotone" 
              data={data} 
              dataKey={line.dataKey} 
              name={line.name}
              stroke={line.stroke} 
              strokeWidth={line.strokeWidth || 2} 
              dot={false}
              activeDot={{ r: 4, stroke: line.stroke, strokeWidth: 2, fill: '#fff' }}
              connectNulls={true}
              // 실시간으로 계속 바뀌는 데이터라 애니메이션을 끈다. (병합으로 중간 스텝이 채워질 때 선이 흔들리지 않게)
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}