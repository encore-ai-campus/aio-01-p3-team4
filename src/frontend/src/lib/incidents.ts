import type { TimelineEvent } from '../api/types';
import type { TimelineRow } from './timeline';
import { stages } from './failures';

export const hasIncident = (rows: TimelineRow[]) => rows.some(row => row.tone === 'fail' || row.tone === 'warn' || row.failure);
export const stageLabel = (stage: string) => stages.find(item => item.id === stage)?.label
  ?? ({ input: '입력 확인', send: '가상 발송', outbox: '보낼 편지함', user: '사용자', blocked: '차단', warn: '확인 필요' }[stage] ?? stage);
export function rerouteTarget(row: TimelineRow): string {
  switch (row.failure) {
    case 'LINK_DEAD': case 'NOT_OFFICIAL': return 'locate';
    case 'PII_IN_DRAFT': case 'DRAFT_MISMATCH': return 'write';
    case 'NEEDS_MORE_INFO': return 'user';
    case 'MAIL_SEND_FAILED': return 'outbox';
    case 'INJECTION_BLOCKED': case 'MAIL_BLOCKED': return 'blocked';
    case 'STALE_INFO': case 'OUT_OF_SCOPE': case 'NO_CHANNEL': return 'warn';
    default: return row.stage;
  }
}
export function incidentCards(events: TimelineEvent[], rows: TimelineRow[]) {
  const cards: { key: string; row: TimelineRow; event: TimelineEvent; recovery?: TimelineEvent; evidenceEvent?: TimelineEvent;
    target: string; targetAttempt: number; attempts: number; human: boolean }[] = [];
  rows.forEach((row, index) => {
    if (!hasIncident([row])) return;
    const event = events[index], target = rerouteTarget(row);
    const existing = cards.find(card => card.event.payload.incident_id
      ? card.event.payload.incident_id === event.payload.incident_id && card.row.failure === row.failure && (card.event.payload.index ?? 0) === (event.payload.index ?? 0)
      : card.row.stage === row.stage && card.row.failure === row.failure && (!card.recovery || Date.parse(card.recovery.created_at) > Date.parse(row.at)));
    if (existing) return;
    // Resolved questions may continue downstream without another classifier event.
    const later = events.slice(index + 1).filter((item, offset) => (rows[index + offset + 1].stage === row.stage || row.status === 'needs_user' && row.tone === 'recovered')
      && (item.payload.index ?? 0) === (event.payload.index ?? 0)
      && (row.failure === 'SERVICE_UNAVAILABLE' || !item.payload.incident_id || !event.payload.incident_id || item.payload.incident_id === event.payload.incident_id));
    const recovery = ['blocked', 'warn'].includes(target) ? undefined
      : later.find(item => item.payload.status === 'success' && !item.payload.failure
        || row.tone === 'fail' && (row.failure === 'SERVICE_UNAVAILABLE' || !!event.payload.incident_id && item.payload.incident_id === event.payload.incident_id) &&
        (item.payload.status === 'needs_user' || item.payload.status === 'failed' && item.payload.failure === 'OUT_OF_SCOPE'));
    const through = events.slice(index, recovery ? events.indexOf(recovery) + 1 : undefined);
    const attempts = Math.max(...[event, ...through.filter(item => later.includes(item))].map(item =>
      Math.max(item.attempt, item.agent === 'mailer' && typeof item.payload.data.attempts === 'number' ? item.payload.data.attempts : 0)));
    const lastFailure = through.findLast(item => item.payload.failure === row.failure && item.agent === event.agent) ?? event;
    const priorTarget = rows.slice(0, events.indexOf(lastFailure) + 1).filter(item => item.stage === (target === 'outbox' ? 'send' : target));
    const evidenceEvent = row.failure === 'SERVICE_UNAVAILABLE' ? later.find(item => item.payload.failure !== 'SERVICE_UNAVAILABLE'
      && typeof (item.payload.data.evidence as { fixed?: unknown } | undefined)?.fixed === 'string') : undefined;
    cards.push({ key: `${event.payload.incident_id ?? `event-${event.id}`}:${row.failure}:${event.payload.index ?? 0}`, row, event, target, recovery, evidenceEvent, attempts,
      targetAttempt: Math.max(0, ...priorTarget.map(item => item.attempt)) + 1,
      human: target === 'user' || through.some(item => item.payload.review) });
  });
  return cards;
}
export type IncidentCard = ReturnType<typeof incidentCards>[number];
export function incidentEvidence(card: IncidentCard) {
  const read = (event: TimelineEvent | undefined, key: 'broken' | 'fixed') => {
    const evidence = event?.payload.data.evidence;
    if (!evidence || typeof evidence !== 'object') return undefined;
    const value = (evidence as Record<string, unknown>)[key];
    return typeof value === 'string' && value.trim() ? value : undefined;
  };
  return { broken: read(card.event, 'broken'),
    fixed: read(card.recovery, 'fixed') ?? read(card.evidenceEvent, 'fixed') ?? (['warn', 'blocked'].includes(card.target) ? read(card.event, 'fixed') : undefined) };
}
export function incidentState(card: IncidentCard, running: boolean) {
  if (card.target === 'blocked') return { tone: 'blocked', label: '차단' };
  if (card.target === 'warn') return { tone: 'warn', label: '확인 필요' };
  if (card.recovery) return { tone: card.human ? 'warn' : 'recovered', label: card.human ? '사람 확인 후 재개' : '자동 복구' };
  if (card.target === 'user') return { tone: 'warn', label: '확인 필요' };
  return { tone: running ? 'recovering' : 'blocked', label: running ? '복구 중' : '복구 중단' };
}
