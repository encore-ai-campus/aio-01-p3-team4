import { useEffect, useMemo, useState } from 'react';
import { Activity, Send } from 'lucide-react';
import type { Api } from '../../api/client';
import type { Incident, TimelineEvent } from '../../api/types';
import type { TimelineRow } from '../../lib/timeline';
import { incidentCards, incidentEvidence, incidentState, stageLabel } from '../../lib/incidents';
import { agentLabel, formatOffset } from '../../lib/labels';
import { stages } from '../../lib/failures';
import { failureActions, teamTalk } from '../../lib/talk';
import { stationIcons } from './Pipeline';

const time = (at: string | null) => {
  if (!at) return '—';
  const date = new Date(at);
  return [date.getHours(), date.getMinutes(), date.getSeconds()].map(value => String(value).padStart(2, '0')).join(':');
};
export function IncidentBoard({ api, events, rows, running, settled }: {
  api: Api; events: TimelineEvent[]; rows: TimelineRow[]; running: boolean; settled: boolean;
}) {
  const cards = incidentCards(events, rows), latestFailure = rows.findLast(row => row.failure || row.tone === 'fail')?.id;
  const [now, setNow] = useState(Date.now), [detected, setDetected] = useState<number | null>(null);
  const [records, setRecords] = useState<Incident[] | null>(null), [unavailable, setUnavailable] = useState(false);
  const jobId = events[0]?.job_id;
  const reports = useMemo(() => new Map(teamTalk(events, { running: false })
    .filter(item => item.kind === 'report' || item.kind === 'fail').map(item => [Number(item.id.split('-')[0]), item])), [events]);
  useEffect(() => {
    if (latestFailure === undefined) return;
    setDetected(latestFailure);
    const timer = setTimeout(() => setDetected(null), 700);
    return () => clearTimeout(timer);
  }, [latestFailure]);
  const ticking = running && cards.some(card => !card.recovery && !['warn', 'blocked'].includes(card.target));
  useEffect(() => {
    if (!ticking) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [ticking]);
  useEffect(() => {
    if (!settled || !jobId) return;
    const controller = new AbortController();
    setRecords(null); setUnavailable(false);
    void api.getIncidents(controller.signal).then(items => {
      if (!controller.signal.aborted) setRecords(items.filter(item => item.job_id === jobId));
    }).catch(() => { if (!controller.signal.aborted) setUnavailable(true); });
    return () => controller.abort();
  }, [api, jobId, settled]);
  const states = cards.map(card => incidentState(card, running));
  const state = states.find(item => item.tone === 'blocked') ?? states.find(item => item.tone === 'recovering')
    ?? states.find(item => item.tone === 'warn') ?? states[0];
  const human = cards.some(card => card.human) || records?.some(item => item.human_intervention);
  const label = detected !== null ? '감지됨' : human && state?.tone === 'recovered' ? '사람 확인 후 재개' : state?.label ?? '장애 감지 대기';
  const tone = detected !== null ? 'detected' : state?.tone ?? 'waiting';
  const offset = (at: string) => formatOffset(Date.parse(at) - Date.parse(events[0].created_at));
  if (!cards.length) return null;
  return <section className="incident-board panel" data-board="incident" role="region" aria-label="장애 관제">
    <header><h2><Activity aria-hidden="true"/>장애 관제 보드</h2><span>{cards.length}건</span></header>
    <p key={detected ?? tone} className={`incident-state incident-status ${tone}`} data-state={tone} aria-live="polite"><i aria-hidden="true"/>{label}
      {label === '자동 복구' && (human ? ' · 사람 확인 포함' : ' · 사람 손 없이')}</p>
    <div className="incident-cards">{cards.map(card => {
      const { row, event, recovery, target } = card;
      const Icon = stationIcons[stages.findIndex(stage => stage.id === row.stage)] ?? (row.stage === 'send' ? Send : Activity);
      const duration = ((Math.max(Date.parse(row.at), recovery ? Date.parse(recovery.created_at) : running ? now : Date.parse(events.at(-1)!.created_at)) - Date.parse(row.at)) / 1000).toFixed(1);
      const result = incidentState(card, running);
      const evidence = incidentEvidence(card);
      return <article key={card.key} className={`incident-card ${result.tone}`} data-agent={row.agent} data-stage={row.stage} data-failure={row.failure}><h3><Icon aria-hidden="true"/>{agentLabel[row.agent]} <small>{stageLabel(row.stage)}</small></h3>
        <p><code>{row.failure ?? row.status}</code> · {row.failureName ?? '실패'}</p>
        <dl><div><dt>감지 시각</dt><dd>{offset(row.at)}</dd></div>
          <div><dt>총괄 결정</dt><dd>{row.failure ? failureActions[row.failure] : '상태 확인'}</dd></div>
          <div><dt>우회 대상</dt><dd>{stageLabel(target)}{!['user', 'blocked', 'warn'].includes(target) && ` ${card.targetAttempt}회차`}</dd></div>
          <div><dt>시도 횟수</dt><dd>{card.attempts}회</dd></div>
          <div><dt>복구 시각 · 소요</dt><dd>{recovery ? `${offset(recovery.created_at)} · ${duration}초` : ['warn', 'blocked'].includes(target) ? '해당 없음' : `${running ? '경과' : '중단까지'} ${duration}초`}</dd></div>
          <div><dt>결과</dt><dd>{result.label}</dd></div>
          <div className="incident-evidence"><dt>망가진 것</dt><dd title={evidence.broken}>{evidence.broken ?? '증거 기록 없음'}</dd></div>
          <div className="incident-evidence"><dt>고친 결과</dt><dd title={evidence.fixed}>{evidence.fixed ?? (running && !recovery ? '복구 결과 대기' : '복구 증거 기록 없음')}</dd></div></dl>
        <small className="incident-id">incident_id · <code>{event.payload.incident_id ?? '없음'}</code></small>
      </article>;
    })}</div>
    <h3 className="transfer-heading">데이터 전달 점검표</h3>
    <div className="transfer-scroll" tabIndex={0} role="region" aria-label="데이터 전달 점검표 가로 스크롤"><table className="transfer-table handoff-ledger"><thead><tr>{['순서', '보낸 쪽 → 받는 쪽', '넘긴 것', '계약', 'DB'].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead>
      <tbody>{events.map((event, index) => {
        const report = reports.get(event.id), incidentId = event.payload?.incident_id;
        const summary = report?.text || rows[index]?.failureName || '추가 답변 요청';
        const record = records?.find(item => item.id === incidentId);
        const contract = event.payload && ['success', 'retry', 'failed', 'needs_user'].includes(event.payload.status);
        return <tr key={event.id} data-event-id={event.id}><td>{index + 1}</td><td>{report ? `${report.from} → ${report.to}` : `${agentLabel[event.agent]} → 총괄`}</td>
          <td title={summary}><span aria-label={summary}>{summary.length > 20 ? `${summary.slice(0, 19)}…` : summary}</span></td><td>{contract ? '✓ 계약 형식' : '확인 필요'}</td>
          <td className="ledger-db" data-ok={!incidentId || record ? 'true' : unavailable || records ? 'false' : 'pending'}><span>✓ 이벤트 <code>#{event.id}</code> 저장</span>{incidentId && <small>{record ? <>✓ incidents 기록<br/>감지 {time(record.detected_at)}<br/>복구 {time(record.recovered_at)}</> : unavailable ? '확인 불가' : records ? '일치 기록 없음' : '기록 확인 중…'}</small>}</td></tr>;
      })}</tbody></table></div>
  </section>;
}
