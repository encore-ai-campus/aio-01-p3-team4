import mailer from '@contracts/samples/mailer_output.json';
import queued from '@contracts/samples/mailer_mail_down_output.json';
import blockedSample from '@contracts/samples/mailer_blocked_output.json';
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { LiveApi } from '../src/api/live';
import { MockApi } from '../src/api/mock';
import type { Api } from '../src/api/client';
import type { TimelineEvent } from '../src/api/types';
import { useJobRun } from '../src/hooks/useJobRun';
import { useLab } from '../src/hooks/useLab';
import { useMailbox } from '../src/hooks/useMailbox';
import { exampleQuestions, failureNames, SEND_STAGE } from '../src/lib/failures';
import { VIRTUAL_SEND_NOTICE } from '../src/lib/labels';
import { deriveRows, deriveSend, deriveStages, narrate } from '../src/lib/timeline';
import { replaySchedule } from '../src/lib/replay';

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('가상 발송·회신을 시각대로 공개하고 편지함 재발송·자동 발송·폴링 취소를 처리한다', async () => {
  const draft = { title: '수정한 신고', body: '수정한 본문' };
  const start = Date.parse('2026-10-07T00:00:00Z');
  expect(blockedSample.data.message_id).toBeNull();
  expect(SEND_STAGE).toEqual({ id: 'send', label: '가상 발송' });
  expect(VIRTUAL_SEND_NOTICE).toBe('가상 발송: 실제 기관에는 보내지 않아요');
  expect(deriveSend([])).toBe('idle');
  for (const asyncMode of [false, true]) {
    for (const down of [false, true]) {
      let now = start;
      const api = new MockApi({ async: asyncMode, now: () => now });
      const job = await api.createJob(exampleQuestions[0].text);
      if (asyncMode) { now += 5600; expect((await api.getJob(job.id)).status).toBe('success'); }
      const sentAt = now;
      if (down) await api.setChaosFlag('mailer', { incident_id: 'mail-down', kind: 'mail_down', injected_at: new Date(now).toISOString(), params: {} });
      await api.sendJob(job.id, draft);
      await expect(api.sendJob(job.id, draft)).rejects.toMatchObject({ status: 409 });
      expect((await api.getTimeline(job.id)).filter(event => event.agent === 'mailer')).toEqual([]);
      expect(await api.getMailbox()).toEqual([]);
      expect(await api.getIncidents()).toEqual([]);
      now += 799;
      expect((await api.getTimeline(job.id)).filter(event => event.agent === 'mailer')).toEqual([]);
      now++;
      const first = (await api.getTimeline(job.id)).at(-1)!;
      const { message_id: _sampleMessageId, ...sampleData } = down ? queued.data : mailer.data;
      expect(first).toMatchObject({ agent: 'mailer', attempt: 1, created_at: new Date(now).toISOString(), payload: {
        author: 'mailer', status: down ? 'retry' : 'success', failure: down ? 'MAIL_SEND_FAILED' : null,
        data: { ...sampleData, ...(down ? { evidence: { broken: `[mock] ${queued.data.evidence.broken}` } } : {}) },
      } });
      if (down) {
        expect(deriveSend(deriveRows(await api.getTimeline(job.id)))).toBe('fail');
        expect(narrate(deriveRows(await api.getTimeline(job.id)))).toBe('메일 서버가 응답하지 않아 보낼 편지함에 넣고 다시 보내고 있어요');
        expect(await api.getMailbox()).toEqual([]);
        expect((await api.getIncidents())[0]).toMatchObject({ detected_at: first.created_at, recovered_at: null });
        now = sentAt + 3999;
        expect(await api.getMailbox()).toEqual([]);
        now++;
        const events = await api.getTimeline(job.id);
        expect(events.at(-1)).toMatchObject({ attempt: 2, payload: { status: 'success', data: { status: 'sent', attempts: 1, receipt_no: '2026-1007-0001' } } });
        const rows = deriveRows(events);
        expect(rows.at(-1)).toMatchObject({ stage: 'send', tone: 'recovered', note: '자동 복구' });
        expect(deriveSend(rows)).toBe('recovered');
        expect(narrate(rows)).toBe('보낼 편지함에서 다시 보내 담당자에게 도착했어요');
        expect((await api.getIncidents())[0]).toMatchObject({ detected_at: first.created_at, recovered_at: new Date(now).toISOString(), result: 'recovered' });
      } else expect(deriveSend(deriveRows(await api.getTimeline(job.id)))).toBe('done');
      expect(deriveStages(deriveRows(await api.getTimeline(job.id))).some(stage => stage.status === 'active')).toBe(false);
      const report = (await api.getMailbox())[0];
      expect(report).toMatchObject({ kind: 'report', from: 'minwon-guide@minwon.example', subject: '[가상 민원] 수정한 신고', snippet: draft.body, created: new Date(now).toISOString() });
      now += (down ? 3000 : 2200) - 1;
      expect(await api.getMailbox()).toHaveLength(1);
      now++;
      const messages = await api.getMailbox();
      expect(messages.map(message => message.kind)).toEqual(['reply', 'report']);
      expect(messages[0]).toMatchObject({ from: report.to[0], to: ['citizen@minwon.example'], subject: '[가상 회신] 접수되었습니다 · 접수번호 2026-1007-0001' });
      expect(messages.every(message => message.to.every(to => to.endsWith('.example')))).toBe(true);
      expect((await api.getJob(job.id)).status).toBe('success');
      const next = await api.createJob(exampleQuestions[0].text);
      if (asyncMode) now += 5600;
      await api.sendJob(next.id, draft); now += 800;
      expect((await api.getTimeline(next.id)).at(-1)?.payload.status).toBe('success');
      expect(await api.getIncidents()).toHaveLength(down ? 1 : 0);
    }
  }
  vi.useFakeTimers(); vi.setSystemTime(start);
  const api = new MockApi();
  const send = vi.spyOn(api, 'sendJob');
  const hook = renderHook(() => { const job = useJobRun(api); return { job, lab: useLab(api, job.submit) }; });
  await act(async () => { await hook.result.current.job.send(draft); });
  expect(send).not.toHaveBeenCalled();
  await act(async () => { await hook.result.current.lab.trigger('mail_down'); });
  const schedule = replaySchedule(await api.getTimeline(hook.result.current.job.jobId!));
  await act(async () => { await vi.advanceTimersByTimeAsync(schedule.at(-1)!); });
  expect(hook.result.current.job).toMatchObject({ phase: 'success', mail: { status: 'sending' } });
  expect(send).toHaveBeenCalledTimes(1);
  await act(async () => { await hook.result.current.job.send(draft); await vi.advanceTimersByTimeAsync(1000); });
  expect(send).toHaveBeenCalledTimes(1);
  expect(hook.result.current.job).toMatchObject({ phase: 'success', mail: { status: 'queued', receiptNo: null, error: 'MAIL_SEND_FAILED' } });
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(hook.result.current.job).toMatchObject({ phase: 'success', mail: { status: 'sent', receiptNo: '2026-1007-0001', error: null } });
  expect(vi.getTimerCount()).toBe(0);
  await act(async () => { await hook.result.current.job.send(draft); await vi.advanceTimersByTimeAsync(1000); });
  expect(hook.result.current.job.mail.status).toBe('sent');
  expect((await api.getMailbox())[0].subject).toBe('[가상 민원] 수정한 신고');
  const empty = vi.spyOn(api, 'getTimeline').mockResolvedValue([]);
  await act(async () => { await hook.result.current.job.send(draft); await vi.advanceTimersByTimeAsync(30_000); });
  expect(hook.result.current.job).toMatchObject({ phase: 'success', mail: { status: 'error', error: '메일 발송 확인 시간이 지났어요' } });
  expect(vi.getTimerCount()).toBe(0);
  empty.mockRestore();
  // 타임라인 요청 자체가 멈춰도 30초에 종료하고 늦은 응답은 버린다.
  let release: ((events: TimelineEvent[]) => void) | undefined;
  const stalled = vi.spyOn(api, 'getTimeline').mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
  await act(async () => { await hook.result.current.job.send(draft); await vi.advanceTimersByTimeAsync(30_000); });
  expect(hook.result.current.job.mail.status).toBe('error');
  expect(vi.getTimerCount()).toBe(0);
  await act(async () => { release!([]); });
  expect(hook.result.current.job.mail.status).toBe('error');
  stalled.mockRestore();
  send.mockRejectedValueOnce(new Error('발송 요청 오류'));
  await act(async () => { await hook.result.current.job.send(draft); });
  expect(hook.result.current.job).toMatchObject({ phase: 'success', mail: { status: 'error', error: '발송 요청 오류' } });
  await act(async () => { await hook.result.current.job.send(draft); });
  act(() => { hook.result.current.job.reset(); });
  expect(vi.getTimerCount()).toBe(0);
  const mailboxApi: Api = api;
  const mailbox = vi.spyOn(mailboxApi, 'getMailbox');
  const inbox = renderHook(({ active }) => useMailbox(mailboxApi, { active }), { initialProps: { active: false } });
  expect(mailbox).not.toHaveBeenCalled();
  await act(async () => { inbox.rerender({ active: true }); });
  expect(inbox.result.current.connected).toBe(true);
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(mailbox).toHaveBeenCalledTimes(2);
  mailbox.mockResolvedValueOnce(null);
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(inbox.result.current.connected).toBe(false);
  inbox.rerender({ active: false });
  expect(vi.getTimerCount()).toBe(0);
  await act(async () => { inbox.rerender({ active: true }); });
  inbox.unmount(); hook.unmount();
  expect(vi.getTimerCount()).toBe(0);
});

