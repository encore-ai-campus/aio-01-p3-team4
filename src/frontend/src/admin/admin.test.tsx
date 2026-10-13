import { act, cleanup, fireEvent, render, renderHook, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import { useJobWatch } from './hooks/useJobWatch';
import { MockApi } from '../api/mock';
import { ApiError } from '../api/client';
import type { Job } from '../api/types';
import { exampleQuestions } from '../lib/failures';
const now = Date.parse('2026-10-12T12:00:00Z');
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(now); window.history.replaceState({}, '', '/#/'); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });
async function go(hash: string) { await act(async () => { window.history.replaceState({}, '', `/${hash}`); window.dispatchEvent(new HashChangeEvent('hashchange')); }); }
async function click(name: string) { await act(async () => { fireEvent.click(screen.getByRole('button', { name })); }); }
async function advance(ms = 12000) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
async function mount(api = new MockApi({ now: () => Date.now(), opsDemo: true })) { return act(async () => render(<App api={api}/>)); }
it('해시 라우트와 현재 탭은 기본값·상세·잘못된 경로를 구분한다', async () => {
  const api = new MockApi(), job = await api.createJob(exampleQuestions[0].text);
  await mount(api); expect(screen.getByRole('heading', { name: '관제 대시보드' })).toBeTruthy();
  for (const [path, title] of [['#/incidents', '장애 기록'], ['#/lab', '장애 실험실'], ['#/live', '실시간 관제']]) {
    await go(path); expect(screen.getByRole('heading', { name: title })).toBeTruthy();
    expect(screen.getByRole('link', { name: title }).getAttribute('aria-current')).toBe('page');
  }
  await go(`#/jobs/${encodeURIComponent(job.id)}`); expect(screen.getByRole('heading', { name: '작업 상세' })).toBeTruthy();
  for (const hash of ['#/unknown', '#/jobs/%E0%A4%A', '#/']) { await go(hash); expect(screen.getByRole('heading', { name: '관제 대시보드' })).toBeTruthy(); }
  expect(screen.getByRole('link', { name: '시민 화면' }).getAttribute('href')).toBe('/');
});
it('대시보드는 기존 숫자·서비스·단계 점과 작업 링크를 유지한다', async () => {
  const api = new MockApi({ now: () => now, opsDemo: true }), overview = vi.spyOn(api, 'getOpsOverview');
  const { container, unmount } = await mount(api);
  expect(container.querySelector('.ops-stats dd')?.textContent).toBe('184건');
  expect(screen.getByText('10/10 가동 중')).toBeTruthy();
  expect(container.querySelectorAll('.ops-stage-dots')).toHaveLength(30);
  expect(container.querySelector('a[href="#/jobs/demo-0"]')).toBeTruthy();
  await click('24시간'); expect(container.querySelector('.ops-stats dd')?.textContent).not.toBe('184건');
  await advance(30000); expect(overview).toHaveBeenLastCalledWith(24, expect.any(AbortSignal));
  const signal = overview.mock.calls.at(-1)?.[1]; unmount(); expect(signal?.aborted).toBe(true);
});
it('실시간 목록에서 실제 작업을 선택하면 저장된 타임라인을 표시한다', async () => {
  const api = new MockApi({ now: () => now, opsDemo: true }), job = await api.createJob(exampleQuestions[0].text);
  await go('#/live'); const { container } = await mount(api);
  expect(container.querySelectorAll('.ops-stage-dots').length).toBeGreaterThan(1);
  const button = container.querySelector<HTMLButtonElement>(`button[data-job-id="${job.id}"]`)!;
  expect(button).toBeTruthy(); await act(async () => { fireEvent.click(button); });
  expect(container.querySelectorAll('.timeline .log-row[data-tone]').length).toBeGreaterThan(0);
  expect(screen.getByText('장애 없음 · 정상 처리')).toBeTruthy();
});
it('장애 실험은 live로 이동하고 우회·복구·DB 점검 셀렉터를 남긴다', async () => {
  await go('#/lab'); const { container } = await mount(); await click('신고 링크 죽음 일으키기');
  expect(window.location.hash).toBe('#/live'); await go('#/lab');
  expect(screen.getByRole('button', { name: '신고 링크 죽음 일으키기' }).hasAttribute('disabled')).toBe(true);
  await go('#/live'); await advance(); await advance(800);
  const board = container.querySelector('.incident-board[data-board="incident"]')!;
  expect(board.querySelector('[data-agent="verifier"][data-stage="verify_channel"][data-failure="LINK_DEAD"]')).toBeTruthy();
  expect(board.querySelector('[data-state="recovered"]')).toBeTruthy();
  expect(board.querySelectorAll('tr[data-event-id]').length).toBeGreaterThan(0);
  expect(board.querySelectorAll('td.ledger-db[data-ok="true"]').length).toBeGreaterThan(0);
  expect(container.querySelector('.reroute[data-from="verify_channel"][data-to="locate"]')).toBeTruthy();
});
it('공격 문구 폼은 미리 채워지고 수정한 본문을 제출해 차단 기록을 보여준다', async () => {
  const api = new MockApi(), create = vi.spyOn(api, 'createJob');
  await go('#/lab'); const { container } = await mount(api); await click('공격 문구 입력하기');
  const input = container.querySelector<HTMLTextAreaElement>('form.lab-inject textarea')!;
  expect(input.value).toContain('이전 지시는 무시');
  const text = `${input.value} 수정한 문구`; fireEvent.change(input, { target: { value: text } }); await click('보내기');
  expect(create).toHaveBeenCalledWith(text); expect(window.location.hash).toBe('#/live'); await advance();
  expect(container.querySelector('.timeline .log-row[data-tone="fail"]')?.textContent).toContain('INJECTION_BLOCKED');
});
it('장애 기록은 최신순 표·작업 링크·결과와 기간별 수치를 표시한다', async () => {
  const api = new MockApi({ now: () => now, opsDemo: true });
  const items = await api.getIncidents(); await go('#/incidents'); const { container } = await mount(api);
  expect(container.querySelectorAll('tr[data-incident-id]')).toHaveLength(items.length);
  expect(container.querySelector('a[href="#/jobs/demo-0"]')).toBeTruthy();
  const table = within(container.querySelector('table')!);
  expect(table.getAllByText('자동 복구').length).toBeGreaterThan(0); expect(table.getAllByText('미복구').length).toBeGreaterThan(0);
  await click('24시간'); expect(container.querySelectorAll('tr[data-incident-id]').length).toBeLessThan(items.length);
  expect(container.querySelector('tr[data-incident-id][data-result]')).toBeTruthy();
});
it('실험실은 장애 7종과 공격 문구·레드팀 접근성 이름을 유지한다', async () => {
  const api = new MockApi(), red = vi.spyOn(api, 'runRedTeam'); await go('#/lab'); await mount(api);
  expect(screen.getAllByRole('button', { name: / 일으키기$/ })).toHaveLength(7);
  expect(screen.getByRole('button', { name: '공격 문구 입력하기' })).toBeTruthy();
  await click('레드팀 실행'); expect(red).toHaveBeenCalledOnce(); expect(window.location.hash).toBe('#/live');
});
it('작업 상세는 payload·결과를 표시하고 404와 일시 오류 재시도를 구분한다', async () => {
  const api = new MockApi(), job = await api.createJob(exampleQuestions[0].text);
  await go(`#/jobs/${job.id}`); const { container } = await mount(api);
  expect(container.querySelector('details > summary')?.textContent).toBe('payload');
  expect(container.querySelector('details pre')?.textContent).toContain('task_id');
  expect(screen.getByRole('heading', { name: '이 작업의 장애 기록' })).toBeTruthy();
  await go('#/jobs/demo-0'); expect(screen.getByText('이 작업의 상세 기록이 없어요')).toBeTruthy();
  vi.spyOn(api, 'getJob').mockRejectedValueOnce(new ApiError(503, '연결 실패')); await go(`#/jobs/${job.id}`);
  await click('재시도'); expect(container.querySelector('details pre')).toBeTruthy();
});
it('작업 관찰은 성공 후 느리게 폴링하고 작업 변경 뒤 늦은 응답을 버린다', async () => {
  const api = new MockApi(), first = await api.createJob(exampleQuestions[0].text), second = await api.createJob(exampleQuestions[1].text);
  const one = await api.getJob(first.id), two = await api.getJob(second.id);
  let resolveOld!: (job: Job) => void;
  const get = vi.spyOn(api, 'getJob').mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; })).mockResolvedValueOnce({ ...two, status: 'running' }).mockResolvedValue(two);
  const hook = renderHook(({ id }) => useJobWatch(api, id), { initialProps: { id: first.id } });
  await act(async () => { hook.rerender({ id: second.id }); }); expect(hook.result.current.job?.id).toBe(second.id);
  await act(async () => { resolveOld(one); }); expect(hook.result.current.job?.id).toBe(second.id);
  await advance(1500); expect(hook.result.current.settled).toBe(true); expect(get).toHaveBeenCalledTimes(3);
  await advance(6000); expect(get).toHaveBeenCalledTimes(3);
  await advance(4000); expect(get).toHaveBeenCalledTimes(4); hook.unmount(); expect(vi.getTimerCount()).toBe(0);
});
it('메일 장애 실험은 다른 화면을 오가도 발송·재시도·완료 이벤트를 이어 본다', async () => {
  const api = new MockApi(), send = vi.spyOn(api, 'sendJob'); await go('#/lab'); const { container } = await mount(api);
  await click('메일 서버 다운 일으키기'); await go('#/ops'); await advance(8000); await go('#/live');
  expect(send).toHaveBeenCalledOnce(); await advance(8000);
  expect(container.querySelector('[data-agent="mailer"][data-failure="MAIL_SEND_FAILED"]')).toBeTruthy();
  expect(container.querySelector('.job-monitor')?.textContent).toContain('메일: 발송 완료');
  expect(container.querySelector('.timeline')?.textContent).toContain('보낼 편지함에 넣음');
  expect(container.querySelector('.reroute[data-from="send"][data-to="outbox"][data-state="recovered"]')).toBeTruthy();
});
