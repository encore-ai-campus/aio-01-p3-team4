import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from '../src/admin/App';
import { MockApi } from '../src/api/mock';
import type { Incident, TimelineEvent } from '../src/api/types';
import { IncidentBoard } from '../src/admin/components/IncidentBoard';
import { Pipeline } from '../src/admin/components/Pipeline';
import { HERO_EVENTS } from '../src/citizen/components/hero/scenario';
import { deriveRows, deriveStages } from '../src/lib/timeline';
import { incidentCards, incidentState, rerouteTarget } from '../src/lib/incidents';
import { chaosCatalogue, exampleQuestions, makeFlag } from '../src/lib/failures';

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });
const board = (api: MockApi, events: TimelineEvent[], running = true) => <IncidentBoard api={api} events={events} rows={deriveRows(events)} running={running} settled={!running}/>;
async function submit(api: MockApi) {
  vi.spyOn(api, 'getOpsOverview').mockResolvedValue(await new MockApi().getOpsOverview());
  window.history.replaceState({}, '', '/admin/#/lab');
  render(<App api={api}/>);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '공격 문구 입력하기' })); });
  fireEvent.change(screen.getByLabelText('공격 문구'), { target: { value: exampleQuestions[0].text } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '보내기' })); });
  await act(async () => { window.dispatchEvent(new HashChangeEvent('hashchange')); });
  await act(async () => { await vi.advanceTimersByTimeAsync(12000); });
}

it('LINK_DEAD는 실패 단계·결정·우회 화살표와 감지→복구 소요를 남긴다', async () => {
  vi.useFakeTimers();
  const api = new MockApi(), events = HERO_EVENTS.slice(0, 3), rows = deriveRows(events);
  const view = render(<>{board(api, events)}<Pipeline stages={deriveStages(rows)} rows={rows} events={events} running/></>);
  const region = within(screen.getByRole('region', { name: '장애 관제' }));
  expect(region.getByText('검증')).toBeTruthy();
  expect(region.getByText('LINK_DEAD', { selector: 'code' })).toBeTruthy();
  expect(region.getByText('그 창구 빼고 다시 찾기')).toBeTruthy();
  expect(region.getByText('창구 찾기 2회차')).toBeTruthy();
  expect(document.querySelector('svg.reroute[data-from="verify_channel"][data-to="locate"][data-state="active"]')).toBeTruthy();
  expect(document.querySelector('.pipeline li.fail .station-dot[data-pulse="on"]')).toBeTruthy();
  expect(document.querySelector('.incident-card[data-agent="verifier"][data-stage="verify_channel"][data-failure="LINK_DEAD"]')).toBeTruthy();
  expect(document.querySelector('.incident-state')?.textContent).toBe('감지됨');
  await act(async () => { await vi.advanceTimersByTimeAsync(710); });
  expect(document.querySelector('.incident-state')?.textContent).toBe('복구 중');
  const recoveredRows = deriveRows(HERO_EVENTS);
  await act(async () => { view.rerender(<>{board(api, HERO_EVENTS, false)}<Pipeline stages={deriveStages(recoveredRows)} rows={recoveredRows} events={HERO_EVENTS} running={false} compact/></>); });
  expect(document.querySelector('.incident-state')?.textContent).toBe('자동 복구 · 사람 손 없이');
  expect(region.getByText(/4\.2초/)).toBeTruthy();
  expect(document.querySelector('.reroute.recovered')).toBeTruthy();
  expect(document.querySelector('[data-stage="verify_channel"] .station-dot')?.textContent).toBe('✓');
});