it('live 발송은 본문을 무시하고 메일함은 제목으로 회신을 구별하며 미연결·차단을 처리한다', async () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  const api = new LiveApi();
  const draft = { title: '고친 제목', body: '고친 본문', fill_in: [], removed_pii: [] };
  for (const response of [new Response(null, { status: 204 }), new Response('JSON이 아닌 응답', { status: 202 })]) {
    fetch.mockResolvedValueOnce(response);
    expect(await api.sendJob('job/1', draft)).toBeUndefined();
    expect(fetch).toHaveBeenLastCalledWith('/api/jobs/job%2F1/send', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: draft.title, body: draft.body }) });
  }
  const citizen = { name: '김민원', email: 'minwon.kim@minwon.example' };
  fetch.mockResolvedValueOnce(new Response(null, { status: 204 }));
  await api.sendJob('job/1', { ...draft, citizen });
  expect(JSON.parse(fetch.mock.lastCall![1].body)).toEqual({ title: draft.title, body: draft.body, citizen });
  const message = (ID: string, to: string, Subject: string, Created: string) => ({ ID, From: { Name: '가상 담당자', Address: 'officer@minwon.example' }, To: [{ Name: '시민', Address: to }], Subject, Snippet: '미리보기', Created });
  fetch.mockResolvedValueOnce(new Response(JSON.stringify({ messages: [
    message('report', 'officer@minwon.example', '[가상 민원] 신고', '2026-10-07T00:00:00Z'),
    message('reply', 'another@minwon.example', '[가상 회신] 접수되었습니다 · 접수번호 2026-1007-0001', '2026-10-07T00:00:03Z'),
    message('citizen-report', 'citizen@minwon.example', '시민 주소로 온 신고', '2026-10-07T00:00:01Z'),
    message('other', 'outside@invalid.test', '기타', '2026-10-07T00:00:02Z'),
  ] })));
  // 시민 주소만으로는 회신으로 판정하지 않는다.
  const fixture = await api.getMailbox();
  expect(fixture?.map(item => [item.id, item.kind])).toEqual([['reply', 'reply'], ['other', 'other'], ['citizen-report', 'report'], ['report', 'report']]);
  expect(fixture?.[0]).toMatchObject({ from: 'officer@minwon.example', to: ['another@minwon.example'], snippet: '미리보기' });
  expect(fetch).toHaveBeenLastCalledWith('/mailbox/api/v1/messages?limit=20');
  for (const status of [404, 502]) { fetch.mockResolvedValueOnce(new Response(null, { status })); expect(await api.getMailbox()).toBeNull(); }
  fetch.mockRejectedValueOnce(new TypeError('네트워크 오류')); expect(await api.getMailbox()).toBeNull();
  fetch.mockResolvedValueOnce(new Response(null, { status: 500 })); await expect(api.getMailbox()).rejects.toMatchObject({ status: 500 });
  fetch.mockResolvedValueOnce(new Response(JSON.stringify({ detail: '안전장치로 차단' }), { status: 422 }));
  await expect(api.sendJob('job-1', draft)).rejects.toMatchObject({ status: 422, message: '안전장치로 차단' });
  const blocked: TimelineEvent = { id: 1, job_id: 'job-1', agent: 'mailer', event_type: 'failed', attempt: 1, created_at: new Date().toISOString(),
    payload: { task_id: 'job-1', author: 'mailer', status: 'failed', failure: 'MAIL_BLOCKED', incident_id: null, data: blockedSample.data } };
  expect(failureNames.MAIL_BLOCKED).toBe('안전장치로 차단');
  expect(deriveSend(deriveRows([blocked]))).toBe('fail');
  expect(narrate(deriveRows([blocked]))).toBe('안전장치로 차단: 실제 기관 주소라 보내지 않았어요. 가상 발송만 허용돼요');
  expect(deriveStages(deriveRows([blocked])).some(stage => stage.status === 'active')).toBe(false);
});

