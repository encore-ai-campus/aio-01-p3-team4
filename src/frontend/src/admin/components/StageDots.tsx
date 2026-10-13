import type { OpsJob } from '../../api/types';
import { opsStatus, opsColors } from '../../lib/ops';
export function StageDots({ job }: { job: OpsJob }) {
  const firstWriter = job.stages.findIndex(stage => stage.agent === 'writer'), lastWriter = job.stages.findLastIndex(stage => stage.agent === 'writer');
  const values = [job.stages.filter(s => s.agent === 'classifier').at(-1), job.stages.filter(s => s.agent === 'locator').at(-1),
    job.stages.filter((s, i) => s.agent === 'verifier' && (firstWriter < 0 || i < firstWriter)).at(-1), job.stages[lastWriter],
    job.stages.filter((s, i) => s.agent === 'verifier' && lastWriter >= 0 && i > lastWriter).at(-1)];
  return <span className="ops-stage-dots" aria-label={values.map((stage, i) => `${['분류', '창구 찾기', '창구 검증', '문구 작성', '문구 검증'][i]} ${stage ? opsStatus[stage.status] : '대기'}`).join(', ')}>{values.map((stage, i) => <i key={i} style={{ background: stage ? opsColors[stage.status] : 'var(--line)' }} title={`${['분류', '창구 찾기', '창구 검증', '문구 작성', '문구 검증'][i]} · ${stage ? opsStatus[stage.status] : '대기'}`}/>)}</span>;
}