it('완료 후 보드는 타임라인 위에 남고 이벤트 수와 DB 식별자가 일치한다', async () => {
  vi.useFakeTimers(); const api = new MockApi(), getIncidents = vi.spyOn(api, 'getIncidents');
  await api.setChaosFlag('verifier', makeFlag('link_dead'));
  await submit(api);
  const region = screen.getByRole('region', { name: '장애 관제' });
  const events = await api.getTimeline('job-mock-1');
  expect(region.querySelectorAll('table.handoff-ledger tr[data-event-id]')).toHaveLength(events.length);
  for (const event of events) expect(within(region).getByText(`#${event.id}`)).toBeTruthy();
  expect(region.querySelectorAll('tbody td:nth-child(4)')).toHaveLength(events.length);
  expect(region.textContent).toContain('✓ incidents 기록');
  expect(region.textContent).toMatch(/감지 \d{2}:\d{2}:\d{2}/);
  expect(getIncidents).toHaveBeenCalledTimes(1);
  expect(region.querySelectorAll('td.ledger-db[data-ok="true"]')).toHaveLength(events.length);
  expect(region.matches('section.incident-board[data-board="incident"]')).toBe(true);
  const columns = document.querySelector('.monitor-columns')!;
  expect(columns.querySelectorAll('.timeline .log-row[data-tone]')).toHaveLength(events.length);
  expect(document.querySelector('.monitor-meta .ops-badge')?.textContent).toBe('성공');
  expect(region.compareDocumentPosition(columns) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  for (const hash of ['#/lab', '#/live']) {
    await act(async () => { window.location.hash = hash; window.dispatchEvent(new HashChangeEvent('hashchange')); });
  }
  expect(screen.getByRole('region', { name: '장애 관제' }).querySelectorAll('td.ledger-db[data-ok="true"]')).toHaveLength(events.length);
});

it('장애 없는 실행은 보드와 장애 조회 없이 정상 처리로 표시한다', async () => {
  vi.useFakeTimers(); const api = new MockApi(), getIncidents = vi.spyOn(api, 'getIncidents');
  await submit(api);
  expect(screen.queryByRole('region', { name: '장애 관제' })).toBeNull();
  expect(screen.getByText('장애 없음 · 정상 처리')).toBeTruthy();
  expect(document.querySelectorAll('.timeline .log-row[data-tone]').length).toBeGreaterThan(0);
  expect(getIncidents).not.toHaveBeenCalled();
});

it('재시도는 한 카드로 묶고 우회·다른 문구의 성공을 원래 단계 복구로 오인하지 않는다', () => {
  const original = structuredClone(HERO_EVENTS.slice(0, 3));
  original[2].payload.incident_id = 'inc-a';
  const repeat = { ...structuredClone(original[2]), id: 80, attempt: 2 };
  const unrelated = { ...structuredClone(HERO_EVENTS[4]), id: 81, payload: { ...HERO_EVENTS[4].payload, index: 1 } };
  const events = [...original, repeat, HERO_EVENTS[3], unrelated];
  let cards = incidentCards(events, deriveRows(events));
  expect(cards).toHaveLength(1); expect(cards[0].recovery).toBeUndefined(); expect(cards[0].attempts).toBe(2);
  cards = incidentCards([...events, HERO_EVENTS[4]], deriveRows([...events, HERO_EVENTS[4]]));
  expect(cards[0].recovery?.id).toBe(HERO_EVENTS[4].id);
  const row = deriveRows(original)[2];
  for (const [failure, target] of [['PII_IN_DRAFT', 'write'], ['DRAFT_MISMATCH', 'write'], ['NEEDS_MORE_INFO', 'user'], ['MAIL_SEND_FAILED', 'outbox'], ['STALE_INFO', 'warn'], ['INJECTION_BLOCKED', 'blocked'], ['LLM_OUTPUT_INVALID', 'verify_channel'], ['SERVICE_UNAVAILABLE', 'verify_channel']] as const) {
    expect(rerouteTarget({ ...row, failure, stage: failure === 'MAIL_SEND_FAILED' ? 'send' : row.stage })).toBe(target);
  }
  expect(incidentState({ ...cards[0], target: 'warn' }, false).label).toBe('확인 필요');
  expect(incidentState({ ...cards[0], target: 'blocked' }, false).label).toBe('차단');
  expect(incidentState({ ...cards[0], human: true }, false).label).toBe('사람 확인 후 재개');
});

it('장애 기록 조회 실패·다른 작업 기록은 DB 확인으로 표시하지 않는다', async () => {
  const api = new MockApi(), events = structuredClone(HERO_EVENTS);
  events[2].payload.incident_id = 'inc-a';
  vi.spyOn(api, 'getIncidents').mockRejectedValueOnce(new Error('offline'));
  const view = await act(async () => render(board(api, events, false)));
  expect(screen.getByText('확인 불가')).toBeTruthy();
  expect(screen.queryByText('✓ incidents 기록')).toBeNull();
  view.unmount();
  vi.mocked(api.getIncidents).mockResolvedValueOnce([{ id: 'inc-a', job_id: 'another-job' } as Incident]);
  await act(async () => { render(board(api, events, false)); });
  expect(screen.getByText('일치 기록 없음')).toBeTruthy();
});

it('MAIL_SEND_FAILED 발송→보낼 편지함 경로는 시간을 갱신하고 재발송 후 복구된다', async () => {
  vi.useFakeTimers(); const api = new MockApi();
  const job = await api.createJob(exampleQuestions[0].text);
  await api.setChaosFlag('mailer', makeFlag('mail_down'));
  await api.sendJob(job.id, { title: '가상 발송 확인', body: '가로등 확인 요청' });
  await vi.advanceTimersByTimeAsync(800);
  const events = await api.getTimeline(job.id), rows = deriveRows(events);
  const view = render(<>{board(api, events)}<Pipeline stages={deriveStages(rows)} rows={rows} events={events} running={false} compact/></>);
  expect(screen.getByText('경과 0.0초')).toBeTruthy();
  expect(document.querySelector('svg.reroute[data-from="send"][data-to="outbox"][data-state="active"]')).toBeTruthy();
  expect(document.querySelector('.incident-card[data-agent="mailer"][data-stage="send"][data-failure="MAIL_SEND_FAILED"]')).toBeTruthy();
  expect(screen.getByText('보낼 편지함 2회차')).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(screen.getByText('경과 2.0초')).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(1200); });
  const recovered = await api.getTimeline(job.id), recoveredRows = deriveRows(recovered);
  await act(async () => { view.rerender(<>{board(api, recovered, false)}<Pipeline stages={deriveStages(recoveredRows)} rows={recoveredRows} events={recovered} running={false} compact/></>); });
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(screen.getByText(/3\.2초/)).toBeTruthy();
  expect(document.querySelector('.incident-status[data-state="recovered"]')).toBeTruthy();
  expect(document.querySelector('svg.reroute[data-from="send"][data-to="outbox"][data-state="recovered"]')).toBeTruthy();
  expect(vi.getTimerCount()).toBe(0);
});

