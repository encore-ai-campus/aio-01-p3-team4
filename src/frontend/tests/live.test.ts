import { afterEach, expect, it, vi } from 'vitest';
import { ApiError } from '../src/api/client';
import { LiveApi } from '../src/api/live';
import type { ChaosFlag, JobCreated, Metrics } from '../src/api/types';

afterEach(() => vi.unstubAllGlobals());

it('live 경로·메서드·본문과 501 대기 상태, 읽을 수 있는 422 및 일반 오류를 유지한다', async () => {
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  const api = new LiveApi();
  const created: JobCreated = { id: 'job-1', status: 'success', state: { question_round: 0, confirm_needed: [] } };
  const flag: ChaosFlag = { incident_id: 'inc-1', kind: 'delay', injected_at: '2026-10-07T12:00:00+09:00', params: { seconds: 20 } };
  const metrics: Metrics = { total: 0, auto_recovered: 0, auto_recovery_rate: 0, avg_recovery_sec: 0 };
  const selection = { target: 'classifier', kind: 'delay', reason: '테스트' };
  const reply = (body: unknown, status = 200) => fetch.mockResolvedValueOnce(new Response(JSON.stringify(body), { status }));
  const checkCall = (path: string, method: string, body?: unknown) => {
    expect(fetch).toHaveBeenLastCalledWith(`/api${path}`, {
      method,
      ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    });
  };

  reply(created, 201);
  expect(await api.createJob('상도동 가로등')).toEqual(created);
  checkCall('/jobs', 'POST', { text: '상도동 가로등' });
  const job = { ...created, text: '상도동 가로등', created_at: flag.injected_at, completed_at: flag.injected_at };
  reply(job);
  expect(await api.getJob('job/1')).toEqual(job);
  checkCall('/jobs/job%2F1', 'GET');
  reply([]);
  expect(await api.getTimeline('job/1')).toEqual([]);
  checkCall('/jobs/job%2F1/timeline', 'GET');
  reply(created);
  expect(await api.answerJob('job/1', { q1: '상도동 주민센터 앞' })).toEqual(created);
  checkCall('/jobs/job%2F1/answers', 'POST', { answers: { q1: '상도동 주민센터 앞' } });
  reply(metrics);
  expect(await api.getMetrics()).toEqual(metrics);
  checkCall('/metrics', 'GET');
  reply([]);
  expect(await api.getIncidents()).toEqual([]);
  checkCall('/incidents', 'GET');
  fetch.mockResolvedValueOnce(new Response(null, { status: 204 }));
  expect(await api.setChaosFlag('classifier', flag)).toBeUndefined();
  checkCall('/chaos/flags/classifier', 'PUT', flag);
  reply(selection);
  expect(await api.runRedTeam()).toEqual({ ...selection, ready: true });
  checkCall('/chaos/run', 'POST', {});

  reply({ detail: '집계 준비 중' }, 501);
  expect(await api.getMetrics()).toBeNull();
  reply({ detail: '레드팀 구현 예정' }, 501);
  expect(await api.runRedTeam()).toEqual({ ready: false, message: '레드팀 구현 예정' });
  reply({}, 501);
  expect(await api.runRedTeam()).toEqual({ ready: false, message: '레드팀은 준비 중입니다' });
  reply({ detail: [{ loc: ['body', 'text'], msg: '500자 이하여야 합니다' }, { loc: ['body', 'answers', 'q1'], msg: '빈 답변입니다' }] }, 422);
  await expect(api.createJob('x'.repeat(501))).rejects.toMatchObject({
    name: 'ApiError', status: 422, message: 'body.text: 500자 이하여야 합니다; body.answers.q1: 빈 답변입니다',
  });
  reply({ detail: '질문 id와 빈 답변을 확인하세요' }, 422);
  await expect(api.answerJob('job-1', {})).rejects.toThrow('질문 id와 빈 답변을 확인하세요');
  for (const request of [() => api.getMetrics(), () => api.runRedTeam(), () => api.getIncidents()]) {
    reply({ detail: '서버 오류' }, 500);
    await expect(request()).rejects.toMatchObject({ status: 500, message: '서버 오류' });
  }
  reply({ detail: '작업 없음' }, 404);
  await expect(api.getJob('missing')).rejects.toBeInstanceOf(ApiError);
  fetch.mockRejectedValueOnce(new TypeError('연결 실패'));
  await expect(api.getMetrics()).rejects.toThrow('연결 실패');
  fetch.mockResolvedValueOnce(new Response('잘못된 JSON', { status: 200 }));
  await expect(api.getJob('job-1')).rejects.toBeInstanceOf(SyntaxError);
});
