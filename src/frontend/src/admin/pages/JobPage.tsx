import { useEffect, useState } from 'react';
import type { Api } from '../../api/client';
import type { Incident } from '../../api/types';
import { agentLabel, categoryLabel, formatOffset, regionText } from '../../lib/labels';
import { useJobWatch } from '../hooks/useJobWatch';
import { JobMonitor } from '../components/JobMonitor';
import { IncidentTable } from '../components/IncidentTable';

export function JobPage({ api, id }: { api: Api; id: string }) {
  const watch = useJobWatch(api, id), { job, events } = watch;
  const [records, setRecords] = useState<{ id: string; items: Incident[]; error: boolean }>({ id, items: [], error: false });
  const latestIncident = events.findLast(event => event.payload.incident_id)?.id;
  useEffect(() => {
    if (!job) return;
    const controller = new AbortController();
    void api.getIncidents(controller.signal).then(items => {
      if (!controller.signal.aborted) setRecords({ id, items: items.filter(item => item.job_id === id), error: false });
    }).catch(() => { if (!controller.signal.aborted) setRecords({ id, items: [], error: true }); });
    return () => controller.abort();
  }, [api, id, job?.status, latestIncident]);
  const date = (at: string | null) => at ? new Date(at).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) : '—';
  return <main className="page job-page"><header className="page-heading"><h1>작업 상세</h1><a href="#/live">실시간 관제로 →</a></header>
    {watch.notFound ? <section className="panel"><p>이 작업의 상세 기록이 없어요</p><a href="#/ops">대시보드로</a></section> : watch.error ? <section className="panel danger" role="alert"><p>{watch.error}</p><button onClick={watch.reload}>재시도</button></section> : !job ? <p className="ops-empty">작업 기록을 불러오는 중</p> : <>
      <dl className="job-metadata"><div><dt>작업 id</dt><dd><code>{job.id}</code></dd></div><div><dt>생성 시각</dt><dd>{date(job.created_at)}</dd></div><div><dt>완료 시각</dt><dd>{date(job.completed_at)}</dd></div></dl>
      <JobMonitor api={api} source={watch} detailLink={false}/>
      {job.state.result && <section className="panel job-result"><h2>결과 요약</h2><dl><div><dt>유형</dt><dd>{categoryLabel[job.state.result.category]}</dd></div><div><dt>지역</dt><dd>{regionText(job.state.result.region)}</dd></div><div><dt>창구</dt><dd>{job.state.result.channels.map(channel => channel.name).join(' · ') || '—'}</dd></div></dl></section>}
      <section className="panel job-payload"><h2>단계별 payload</h2><div className="table-scroll" tabIndex={0} role="region" aria-label="단계별 payload 표"><table className="payload-table"><thead><tr>{['이벤트 id', '시각 오프셋', '에이전트', 'status', 'attempt', 'failure', 'incident_id', 'payload'].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{events.map(event => <tr key={event.id} data-event-id={event.id}>
        <td>{event.id}</td><td>{formatOffset(Date.parse(event.created_at) - Date.parse(events[0].created_at))}</td><td>{agentLabel[event.agent]}</td><td>{event.payload.status}</td><td>{event.attempt}</td><td>{event.payload.failure ?? '—'}</td><td><code>{event.payload.incident_id ?? '—'}</code></td><td><details><summary>payload</summary><pre>{JSON.stringify(event.payload, null, 2)}</pre></details></td>
      </tr>)}</tbody></table>{!events.length && <p className="ops-empty">단계별 기록을 기다리고 있어요</p>}</div></section>
      <section className="panel job-incidents"><h2>이 작업의 장애 기록</h2>{records.id === id && records.error ? <p className="danger" role="alert">장애 기록을 불러오지 못했어요</p> : <IncidentTable incidents={records.id === id ? records.items : []} compact/>}</section>
    </>}
  </main>;
}