it.each([
  ['llm_invalid', /분류 결과를 문장으로 설명합니다.*JSONDecodeError/, /mock-fixture.*streetlight.*상도동/],
  ['link_dead', /국민신문고.*https:.*연결 실패/, /안전신문고.*https:.*검증 통과/],
  ['not_official', /https:\/\/example.com\/.*비공식 주소/, /안전신문고.*검증 통과/],
  ['pii_leak', /연락처 010-1234-5678 \(실험용 가짜 번호\)/, /연락처 \[개인정보 삭제\].*지운 항목: 전화번호/],
  ['delay', /지연 설정 20초.*1.4초.*SERVICE_UNAVAILABLE/, /재호출 성공까지 15초/],
] as const)('%s 카드는 실제 모의 실패→복구 증거를 제목과 함께 보이고 미래 결과를 숨긴다', async (kind, brokenPattern, fixedPattern) => {
  const api = new MockApi({ now: () => Date.parse('2026-10-12T00:00:00Z') });
  const selected = chaosCatalogue.find(item => item.kind === kind)!;
  await api.setChaosFlag(selected.target!, makeFlag(kind, selected.params));
  const job = await api.createJob(exampleQuestions[0].text), events = await api.getTimeline(job.id);
  const failure = events.findIndex(event => event.payload.failure !== null);
  const view = render(board(api, events.slice(0, failure + 1)));
  const values = () => [...document.querySelectorAll('.incident-card .incident-evidence dd')];
  expect(values()[0].textContent).toMatch(brokenPattern);
  expect(values()[0].getAttribute('title')).toBe(values()[0].textContent);
  expect(values()[1].textContent).toBe('복구 결과 대기');
  expect(values()[1].hasAttribute('title')).toBe(false);
  await act(async () => { view.rerender(board(api, events, false)); });
  expect(values()[0].textContent).toMatch(brokenPattern);
  expect(values()[1].textContent).toMatch(fixedPattern);
  expect(values()[1].getAttribute('title')).toBe(values()[1].textContent);
  expect(document.querySelectorAll('.incident-card')).toHaveLength(1);
});

