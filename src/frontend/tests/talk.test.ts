import { expect, it } from 'vitest';
import { teamTalk } from '../src/lib/talk';
import { HERO_EVENTS } from '../src/citizen/components/hero/scenario';
import dead from '@contracts/samples/verifier_link_dead_output.json';
it('LINK_DEAD 대화는 계약 상세와 이전 이벤트를 근거로 인계·실패·조치·복구 순서를 만든다', () => {
  const items = teamTalk(HERO_EVENTS, { running: false });
  const fail = items.findIndex(item => item.kind === 'fail');
  expect(items[fail - 1]).toMatchObject({ kind: 'handoff', to: '창구 검증', text: '창구 2개 확인 부탁' });
  expect(items[fail].text).toBe(`링크가 안 열림 · ${dead.data.checks.find(c => !c.ok)!.detail}`);
  expect(items[fail + 1]).toMatchObject({ kind: 'decision', text: '링크가 안 열림 감지 → 그 창구 빼고 다시 찾기' });
  expect(items[fail + 2]).toMatchObject({ kind: 'handoff', to: '창구 찾기' });
  expect(items[fail + 2].text).toContain('가로등 고장 · 서울특별시 동작구 상도동');
  expect(items.filter(item => item.kind === 'recovered').map(item => item.text)).toEqual(['창구 검증 자동 복구 · 사람 손 없이']);
  expect(items.find(item => item.kind === 'handoff' && item.to === '문구 작성')?.text).toBe('안전신문고 신고 문구 부탁');
  expect(teamTalk(HERO_EVENTS.slice(0, 2), { running: true }).at(-1)?.text).toBe('epeople.go.kr 외 1곳 링크 여는 중');
});
