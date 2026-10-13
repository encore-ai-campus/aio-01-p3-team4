import { isOutOfScope } from './scope';
import type { FailureCode, TimelineEvent } from '../../api/types';
import type { TimelineRow } from '../../lib/timeline';
import type { useJobRun } from '../../hooks/useJobRun';
import { categoryLabel, regionText } from '../../lib/labels';
import { stages } from '../../lib/failures';

const pair = (doing: string, done = doing) => ({ doing, done });
const link = pair('창구 링크가 안 열려서 다른 창구를 찾고 있어요', '다른 창구를 찾아 확인된 창구로 안내했어요');
const response = pair('잠깐 응답이 늦어 다시 확인하고 있어요', '다시 확인해서 이어서 안내했어요');
const question = '몇 가지만 더 알려 주시면 이어서 찾을게요';
export const citizenFailures: Record<FailureCode, { doing: string; done: string }> = {
  LINK_DEAD: link, NOT_OFFICIAL: link,
  LLM_OUTPUT_INVALID: response, SERVICE_UNAVAILABLE: response,
  PII_IN_DRAFT: pair('문구에 개인정보가 보여 지우고 다시 쓰고 있어요', '개인정보를 지운 문구로 준비했어요'),
  DRAFT_MISMATCH: pair('문구를 민원 내용에 맞게 다시 쓰고 있어요', '민원 내용에 맞는 문구로 준비했어요'),
  STALE_INFO: pair('창구 정보가 오래되어 확인이 필요한 부분을 표시했어요'),
  MAIL_SEND_FAILED: pair('메일 서버가 잠깐 응답이 없어 다시 보내고 있어요', '다시 보내서 가상 담당자에게 도착했어요'),
  MAIL_BLOCKED: pair('실제 기관 주소라 보내지 않았어요. 가상 발송만 할 수 있어요'),
  INJECTION_BLOCKED: pair('안내에 쓸 수 없는 문구가 있어 멈췄어요'),
  NEEDS_MORE_INFO: pair(question, '추가 답변을 받아 이어서 찾고 있어요'),
  OUT_OF_SCOPE: pair('지원 범위 밖이라 전국 공통 창구만 확인하고 있어요', '전국 공통 창구를 안내만 했어요'),
  NO_CHANNEL: pair('확인된 부분까지만 안내해요'),
};
export const recoveredText = (stage: string) => ['locate', 'verify_channel'].includes(stage)
  ? '확인된 창구로 안내했어요' : stage === 'send' ? '가상 담당자에게 도착했어요' : '확인된 길로 안내했어요';

export function workingText(stage: string, events: TimelineEvent[]) {
  const data = events.findLast(event => event.agent === 'classifier' && event.payload.status === 'success')?.payload.data;
  const category = data?.category as keyof typeof categoryLabel | undefined;
  const region = data?.region;
  const context = [category && categoryLabel[category], region && typeof region === 'object'
    ? regionText(region as Parameters<typeof regionText>[0]) : ''].filter(Boolean).join(' · ');
  return ({ classify: '민원 내용을 읽고 있어요', locate: `${context ? `${context} ` : ''}신고 창구를 찾고 있어요`,
    verify_channel: '창구 링크가 잘 열리는지 확인하고 있어요', write: '신고 문구를 쓰고 있어요',
    verify_draft: '문구에 개인정보가 없는지 확인하고 있어요', send: '가상 담당자에게 보내고 있어요',
  } as Record<string, string>)[stage] ?? null;
}
export function nextStage(rows: TimelineRow[], sending = false): string | undefined {
  if (sending) return 'send';
  const last = rows.at(-1);
  if (!last) return 'classify';
  if (last.tone === 'fail') return last.failure === 'LINK_DEAD' || last.failure === 'NOT_OFFICIAL' ? 'locate'
    : last.failure === 'PII_IN_DRAFT' || last.failure === 'DRAFT_MISMATCH' ? 'write' : last.stage;
  return stages[stages.findIndex(stage => stage.id === last.stage) + 1]?.id;
}
type StatusRun = Pick<ReturnType<typeof useJobRun>, 'rows' | 'events' | 'phase' | 'mails'> & Partial<Pick<ReturnType<typeof useJobRun>, 'result' | 'results'>>;
export function citizenStatus({ rows, events, phase, mails, result, results }: StatusRun): string | null {
  if (phase === 'failed') return rows.some(row => row.failure === 'INJECTION_BLOCKED') ? citizenFailures.INJECTION_BLOCKED.doing : '안내를 마치지 못했어요';
  if (phase === 'error') return '연결을 확인해 주세요';
  if (phase === 'needs_user') return question;
  if (isOutOfScope(events, result, results) && !rows.some(row => row.failure === 'NO_CHANNEL')) {
    return phase === 'success' ? citizenFailures.OUT_OF_SCOPE.done : '전국 공통 창구만 확인하고 있어요. 문구 작성·문구 검증은 안 해요';
  }
  for (let index = rows.length - 1; index >= 0; index--) {
    const row = rows[index];
    if (row.tone === 'ok' && !row.failure || row.tone === 'ask') continue;
    const failure = row.tone === 'recovered'
      ? rows.slice(0, index).findLast(prior => prior.stage === row.stage && prior.tone === 'fail')?.failure : row.failure;
    if (failure) return citizenFailures[failure][row.tone === 'recovered' ? 'done' : 'doing'];
    break;
  }
  const sending = Object.values(mails).some(mail => ['sending', 'queued'].includes(mail.status));
  if (['running', 'submitting'].includes(phase) || sending) return workingText(nextStage(rows, sending) ?? '', events);
  return phase === 'success' ? '확인된 창구로 안내했어요' : null;
}
export function citizenError(message: string | null, fallback: string): string {
  if (!message) return fallback;
  if (/failed to fetch|networkerror|network request failed|load failed/i.test(message)) return '연결을 확인한 뒤 다시 시도해 주세요';
  if (Object.hasOwn(citizenFailures, message)) return citizenFailures[message as FailureCode].doing;
  return /[A-Z]+_[A-Z_]+|incident|총괄|classifier|locator|verifier|writer|mailer|에이전트 팀 대화/i.test(message) ? fallback : message;
}