it('8종 mock 증거는 fixture 출처를 표시하고 차단·확인 필요·메일 재발송의 실제 모의 값을 남긴다', async () => {
  for (const selected of chaosCatalogue) {
    let now = Date.parse('2026-10-12T00:00:00Z');
    const api = new MockApi({ now: () => now });
    if (selected.target) await api.setChaosFlag(selected.target, makeFlag(selected.kind, selected.params));
    const job = await api.createJob(selected.text);
    if (selected.kind === 'mail_down') {
      await api.sendJob(job.id, { title: '가상 발송', body: '가로등 확인 요청' });
      now += 4000;
    }
    const events = await api.getTimeline(job.id);
    const view = await act(async () => render(board(api, events, false)));
    const values = [...document.querySelectorAll('.incident-evidence dd')];
    expect(values).toHaveLength(2);
    for (const value of values) {
      expect(value.textContent).toMatch(/^\[mock\] /);
      expect(value.textContent!.length).toBeLessThanOrEqual(500);
      expect(value.getAttribute('title')).toBe(value.textContent);
    }
    if (selected.kind === 'injection') {
      expect(events).toHaveLength(1);
      expect(values[0].textContent).toContain('이전 지시는 무시');
      expect(values[1].textContent).toContain('차단 · 에이전트 호출 0');
    }
    if (selected.kind === 'stale_info') {
      expect(values[0].textContent).toContain('2020-01-01');
      expect(values[0].textContent).toMatch(/\d+일 전, 기준 90일/);
      expect(values[1].textContent).toContain('확인 필요 표시');
      expect(incidentCards(events, deriveRows(events))[0].recovery).toBeUndefined();
    }
    if (selected.kind === 'mail_down') {
      const sent = events.at(-1)!;
      expect(values[1].textContent).toContain(sent.created_at);
      expect(values[1].textContent).toContain(String(sent.payload.data.receipt_no));
    }
    if (selected.kind === 'delay') {
      const recovered = events.find(event => event.agent === 'classifier' && event.payload.status === 'success')!;
      recovered.payload.incident_id = 'separate-model-recovery';
      await act(async () => { view.rerender(board(api, [...events], false)); });
      expect(incidentCards(events, deriveRows(events))[0].recovery?.id).toBe(recovered.id);
      expect(document.querySelectorAll('.incident-evidence dd')[1].textContent).toContain('재호출 성공까지 15초');
      expect(document.querySelector('.incident-card.recovered')).toBeTruthy();
      recovered.payload.status = recovered.event_type = 'retry';
      recovered.payload.failure = 'LLM_OUTPUT_INVALID';
      recovered.payload.data.evidence = { fixed: '재호출 15초 · retry · LLM_OUTPUT_INVALID' };
      const failedEvents = events.slice(0, events.indexOf(recovered) + 1);
      await act(async () => { view.rerender(board(api, failedEvents, false)); });
      const delayed = document.querySelector('.incident-card[data-failure="SERVICE_UNAVAILABLE"]')!;
      expect(delayed.querySelectorAll('.incident-evidence dd')[1].textContent).toBe('재호출 15초 · retry · LLM_OUTPUT_INVALID');
      expect(incidentCards(failedEvents, deriveRows(failedEvents))[0].recovery).toBeUndefined();
      expect(delayed.classList.contains('blocked')).toBe(true);
      expect(delayed.textContent).not.toContain('자동 복구');
    }
    for (const event of events) delete event.payload.data.evidence;
    await act(async () => { view.rerender(board(api, events, false)); });
    expect(document.querySelectorAll('.incident-evidence dd')[0].textContent).toBe('증거 기록 없음');
    expect(document.querySelectorAll('.incident-evidence dd')[1].textContent).toBe('복구 증거 기록 없음');
    view.unmount();
  }
});
