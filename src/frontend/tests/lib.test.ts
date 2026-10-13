import { expect, it, vi } from 'vitest';
import type { FailureCode, StageStatus, TimelineEvent } from '../src/api/types';
import { chaosCatalogue, exampleQuestions, failureNames, makeFlag } from '../src/lib/failures';
import { deriveRows, deriveStages, narrate } from '../src/lib/timeline';
import { teamTalk } from '../src/lib/talk';
import { replaySchedule } from '../src/lib/replay';

let nextId = 0;
function event(agent: TimelineEvent['agent'], status: StageStatus = 'success', failure: FailureCode | null = null,
  attempt = 1, milliseconds = 0): TimelineEvent {
  return { id: ++nextId, job_id: 'job-1', agent, event_type: status, attempt,
    payload: { task_id: 'job-1', author: agent, status, failure, incident_id: null, data: {} },
    created_at: new Date(milliseconds).toISOString() };
}

it('실패 뒤 첫 성공만 복구로 표시하고 창구·문구 검증과 종료 상태를 구별한다', () => {
  const events = [event('classifier', 'retry', 'LLM_OUTPUT_INVALID'), event('classifier', 'success', null, 2),
    event('classifier'), event('locator'), event('verifier', 'failed', 'LINK_DEAD'), event('locator', 'success', null, 2),
    event('verifier', 'success', null, 2), event('verifier', 'success', 'STALE_INFO', 3), event('writer'),
    event('verifier', 'retry', 'PII_IN_DRAFT'), event('writer', 'success', null, 2),
    event('verifier', 'success', null, 2), event('verifier', 'success', null, 3)];
  const rows = deriveRows(events);
  expect(rows.map(row => row.tone)).toEqual(['fail', 'recovered', 'ok', 'ok', 'fail', 'ok', 'recovered', 'warn', 'ok', 'fail', 'ok', 'recovered', 'ok']);
  expect(rows[1].note).toBe('자동 복구');
  expect(rows[5].note).toBe('다른 창구로 다시 찾기');
  expect(rows[4].stage).toBe('verify_channel');
  expect(rows[9].stage).toBe('verify_draft');
  expect(rows[0].failureName).toBe('AI 응답 형식이 깨짐');
  expect(deriveStages(rows.slice(0, 1)).map(stage => stage.status)).toEqual(['fail', 'pending', 'pending', 'pending', 'pending']);
  expect(deriveStages(rows.slice(0, 5), false).map(stage => stage.status)).toEqual(['done', 'active', 'fail', 'pending', 'pending']);
  expect(deriveStages(rows.slice(0, 6), false)[2].status).toBe('fail');
  expect(deriveStages(rows.slice(0, 7), false)[2].status).toBe('recovered');
  expect(deriveStages(rows.slice(0, 8)).map(stage => stage.status)).toEqual(['done', 'done', 'warn', 'active', 'pending']);
  expect(deriveStages(rows.slice(0, 12)).at(-1)?.status).toBe('recovered');
  expect(deriveStages(rows).every(stage => stage.status === 'done' || stage.status === 'warn')).toBe(true);
  expect(deriveRows([event('backend', 'failed', 'INJECTION_BLOCKED')])[0].stage).toBe('input');
  expect(deriveStages(deriveRows([event('backend', 'failed', 'INJECTION_BLOCKED')])).some(stage => stage.status === 'active')).toBe(false);
  expect(deriveStages(deriveRows([event('classifier', 'needs_user', 'NEEDS_MORE_INFO')])).some(stage => stage.status === 'active')).toBe(false);
  expect(deriveStages(rows.slice(0, 1), true).some(stage => stage.status === 'active')).toBe(false);
  expect(deriveStages([], false)[0].status).toBe('active');
  expect(failureNames.SERVICE_UNAVAILABLE).toBe('에이전트가 응답 없음');
  expect(exampleQuestions.map(question => question.label)).toEqual(['가로등', '도로', '쓰레기']);
  expect(exampleQuestions.every(question => question.text.includes('상도동'))).toBe(true);
  expect(chaosCatalogue.find(item => item.kind === 'delay')?.params).toEqual({ seconds: 20 });
  expect(chaosCatalogue.find(item => item.kind === 'injection')?.target).toBeUndefined();
  const flag = makeFlag('delay', { seconds: 20 });
  expect(flag.incident_id).toMatch(/^inc-/);
  expect(flag.injected_at).toMatch(/Z$/);
  expect(flag.params).toEqual({ seconds: 20 });
  // http 앱 서버처럼 crypto.randomUUID가 없는 환경에서도 장애 번호를 만든다
  vi.stubGlobal('crypto', {});
  expect(makeFlag('link_dead').incident_id).toMatch(/^inc-\d+-[a-z0-9]{8,}$/);
  vi.unstubAllGlobals();
});

it('리플레이 간격을 압축하고 양쪽 경계 및 사용자 옵션을 적용한다', () => {
  const events = [0, 100, 5100, 7100].map(at => event('classifier', 'success', null, 1, at));
  expect(replaySchedule(events)).toEqual([300, 750, 2250, 3250]);
  expect(replaySchedule(events, { firstMs: 0, scale: 1, minMs: 100, maxMs: 200 })).toEqual([0, 100, 300, 500]);
  expect(replaySchedule([events[1], events[0]])).toEqual([300, 750]);
  expect(replaySchedule([])).toEqual([]);
});

it.each([
  ['TimeoutError', 'AI 응답 시간 초과'], ['JSONDecodeError', 'AI 응답 형식이 깨짐'], [undefined, 'AI 응답 형식이 깨짐'],
])('실제 오류 %s를 타임라인과 대화 실패·조치에 반영한다', (error_type, label) => {
  const failed = event('writer', 'retry', 'LLM_OUTPUT_INVALID');
  failed.payload.data = { error_type };
  expect(deriveRows([failed])[0].failureName).toBe(label);
  expect(narrate(deriveRows([failed]))).toBe(error_type === 'TimeoutError'
    ? 'AI 응답 시간 초과로 같은 단계를 다시 시키고 있어요' : 'AI 답 모양이 깨져서 같은 단계를 다시 시키고 있어요');
  const talk = teamTalk([failed], { running: false });
  expect(talk.find(item => item.kind === 'fail')?.text).toBe(label);
  expect(talk.find(item => item.kind === 'decision')?.text).toContain(`${label} 감지`);
  failed.payload.failure = 'SERVICE_UNAVAILABLE';
  expect(deriveRows([failed])[0].failureName).toBe(failureNames.SERVICE_UNAVAILABLE);
  expect(chaosCatalogue.find(item => item.kind === 'pii_leak')?.target).toBe('writer');
});