it('편지함 재발송은 30초 뒤에도 접수번호가 일치하는 회신을 확인해 발송 완료로 바꾼다', async () => {
  vi.useFakeTimers();
  const api = new MockApi();
  const { result } = renderHook(() => useJobRun(api));
  await act(async () => { await result.current.submit(exampleQuestions[0].text); await vi.runAllTimersAsync(); });
  const jobId = result.current.jobId!;
  const receiptNo = '2026-1007-0001';
  const events = await api.getTimeline(jobId);
  const retry: TimelineEvent = { id: events.at(-1)!.id + 1, job_id: jobId, agent: 'mailer', event_type: 'retry', attempt: 1,
    created_at: new Date().toISOString(), payload: { ...queued, task_id: jobId, author: 'mailer', status: 'retry', failure: 'MAIL_SEND_FAILED',
      data: { ...queued.data, receipt_no: receiptNo } } };
  vi.spyOn(api, 'sendJob').mockResolvedValue();
  const timeline = vi.spyOn(api, 'getTimeline').mockResolvedValue([...events, retry]);
  const mailbox = vi.spyOn(api, 'getMailbox').mockResolvedValue([]);
  await act(async () => { await result.current.send(result.current.result!.draft); await vi.advanceTimersByTimeAsync(32_000); });
  expect(result.current.mail).toMatchObject({ status: 'queued', receiptNo });
  const reply = { id: 'reply', from: queued.data.to, to: ['citizen@minwon.example'],
    subject: `[가상 회신] 접수되었습니다 · 접수번호 ${receiptNo}0`, snippet: '', created: new Date().toISOString(), kind: 'reply' as const };
  mailbox.mockResolvedValue([reply]);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(result.current.mail.status).toBe('queued');
  mailbox.mockResolvedValue([{ ...reply, subject: `[가상 회신] 접수되었습니다 · 접수번호 ${receiptNo}` }]);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(result.current.mail).toEqual({ status: 'sent', receiptNo, error: null });
  expect(vi.getTimerCount()).toBe(0);
  const calls = mailbox.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(mailbox).toHaveBeenCalledTimes(calls);
  mailbox.mockResolvedValue([]);
  timeline.mockResolvedValue([...events, retry, { ...retry, id: retry.id + 1 }]);
  await act(async () => { await result.current.send(result.current.result!.draft); await vi.advanceTimersByTimeAsync(121_000); });
  expect(result.current.mail).toMatchObject({ status: 'queued', receiptNo });
  expect(vi.getTimerCount()).toBe(0);
  const stoppedCalls = mailbox.mock.calls.length;
  mailbox.mockResolvedValue([{ ...reply, subject: `[가상 회신] 접수되었습니다 · 접수번호 ${receiptNo}` }]);
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(result.current.mail.status).toBe('queued');
  expect(mailbox).toHaveBeenCalledTimes(stoppedCalls);
});
