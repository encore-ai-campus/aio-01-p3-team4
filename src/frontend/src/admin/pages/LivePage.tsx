import { useEffect, useState } from 'react';
import type { Api } from '../../api/client';
import type { OpsJob } from '../../api/types';
import type { useJobRun } from '../../hooks/useJobRun';
import { categoryLabel, regionText } from '../../lib/labels';
import { opsStatus, opsTime } from '../../lib/ops';
import { incidentCards } from '../../lib/incidents';
import { useJobWatch } from '../hooks/useJobWatch';
import { JobMonitor } from '../components/JobMonitor';
import { StageDots } from '../components/StageDots';

export function LivePage({ api, run, notice }: { api: Api; run: ReturnType<typeof useJobRun>; notice: string | null }) {
  const [list, setList] = useState<{ jobs: OpsJob[]; loading: boolean; error: string | null }>({ jobs: [], loading: true, error: null });
  const [manual, setManual] = useState<string | null>(null);
  const experiment = run.phase !== 'idle', submitting = run.phase === 'submitting';
  const terminal = ['success', 'needs_user', 'failed', 'error'].includes(run.phase) ? run.phase : null;
  useEffect(() => { setManual(null); }, [run.jobId, submitting]);
  useEffect(() => {
    let active = true, timer: ReturnType<typeof setTimeout> | undefined, deadline: ReturnType<typeof setTimeout> | undefined, controller: AbortController;
    const refresh = async () => {
      controller = new AbortController(); deadline = setTimeout(() => controller.abort(), 15_000);
      try {
        const overview = await api.getOpsOverview(0, controller.signal);
        if (active) setList({ jobs: overview.recent_jobs, loading: false, error: overview.errors.join(' · ') || null });
      } catch { if (active) setList(previous => ({ ...previous, loading: false, error: '작업 목록을 불러오지 못했어요. 30초 뒤 다시 확인해요.' })); }
      finally { clearTimeout(deadline); if (active) timer = setTimeout(() => { void refresh(); }, 30_000); }
    };
    void refresh();
    return () => { active = false; clearTimeout(timer); clearTimeout(deadline); controller?.abort(); };
  }, [api, run.jobId, terminal]);
  const jobs = [...list.jobs].sort((a, b) => Number(b.status === 'running') - Number(a.status === 'running') || Date.parse(b.created_at) - Date.parse(a.created_at));
  const selected = manual ?? (experiment ? '__experiment__' : jobs[0]?.id ?? null);
  const selectedExperiment = experiment && (selected === '__experiment__' || selected === run.jobId);
  const watch = useJobWatch(api, selectedExperiment ? null : selected);
  const pinned: OpsJob = { id: run.jobId ?? '__experiment__', created_at: run.events[0]?.created_at ?? new Date(run.startedAt ?? Date.now()).toISOString(), status: run.phase === 'success' || run.phase === 'needs_user' || run.phase === 'failed' ? run.phase : run.phase === 'error' ? 'failed' : 'running', duration_sec: run.elapsedSeconds,
    category: run.result?.category ?? null, region: run.result?.region ? regionText(run.result.region) : null, stages: run.events.map(event => ({ agent: event.agent, status: event.payload.status })), incident_count: incidentCards(run.events, run.rows).length };
  const card = (job: OpsJob, pinnedCard = false) => {
    const chosen = pinnedCard ? selectedExperiment : selected === job.id;
    return <button key={pinnedCard ? '__experiment__' : job.id} data-job-id={job.id} className={`live-job ${job.status}${chosen ? ' selected' : ''}${pinnedCard ? ' experiment' : ''}`} aria-pressed={chosen} onClick={() => setManual(pinnedCard ? '__experiment__' : job.id)}>
      <span className="live-job-top">{pinnedCard && <b>실험</b>}<time dateTime={job.created_at}>{opsTime(job.created_at, true)}</time><span className={`ops-badge ${job.status}`}>{opsStatus[job.status]}</span></span>
      <strong>{job.category ? categoryLabel[job.category] : '유형 확인 중'}</strong><span>{job.region ?? '지역 확인 중'}</span>
      <span className="live-job-meta"><StageDots job={job}/><span className={`incident-count${job.incident_count ? '' : ' none'}`}>장애 {job.incident_count}건</span><code>{job.id.slice(0, 8)}</code></span>
    </button>;
  };
  return <main className="page live"><header className="page-heading"><h1>실시간 관제</h1><p>작업을 선택하면 처리 단계와 장애 복구를 함께 확인해요.</p></header>
    <div className="live-layout"><aside className="live-list" aria-label="작업 목록"><h2>최근 작업 <small>30초마다 갱신</small></h2>{list.error && <p className="ops-banner" role="alert">{list.error}</p>}
      {experiment && card(pinned, true)}{jobs.filter(job => !experiment || job.id !== run.jobId).map(job => card(job))}
      {!jobs.length && <p className="ops-empty">{list.loading ? '작업 목록을 불러오는 중' : '최근 작업이 없어요'}</p>}
    </aside><section className="live-monitor" aria-label="선택 작업 관제">
      {selectedExperiment ? <JobMonitor api={api} source={run} notice={notice}/> : watch.notFound ? <p className="ops-empty">이 작업의 상세 기록이 없어요 <a href="#/ops">대시보드로</a></p> : watch.error ? <div className="ops-banner" role="alert">{watch.error} <button onClick={watch.reload}>재시도</button></div> : watch.job ? <JobMonitor api={api} source={watch}/> : <p className="ops-empty">{selected ? '작업 기록을 불러오는 중' : '관찰할 작업을 선택해 주세요'}</p>}
    </section></div>
  </main>;
}
