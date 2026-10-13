import type { FailureCode, TimelineEvent } from '../api/types';
import { stages } from './failures';
import { agentLabel, categoryLabel } from './labels';
import { deriveRows, type TimelineRow } from './timeline';
export type TalkItem = { id: string; kind: 'handoff' | 'working' | 'report' | 'fail' | 'decision' | 'recovered'; from: string; to: string; text: string; tone: 'ok' | 'fail' | 'recovered' | 'warn' | 'ask' };
export const failureActions: Record<FailureCode, string> = {
  LINK_DEAD: '그 창구 빼고 다시 찾기', NOT_OFFICIAL: '그 창구 빼고 다시 찾기', LLM_OUTPUT_INVALID: '다시 → 다른 모델 → 규칙/템플릿',
  PII_IN_DRAFT: '문구 다시 쓰기', DRAFT_MISMATCH: '문구 다시 쓰기', SERVICE_UNAVAILABLE: '잠깐 기다렸다 다시',
  STALE_INFO: '통과시키되 확인 필요 표시', INJECTION_BLOCKED: '차단 + 기록', NEEDS_MORE_INFO: '사용자에게 추가 질문',
  OUT_OF_SCOPE: '전국 공통 창구 + 확인 필요', NO_CHANNEL: '확인된 데까지만 안내 + 확인 필요',
  MAIL_SEND_FAILED: '보낼 편지함에 넣고 다시 보내기', MAIL_BLOCKED: '발송 거부(안전장치)',
};
const record = (v: unknown): Record<string, unknown> => typeof v === 'object' && v !== null ? v as Record<string, unknown> : {};
const objects = (v: unknown) => Array.isArray(v) ? v.map(record) : [];
const str = (v: unknown) => typeof v === 'string' ? v : '';
const category = (v: unknown) => v === 'streetlight' || v === 'road_damage' || v === 'illegal_dumping' || v === 'out_of_scope' ? categoryLabel[v] : '';
const region = (v: unknown) => { const r = record(v); return [r.sido, r.sigungu, r.dong].map(str).filter(Boolean).join(' ') || str(r.place_text); };
const checkNames: Record<string, string> = { pii: '개인정보', category: '유형', region: '지역', links: '링크' };
export function teamTalk(events: readonly TimelineEvent[], { running }: { running: boolean }): TalkItem[] {
  const rows = deriveRows([...events]), items: TalkItem[] = [];
  let classification: Record<string, unknown> = {}, channels: Record<string, unknown>[] = [], title = '', passed: string[] = [];
  const context = () => [category(classification.category), region(classification.region)].filter(Boolean).join(' · ');
  const firstChannel = () => str(channels.find(c => passed.includes(str(c.channel_id)))?.name);
  const request = (stage: string, data: Record<string, unknown>) => stage === 'classify' ? '민원 문장 분류 부탁'
    : stage === 'locate' ? `${context()} 창구 찾기 부탁`.trim()
    : stage === 'verify_channel' ? `창구 ${channels.length}개 확인 부탁`
    : stage === 'write' ? `${firstChannel() || '확인된 내용으로'} 신고 문구 부탁`
    : stage === 'verify_draft' ? `‘${title}’ 검사 부탁`
    : stage === 'send' ? `${str(data.dept)} 앞으로 발송 부탁` : '입력 내용 검사 부탁';
  events.forEach((event, index) => {
    const row = rows[index], data = event.payload.data, checks = objects(data.checks);
    const label = stages.find(s => s.id === row.stage)?.label ?? agentLabel[event.agent];
    const add = (kind: TalkItem['kind'], text: string, tone = row.tone) => items.push({ id: `${event.id}-${kind}`, kind,
      from: kind === 'handoff' || kind === 'decision' || kind === 'recovered' ? '총괄' : label,
      to: kind === 'handoff' ? label : '총괄', text, tone });
    add('handoff', event.payload.review ? '사람이 고친 문구 재검증' : request(row.stage, data), 'ok');
    if (row.tone === 'fail') {
      const detail = str(checks.find(check => check.ok === false)?.detail);
      add('fail', [row.failureName, detail].filter(Boolean).join(' · ') || '실패');
    } else if (row.status === 'success') {
      const summary = row.stage === 'classify' ? `${category(data.category)} · ${region(data.region)}${typeof data.confidence === 'number' ? ` (확신도 ${data.confidence})` : ''}`
        : row.stage === 'locate' ? `창구 ${objects(data.channels).length}곳: ${objects(data.channels).map(c => str(c.name)).join('·')}`
        : row.stage === 'verify_channel' ? `${checks.filter(c => c.ok === true).length}/${checks.length} 통과 · 통과 창구 ${Array.isArray(data.passed_channel_ids) ? data.passed_channel_ids.length : 0}곳`
        : row.stage === 'write' ? `제목: ${str(data.title)}`
        : row.stage === 'verify_draft' ? `${checks.filter(c => c.ok === true).map(c => checkNames[str(c.name)] || str(c.name)).join('·')} 검사 통과`
        : row.stage === 'send' ? `${str(data.dept)} ${str(data.officer)}에게 보냄${str(data.receipt_no) ? ` · 접수번호 ${str(data.receipt_no)}` : ''}` : '입력 확인';
      add('report', summary);
    }
    if (row.failure) add('decision', `${row.failureName} 감지 → ${failureActions[row.failure]}`);
    if (row.tone === 'recovered') add('recovered', `${label} 자동 복구 · 사람 손 없이`);
    if (event.agent === 'classifier' && row.status === 'success') classification = data;
    if (event.agent === 'locator' && Array.isArray(data.channels)) channels = objects(data.channels);
    if (row.stage === 'verify_channel' && Array.isArray(data.passed_channel_ids)) passed = data.passed_channel_ids.filter((s): s is string => typeof s === 'string');
    if (event.agent === 'writer' && typeof data.title === 'string') title = data.title;
  });
  if (running) {
    const last = rows.at(-1), stageIndex = stages.findIndex(s => s.id === last?.stage);
    let next: TimelineRow['stage'] | undefined = last ? stages[stageIndex + 1]?.id : 'classify';
    if (last?.tone === 'fail') next = last.failure === 'LINK_DEAD' || last.failure === 'NOT_OFFICIAL' ? 'locate'
      : last.failure === 'PII_IN_DRAFT' || last.failure === 'DRAFT_MISMATCH' ? 'write' : last.stage;
    const hosts = channels.map(c => { try { return new URL(str(c.url)).hostname.replace(/^www\./, ''); } catch { return ''; } }).filter(Boolean);
    const target = next === 'classify' ? '민원 문장 읽는 중' : next === 'locate' ? `${context()} 창구 찾는 중`
      : next === 'verify_channel' ? `${hosts[0] ?? '창구'}${hosts.length > 1 ? ` 외 ${hosts.length - 1}곳` : ''} 링크 여는 중`
      : next === 'write' ? `${firstChannel() || '확인된 내용으로'} 신고 문구 쓰는 중`
      : next === 'verify_draft' ? `‘${title}’ 개인정보·유형 확인 중` : next === 'send' ? '보낼 편지함 재발송 중' : '';
    if (target) items.push({ id: `working-${events.at(-1)?.id ?? 0}`, kind: 'working', from: stages.find(s => s.id === next)?.label ?? '발송', to: '총괄', text: target, tone: 'ok' });
  }
  return items;
}
