import type { Incident } from '../../api/types';
import { opsTime, recentIncidents } from '../../lib/ops';

export function IncidentTable({ incidents, compact = false }: { incidents: Incident[]; compact?: boolean }) {
  const rows = recentIncidents(incidents, incidents.length);
  return <div className="table-scroll" tabIndex={0} role="region" aria-label="장애 기록 목록"><table className={`incidents-table${compact ? ' compact' : ''}`}><thead><tr><th>감지 시각</th><th>장애</th><th>대상 에이전트</th><th>감지→처리 소요</th><th>결과</th><th>사람 개입</th>{!compact && <th>작업</th>}<th>incident_id</th></tr></thead><tbody>{rows.map(item => {
    const seconds = item.detected_at && item.recovered_at ? (Date.parse(item.recovered_at) - Date.parse(item.detected_at)) / 1000 : null;
    const result = item.human_intervention ? 'human' : item.result;
    const tone = result === 'degraded' || result === 'human' ? 'needs_user' : result === 'injected' ? 'running' : result === 'recovered' || result === 'blocked' ? 'success' : 'failed';
    return <tr key={item.id} data-incident-id={item.id} data-result={result}><td>{item.detected_at ? <time dateTime={item.detected_at}>{opsTime(item.detected_at, true)}</time> : '—'}</td><td>{item.kindLabel}</td><td>{item.targetLabel ?? item.target}</td><td>{seconds !== null && Number.isFinite(seconds) && seconds >= 0 ? `${seconds.toFixed(1)}초` : '—'}</td><td><span className={`ops-badge ${tone}`}>{item.resultLabel}</span></td><td>{item.human_intervention ? '예' : '아니오'}</td>{!compact && <td>{item.job_id ? <a href={`#/jobs/${encodeURIComponent(item.job_id)}`}>{item.job_id.slice(0, 8)}</a> : '—'}</td>}<td><small><code>{item.id}</code></small></td></tr>;
  })}</tbody></table>{!rows.length && <p className="ops-empty">이 기간에는 장애가 없어요</p>}</div>;
}
