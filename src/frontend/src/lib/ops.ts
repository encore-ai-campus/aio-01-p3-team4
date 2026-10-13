import type { Incident, IncidentFault, OpsEvent, OpsHours } from '../api/types';
import { agentLabel, incidentFaultLabel } from './labels';
import { failureNames } from './failures';

export interface IncidentSummary {
  kind: IncidentFault; label: string; total: number; autoRecovered: number; blocked: number; degraded: number; avgRecoverySec: number | null;
}
export function metricsCohort(items: readonly (Incident & { metrics_since?: string })[], since?: string) {
  return items.filter(item => {
    const start = Date.parse(since ?? item.metrics_since ?? '2026-10-12T04:26:00Z');
    return (item.injected_at && item.detected_at !== null && Date.parse(item.injected_at) >= start) ||
      (item.result === 'blocked' && item.fault === 'INJECTION_BLOCKED' && item.detected_at !== null && Date.parse(item.detected_at) >= start);
  });
}
const completedAs = (item: Incident, result: string) => item.result === result && item.recovered_at !== null && !item.human_intervention;
function resultLabel(item: Incident): string {
  if (item.injected_at && !item.detected_at) return '미발생';
  if (item.result === 'blocked') return '차단';
  if (item.result === 'degraded') return '확인 필요 표시';
  if (item.human_intervention) return '사람 조치';
  if (completedAs(item, 'recovered')) return item.injected_at ? '자동 복구' : 'AI 자체 복구';
  return '미복구';
}
export function summarizeIncidents(incidents: readonly Incident[]): IncidentSummary[] {
  const groups = new Map<IncidentFault, Incident[]>();
  for (const incident of metricsCohort(incidents)) {
    const group = groups.get(incident.fault) ?? [];
    group.push(incident); groups.set(incident.fault, group);
  }
  return [...groups].map(([kind, group]) => {
    const recovered = group.filter(item => completedAs(item, 'recovered'));
    // 감지 시각이 없으면 복구 시간을 꾸며 내지 않는다.
    const seconds = recovered.flatMap(item => {
      if (!item.detected_at || !item.recovered_at) return [];
      const duration = (Date.parse(item.recovered_at) - Date.parse(item.detected_at)) / 1000;
      return Number.isFinite(duration) && duration >= 0 ? [duration] : [];
    });
    return { kind, label: incidentFaultLabel(kind), total: group.length, autoRecovered: recovered.length,
      blocked: group.filter(item => completedAs(item, 'blocked')).length, degraded: group.filter(item => item.result === 'degraded').length,
      avgRecoverySec: seconds.length ? seconds.reduce((sum, sec) => sum + sec, 0) / seconds.length : null };
  }).sort((a, b) => b.total - a.total);
}
export function recentIncidents(incidents: readonly Incident[], n = 8) {
  const time = (item: Incident) => Date.parse(item.detected_at ?? item.injected_at ?? '') || 0;
  return [...incidents].sort((a, b) => time(b) - time(a)).slice(0, Math.max(0, n)).map(item => ({
    ...item, targetLabel: agentLabel[item.target], kindLabel: incidentFaultLabel(item.fault),
    resultLabel: resultLabel(item),
  }));
}
export function ratePercent(rate: number): string { return `${Math.round(Math.min(1, Math.max(0, rate)) * 100)}%`; }

export const opsStatus = { success: '성공', retry: '재시도', failed: '실패', needs_user: '추가 질문', running: '진행 중' };
export const opsColors = { success: 'var(--ok)', retry: 'var(--warn)', failed: 'var(--fail)', needs_user: 'var(--accent)', running: 'var(--soft)' };
export const serviceLabels: Record<string, string> = { frontend: '화면', backend: '백엔드', classifier: '분류', locator: '창구 찾기', verifier: '검증', writer: '문구 작성', mailer: '가상 발송', chaos: '장애 스위치', data_pg: '데이터 서버(PG)', data_redis: '데이터 서버(Redis)' };
export function opsTime(at: string, date = false) {
  return new Date(at).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', ...(date ? { month: '2-digit', day: '2-digit' } : {}), hour: '2-digit', minute: '2-digit', hour12: false });
}
export function filterIncidents(items: readonly Incident[], hours: OpsHours, now: number): Incident[] {
  return items.filter(item => { const at = Date.parse(item.detected_at ?? item.injected_at ?? ''); return hours === 0 ? !Number.isFinite(at) || at <= now : at >= now - hours * 3600_000 && at <= now; });
}
export function incidentMetrics(items: readonly Incident[]) {
  items = metricsCohort(items);
  const recovered = items.filter(item => completedAs(item, 'recovered'));
  const handled = items.filter(item => !item.human_intervention && ['recovered', 'blocked', 'degraded'].includes(item.result ?? '')).length;
  const durations = recovered.flatMap(item => { const sec = (Date.parse(item.recovered_at!) - Date.parse(item.detected_at ?? '')) / 1000; return Number.isFinite(sec) && sec >= 0 ? [sec] : []; });
  return { total: items.length, rate: items.length ? handled / items.length * 100 : null,
    avg: durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : null };
}
export function activityLog(events: readonly OpsEvent[], incidents: readonly Incident[]) {
  const rows = events.map((event, index) => ({ id: `event-${event.job_id}-${index}`, at: event.at, agent: agentLabel[event.agent] ?? event.agent,
    kind: (event.status === 'failed' || event.status === 'retry' || event.failure && event.status !== 'needs_user' ? 'fault' : 'normal') as 'fault' | 'normal',
    tone: event.status, text: `${event.review ? '사람이 고친 문구 재검증 · ' : ''}${opsStatus[event.status]}${event.attempt > 1 ? ` ${event.attempt}회차` : ''}${event.failure ? ` · ${failureNames[event.failure as keyof typeof failureNames] ?? event.failure}` : ''}` }));
  return [...rows, ...incidents.flatMap(item => {
    const at = item.detected_at ?? item.injected_at;
    const label = incidentFaultLabel(item.fault), agent = agentLabel[item.target] ?? item.target;
    const detected = at ? [{ id: `incident-${item.id}`, at, agent, kind: 'fault' as const, tone: 'failed' as const, text: `${label} · ${item.detected_at ? '장애 감지' : '미발생'}` }] : [];
    const sec = item.detected_at && item.recovered_at ? (Date.parse(item.recovered_at) - Date.parse(item.detected_at)) / 1000 : null;
    return [...detected, ...(item.recovered_at ? [{ id: `recovery-${item.id}`, at: item.recovered_at, agent, kind: 'fault' as const, tone: item.result === 'degraded' ? 'needs_user' as const : 'success' as const,
      text: `${resultLabel(item)}${sec !== null && sec >= 0 ? ` ${sec.toFixed(1)}초` : ''} · ${label}` }] : [])];
  })].sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
}
