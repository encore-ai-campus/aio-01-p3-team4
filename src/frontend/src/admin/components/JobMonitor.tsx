import type { Api } from '../../api/client';
import type { useJobRun } from '../../hooks/useJobRun';
import type { useJobWatch } from '../hooks/useJobWatch';
import { hasIncident } from '../../lib/incidents';
import { narrate } from '../../lib/timeline';
import { opsStatus } from '../../lib/ops';
import { Pipeline } from './Pipeline';
import { IncidentBoard } from './IncidentBoard';
import { Timeline } from './Timeline';
import { TeamTalk } from './TeamTalk';

export function JobMonitor({ api, source, notice, detailLink = true }: {
  api: Api; source: ReturnType<typeof useJobRun> | ReturnType<typeof useJobWatch>; notice?: string | null; detailLink?: boolean;
}) {
  const run = 'phase' in source ? source : null, job = 'job' in source ? source.job : null;
  const phase = run?.phase ?? job?.status ?? 'idle', id = run?.jobId ?? job?.id;
  const running = phase === 'submitting' || phase === 'running', mail = run?.mail;
  const mailActive = mail?.status === 'sending' || mail?.status === 'queued';
  const end = job?.completed_at ?? (running ? new Date().toISOString() : source.events.at(-1)?.created_at ?? job?.created_at);
  const seconds = run?.elapsedSeconds ?? (job && end ? Math.max(0, (Date.parse(end) - Date.parse(job.created_at)) / 1000) : 0);
  const status = phase === 'submitting' ? '진행 중' : phase === 'error' ? '실패' : phase === 'idle' ? '대기' : opsStatus[phase];
  const { rows, stages, events } = source;
  return <article className="job-monitor"><header className="monitor-heading">
    <p className="monitor-text">“{run?.text ?? job?.text ?? '작업 정보를 기다리고 있어요'}”</p>
    <div className="monitor-meta"><span className={`ops-badge ${phase === 'submitting' ? 'running' : phase === 'error' ? 'failed' : phase}`}>{status}</span><span>{running ? '경과' : '총 소요'} {seconds.toFixed(1)}초</span><code>{id?.slice(0, 8) ?? '작업 생성 중'}</code>{detailLink && id && <a href={`#/jobs/${encodeURIComponent(id)}`}>작업 상세 →</a>}</div>
    {notice && <p className="notice">{notice}</p>}{source.error && <p className="danger" role="alert">{source.error}</p>}
    {mail && mail.status !== 'idle' && <p className={`monitor-mail ${mail.status}`} role="status">메일: {mail.status === 'sending' ? '발송 중' : mail.status === 'queued' ? '보낼 편지함 재시도 중' : mail.status === 'sent' ? `발송 완료${mail.receiptNo ? `(${mail.receiptNo})` : ''}` : `실패${mail.error ? ` · ${mail.error}` : ''}`}</p>}
  </header><Pipeline stages={stages} rows={rows} events={events} running={running} compact/>
    {hasIncident(rows) ? <IncidentBoard key={id ?? 'pending'} api={api} events={events} rows={rows} running={running || mailActive} settled={!running && !mailActive}/> : <p className="monitor-clear">장애 없음 · 정상 처리</p>}
    <div className="monitor-columns"><Timeline rows={rows} events={events}/><TeamTalk events={events} running={running || mailActive} summary={narrate(rows)}/></div>
  </article>;
}
