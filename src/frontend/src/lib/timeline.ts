import type { FailureCode, StageStatus, TimelineEvent } from '../api/types';
import { stages, type StageId } from './failures';
import { failureLabel } from './labels';

export interface TimelineRow {
  id: TimelineEvent['id'];
  stage: StageId | 'input' | 'send';
  agent: TimelineEvent['agent'];
  status: StageStatus;
  attempt: number;
  failure: FailureCode | null;
  failureName: string | null;
  tone: 'fail' | 'recovered' | 'warn' | 'ask' | 'ok';
  note: string | null;
  at: string;
}
export type StageState = 'pending' | 'active' | 'fail' | 'recovered' | 'done' | 'warn';
export interface PipelineStage { id: StageId; label: string; status: StageState }

export function deriveRows(events: TimelineEvent[], finished = false): TimelineRow[] {
  let wrote = false;
  const failed = new Set<TimelineRow['stage']>();
  const rows = events.map((event): TimelineRow => {
    // 검증 출력에 모드가 없어서 앞선 작성 이벤트로 창구/문구 검증을 나눈다.
    const stage: TimelineRow['stage'] = event.agent === 'verifier' ? (wrote ? 'verify_draft' : 'verify_channel')
      : event.agent === 'classifier' ? 'classify' : event.agent === 'locator' ? 'locate'
        : event.agent === 'writer' ? 'write' : event.agent === 'mailer' ? 'send' : 'input';
    if (event.agent === 'writer') wrote = true;
    const { status, failure } = event.payload;
    let tone: TimelineRow['tone'] = 'ok';
    let note: string | null = event.agent === 'locator' && event.attempt >= 2 ? '다른 창구로 다시 찾기' : null;
    if (status === 'retry' || status === 'failed') { tone = 'fail'; failed.add(stage); }
    else if (status === 'needs_user') tone = 'ask';
    else if (failed.delete(stage)) { tone = 'recovered'; note = '자동 복구'; }
    else if (failure === 'STALE_INFO') tone = 'warn';
    if (event.payload.review) note = '사람이 고친 문구 재검증';
    return { id: event.id, stage, agent: event.agent, status, attempt: event.attempt,
      failure, failureName: failure ? failureLabel(failure, event.payload.data) : null, tone, note, at: event.created_at };
  });
  return rows.map((row, index) => finished && row.tone === 'ask' && rows.slice(index + 1).some((next, offset) =>
    next.status === 'success' && stages.findIndex(stage => stage.id === next.stage) > stages.findIndex(stage => stage.id === row.stage)
    && (events[index + offset + 1].payload.index ?? 0) === (events[index].payload.index ?? 0))
    ? { ...row, tone: 'recovered', note: '추가 질문 종료' } : row);
}

export function deriveStages(rows: TimelineRow[], finished?: boolean): PipelineStage[] {
  const last = rows.at(-1);
  const stopped = finished ?? (last?.stage === 'verify_draft' && last.status === 'success'
    || last?.stage === 'input' && last.status === 'failed' || last?.status === 'needs_user');
  const position = last ? stages.findIndex(stage => stage.id === last.stage) : -1;
  const retryStage = last?.tone === 'fail' ? (last.failure === 'LINK_DEAD' || last.failure === 'NOT_OFFICIAL' ? 'locate'
    : last.failure === 'PII_IN_DRAFT' || last.failure === 'DRAFT_MISMATCH' ? 'write' : last.stage) : null;
  const next = retryStage ? stages.findIndex(stage => stage.id === retryStage) : last ? (position < 0 ? -1 : last.status === 'needs_user' ? position : position + 1) : 0;
  return stages.map((stage, index) => {
    const row = rows.slice().reverse().find(item => item.stage === stage.id);
    let status: StageState = !row ? 'pending' : row.tone === 'ok' ? 'done'
      : row.tone === 'ask' ? 'active' : row.tone;
    if (stopped && status === 'active') status = 'pending';
    if (!stopped && index === next && status !== 'fail') status = 'active';
    return { ...stage, status };
  });
}

