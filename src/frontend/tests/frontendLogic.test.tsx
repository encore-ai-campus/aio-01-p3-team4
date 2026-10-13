import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MockApi } from '../src/api/mock';
import { ApiError } from '../src/api/client';
import type { Job, TimelineEvent } from '../src/api/types';
import { useJobRun } from '../src/hooks/useJobRun';
import { useJobWatch } from '../src/admin/hooks/useJobWatch';
import { useLab } from '../src/hooks/useLab';
import { CitizenSite } from '../src/citizen/pages/CitizenSite';
import { deriveRows } from '../src/lib/timeline';
import { incidentCards, incidentState } from '../src/lib/incidents';
import { exampleQuestions } from '../src/lib/failures';
const text = exampleQuestions[0].text;
beforeEach(() => { vi.useFakeTimers(); localStorage.clear(); window.history.replaceState({}, '', '/?hero=0#/'); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });
const advance = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
async function start(api: MockApi) {
  render(<CitizenSite api={api}/>);
  fireEvent.change(screen.getByLabelText('민원 설명'), { target: { value: text } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '신고 창구 찾기' })); });
}
async function fixture() {
  const api = new MockApi(), created = await api.createJob(text), job = await api.getJob(created.id);
  return { api, job, events: await api.getTimeline(job.id) };
}
it('F1: either GET retries three network errors, resets the streak on success, and never creates twice', async () => {
  for (const method of ['getJob', 'getTimeline'] as const) {
    const api = new MockApi({ async: true }), create = vi.spyOn(api, 'createJob'), original = api[method].bind(api);
    let calls = 0;
    vi.spyOn(api, method).mockImplementation(((id: string) => ++calls === 4 || calls >= 8 ? original(id) : Promise.reject(new TypeError('Failed to fetch'))) as typeof api[typeof method]);
    const hook = renderHook(() => useJobRun(api));
    await act(async () => { await hook.result.current.submit(text); }); await advance(9000);
    expect(hook.result.current.phase).toBe('success'); expect(calls).toBe(8); expect(create).toHaveBeenCalledTimes(1); hook.unmount();
  }
});
it('F1: exhausted polling shows Korean text and the retry button resumes the existing job', async () => {
  const api = new MockApi({ async: true }), create = vi.spyOn(api, 'createJob');
  const get = vi.spyOn(api, 'getJob').mockRejectedValue(new TypeError('Failed to fetch'));
  await start(api); await advance(6000);
  expect(get).toHaveBeenCalledTimes(4); expect(screen.getByRole('alert').textContent).not.toContain('Failed to fetch');
  get.mockRestore(); await act(async () => { fireEvent.click(screen.getByRole('button', { name: '다시 시도' })); }); await advance(2000);
  expect(document.querySelector('.result-columns')).toBeTruthy(); expect(create).toHaveBeenCalledTimes(1);
});
it('F1/F6: 409 and 422 preserve answers and question labels while resuming the same job', async () => {
  for (const status of [409, 422]) {
    const { api, job, events } = await fixture();
    const questions = [{ id: 'q_what', field: 'category', text: '불편을 알려 주세요' }, { id: 'q_confirm_location', field: 'location_detail', text: '위치를 확인해 주세요' }, { id: 'custom_pii', field: 'pii', text: '다시 적어 주세요' }];
    const waiting: Job = { ...job, status: 'needs_user', state: { ...job.state, last_result: { ...events[0].payload, data: { questions } } } };
    const create = vi.spyOn(api, 'createJob').mockResolvedValue(waiting);
    vi.spyOn(api, 'getJob').mockResolvedValue(waiting); vi.spyOn(api, 'getTimeline').mockResolvedValue([]);
    const answer = vi.spyOn(api, 'answerJob').mockRejectedValueOnce(new ApiError(status, '답변을 확인해 주세요')).mockResolvedValue(job);
    await start(api);
    for (const q of questions) fireEvent.change(screen.getByLabelText(q.text), { target: { value: '상도동 가로등' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '답변 보내기' })); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '다시 시도' })); }); await advance(1000);
    expect(screen.getByLabelText<HTMLInputElement>(questions[0].text).value).toBe('상도동 가로등');
    expect(document.querySelector('.question-answers')?.textContent).toMatch(/불편 내용:.*위치 확인:.*다시 적은 내용:/);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '답변 보내기' })); });
    expect(create).toHaveBeenCalledTimes(1); expect(answer).toHaveBeenCalledTimes(2); cleanup();
  }
});
it('F2: needs_user keeps polling and success refreshes mail events every ten seconds until unmount', async () => {
  const { api, job, events } = await fixture(); let current: Job = { ...job, status: 'needs_user' }, timeline = events;
  const get = vi.spyOn(api, 'getJob').mockImplementation(async () => current);
  vi.spyOn(api, 'getTimeline').mockImplementation(async () => timeline);
  const hook = renderHook(() => useJobWatch(api, job.id)); await advance(0);
  current = { ...job, status: 'running' }; await advance(2500); expect(hook.result.current.job?.status).toBe('running');
  current = job; await advance(2500); expect(hook.result.current.job?.status).toBe('success');
  timeline = [...events, { ...events[0], id: 99, agent: 'mailer', payload: { ...events[0].payload, author: 'mailer' } }];
  await advance(10000); expect(hook.result.current.events.at(-1)?.id).toBe(99);
  hook.unmount(); const calls = get.mock.calls.length; await advance(10000); expect(get).toHaveBeenCalledTimes(calls);
});
it('F3: same-incident questions and out-of-scope replies recover the internal error without merging cards', async () => {
  const { events } = await fixture();
  for (const status of ['needs_user', 'failed'] as const) {
    const retry: TimelineEvent = { ...events[0], payload: { ...events[0].payload, status: 'retry', failure: 'LLM_OUTPUT_INVALID', incident_id: 'inc-x' } };
    const reply: TimelineEvent = { ...retry, id: 90, payload: { ...retry.payload, status, failure: status === 'needs_user' ? 'NEEDS_MORE_INFO' : 'OUT_OF_SCOPE' } };
    const timeline = [retry, reply], cards = incidentCards(timeline, deriveRows(timeline));
    expect(cards).toHaveLength(2); expect(new Set(cards.map(card => card.key)).size).toBe(2);
    expect(incidentState(cards[0], false).label).toBe('자동 복구'); expect(cards[1].recovery).toBeUndefined();
  }
});
it('F4: forced out-of-scope result resolves old asks and consistently selects the existing scope copy', async () => {
  const { api, job, events } = await fixture();
  events[0].payload = { ...events[0].payload, status: 'needs_user', failure: 'NEEDS_MORE_INFO' };
  const outside = { ...job.state.result!, category: 'out_of_scope' as const };
  job.state = { ...job.state, result: undefined, results: [outside] };
  vi.spyOn(api, 'createJob').mockResolvedValue(job); vi.spyOn(api, 'getTimeline').mockResolvedValue(events.slice(0, 3));
  await start(api); await advance(12000);
  expect(screen.getByTestId('out-of-scope-banner')).toBeTruthy(); expect(document.querySelector('.question-heading .badge')?.textContent).toBe('안내만 했어요');
  expect(document.querySelector('.citizen-status [aria-label]')?.getAttribute('aria-label')).toBe('전국 공통 창구를 안내만 했어요');
  expect(document.querySelectorAll('.pipeline .skipped')).toHaveLength(2); expect(document.querySelector('.reroute-destination')?.textContent).not.toBe('답변 기다림');
  expect(document.querySelector('.reroute-chip')?.textContent).not.toContain('안내 중');
});
it('F5: terminal delay failure shows confirmation and verified channels without draft or ongoing recovery copy', async () => {
  const { api, job, events } = await fixture(); job.status = 'failed'; job.state.confirm_needed = ['창구에 운영 시간을 확인해 주세요'];
  events.at(-1)!.payload = { ...events.at(-1)!.payload, status: 'failed', failure: 'SERVICE_UNAVAILABLE' };
  vi.spyOn(api, 'createJob').mockResolvedValue(job); vi.spyOn(api, 'getTimeline').mockResolvedValue(events);
  await start(api); await advance(12000);
  expect(screen.getByRole('alert').textContent).toContain(job.state.confirm_needed[0]);
  expect(document.querySelector('.channels .channel-row h3')?.textContent).toBe(job.state.result!.channels[0].name);
  expect(document.querySelector('.draft, .virtual-send')).toBeNull();
  expect(document.querySelector('.citizen-status [aria-label]')?.getAttribute('aria-label')).toBe('안내를 마치지 못했어요');
  expect(document.querySelector('.reroute-chip')?.textContent).not.toContain('안내 중');
});
it('F7: a 39-second preparation injects mail_down immediately before send and still queues then recovers', async () => {
  const { api, job, events } = await fixture(), started = Date.now();
  vi.spyOn(api, 'createJob').mockResolvedValue({ ...job, status: 'running' });
  vi.spyOn(api, 'getJob').mockImplementation(async () => Date.now() - started < 39000 ? { ...job, status: 'running' } : job);
  const flag = vi.spyOn(api, 'setChaosFlag'), send = vi.spyOn(api, 'sendJob');
  const hook = renderHook(() => { const run = useJobRun(api); return { run, lab: useLab(api, run.submit) }; });
  await act(async () => { await hook.result.current.lab.trigger('mail_down'); }); await advance(38000);
  expect(flag).not.toHaveBeenCalled(); expect(send).not.toHaveBeenCalled(); await advance(1000);
  expect(flag).toHaveBeenCalledTimes(1); expect(send).toHaveBeenCalledTimes(1);
  expect(flag.mock.invocationCallOrder[0]).toBeLessThan(send.mock.invocationCallOrder[0]);
  await advance(1000); expect(hook.result.current.run.mail.status).toBe('queued');
  await advance(3000); expect(hook.result.current.run.mail.status).toBe('sent'); expect(hook.result.current.run.events.length).toBeGreaterThan(events.length);
});
it('F7: failed preparation never injects a mail outage; other faults still inject before creation', async () => {
  const { api, job } = await fixture(); const create = vi.spyOn(api, 'createJob').mockResolvedValue({ ...job, status: 'failed' });
  const flag = vi.spyOn(api, 'setChaosFlag'), send = vi.spyOn(api, 'sendJob');
  const hook = renderHook(() => { const run = useJobRun(api); return { run, lab: useLab(api, run.submit) }; });
  await act(async () => { await hook.result.current.lab.trigger('mail_down'); }); await advance(12000);
  expect(flag).not.toHaveBeenCalled(); expect(send).not.toHaveBeenCalled();
  await act(async () => { await hook.result.current.lab.trigger('llm_invalid'); });
  expect(flag.mock.invocationCallOrder[0]).toBeLessThan(create.mock.invocationCallOrder[1]);
});
it('F1/F7: terminal timeline retries are cancellable and failed or cancelled injection cannot send', async () => {
  const { api, job } = await fixture(), timeline = vi.spyOn(api, 'getTimeline').mockRejectedValueOnce(new TypeError('Failed to fetch')).mockResolvedValue([]);
  const hook = renderHook(() => { const run = useJobRun(api); return { run, lab: useLab(api, run.submit) }; });
  await act(async () => { await hook.result.current.run.submit(text); }); await advance(1000);
  expect(hook.result.current.run.phase).toBe('success'); expect(timeline).toHaveBeenCalledTimes(2);
  const send = vi.spyOn(api, 'sendJob'); const flag = vi.spyOn(api, 'setChaosFlag').mockRejectedValueOnce(new ApiError(503, '주입 실패'));
  await act(async () => { await hook.result.current.lab.trigger('mail_down'); });
  expect(hook.result.current.run.mail.status).toBe('error'); expect(send).not.toHaveBeenCalled();
  let release!: () => void; flag.mockImplementationOnce(() => new Promise<void>(resolve => { release = resolve; }));
  await act(async () => { await hook.result.current.lab.trigger('mail_down'); });
  act(() => { hook.result.current.run.reset(); }); await act(async () => { release(); }); expect(send).not.toHaveBeenCalled();
  vi.spyOn(api, 'createJob').mockResolvedValue(job); timeline.mockRejectedValue(new TypeError('Failed to fetch'));
  await act(async () => { await hook.result.current.run.submit(text); }); act(() => { hook.result.current.run.reset(); });
  const calls = timeline.mock.calls.length; await advance(5000); expect(timeline).toHaveBeenCalledTimes(calls);
});
