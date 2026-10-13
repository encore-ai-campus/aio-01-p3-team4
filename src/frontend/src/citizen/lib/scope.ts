import type { FinalResult, TimelineEvent } from '../../api/types';
import type { PipelineStage } from '../../lib/timeline';

export const isOutOfScope = (events: TimelineEvent[], final?: FinalResult | null, results: FinalResult[] = []) => {
  const result = events.findLast(event => event.agent === 'classifier')?.payload;
  return final?.category === 'out_of_scope' || results.some(item => item.category === 'out_of_scope') || result?.failure === 'OUT_OF_SCOPE' || result?.data.category === 'out_of_scope';
};
export function citizenStages(stages: PipelineStage[], outside: boolean): (Omit<PipelineStage, 'status'> & { status: PipelineStage['status'] | 'skipped' })[] {
  return stages.map(stage => !outside ? stage : stage.id === 'classify'
    ? { ...stage, label: '범위 밖 판정', status: 'warn' }
    : ['write', 'verify_draft'].includes(stage.id) ? { ...stage, status: 'skipped' }
    : { ...stage, label: '전국 공통 창구 확인' });
}