export function deriveSend(rows: readonly TimelineRow[]): 'idle' | 'sending' | 'fail' | 'recovered' | 'done' {
  const last = rows.filter(row => row.stage === 'send').at(-1);
  if (!last) return 'idle';
  return last.tone === 'fail' ? 'fail' : last.tone === 'recovered' ? 'recovered' : last.status === 'success' ? 'done' : 'sending';
}

// 배분서 §7의 총괄 조치를 화면에서 읽기 쉬운 말로 풀었다.
const narration: Record<FailureCode, { event: string; recovered: string }> = {
  MAIL_SEND_FAILED: { event: '메일 서버가 응답하지 않아 보낼 편지함에 넣고 다시 보내고 있어요', recovered: '보낼 편지함에서 다시 보내 담당자에게 도착했어요' },
  MAIL_BLOCKED: { event: '안전장치로 차단: 실제 기관 주소라 보내지 않았어요. 가상 발송만 허용돼요', recovered: '안전장치로 차단' },
  LINK_DEAD: { event: '창구 검증에서 링크가 열리지 않아 그 창구를 빼고 다시 찾고 있어요', recovered: '다른 창구로 다시 찾았고, 새 창구는 검증을 통과했어요' },
  NOT_OFFICIAL: { event: '공식 주소가 아닌 창구를 빼고 다시 찾고 있어요', recovered: '다른 창구로 다시 찾았고, 새 창구는 검증을 통과했어요' },
  LLM_OUTPUT_INVALID: { event: 'AI 답 모양이 깨져서 같은 단계를 다시 시키고 있어요', recovered: '다시 시켜서 정상 답을 받았어요' },
  PII_IN_DRAFT: { event: '문구에 개인정보가 섞여서 지우고 다시 쓰고 있어요', recovered: '개인정보를 지운 문구가 검증을 통과했어요' },
  SERVICE_UNAVAILABLE: { event: '에이전트가 응답하지 않아 잠깐 기다렸다가 다시 부르고 있어요', recovered: '다시 부른 에이전트가 정상적으로 응답했어요' },
  STALE_INFO: { event: '창구 확인일이 오래되어 안내에 확인 필요를 표시했어요', recovered: '창구 확인일이 오래되어 안내에 확인 필요를 표시했어요' },
  NEEDS_MORE_INFO: { event: '위치·시점이나 민원 유형을 더 알아야 해서 추가 질문을 드리고 있어요', recovered: '추가 답변을 받아 다음 단계로 진행하고 있어요' },
  INJECTION_BLOCKED: { event: '공격 문구를 감지해 차단하고 보안 기록을 남겼어요', recovered: '공격 문구를 감지해 차단하고 보안 기록을 남겼어요' },
  OUT_OF_SCOPE: { event: '지원 범위 밖이라 전국 공통 창구와 확인이 필요한 부분을 안내하고 있어요', recovered: '전국 공통 창구와 확인이 필요한 부분을 안내했어요' },
  NO_CHANNEL: { event: '전국까지 찾아도 창구가 없어 확인된 부분과 확인이 필요한 부분을 안내하고 있어요', recovered: '확인된 창구와 확인이 필요한 부분을 안내했어요' },
  DRAFT_MISMATCH: { event: '문구가 유형·창구와 맞지 않아 다시 쓰고 있어요', recovered: '다시 쓴 문구가 유형·창구 검증을 통과했어요' },
};
export function narrate(rows: readonly TimelineRow[]): string | null {
  for (let index = rows.length - 1; index >= 0; index--) {
    const row = rows[index];
    if (row.tone === 'ok') continue;
    const failure = row.tone === 'recovered'
      ? rows.slice(0, index).reverse().find(prior => prior.stage === row.stage && prior.tone === 'fail')?.failure
      : row.failure;
    if (row.tone !== 'recovered' && row.failureName === 'AI 응답 시간 초과') return 'AI 응답 시간 초과로 같은 단계를 다시 시키고 있어요';
    return failure ? narration[failure][row.tone === 'recovered' ? 'recovered' : 'event'] : null;
  }
  return null;
}
