import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from '../src/admin/App';
import type { Api } from '../src/api/client';
import { MockApi } from '../src/api/mock';
import { LiveApi } from '../src/api/live';
import type { Incident, OpsEvent, OpsOverview } from '../src/api/types';
import { DashboardPage } from '../src/admin/pages/DashboardPage';
import { activityLog, filterIncidents, incidentMetrics, metricsCohort, recentIncidents, summarizeIncidents } from '../src/lib/ops';
import reportMetrics from './fixtures/metrics_report.json';
const now = Date.parse('2026-10-12T12:00:00Z');
const incident = (id: string, offset: number, extra: Partial<Incident> = {}): Incident => ({ id, job_id: 'job-1', target: 'verifier', fault: 'link_dead', injected_at: new Date(now - offset).toISOString(), detected_at: new Date(now - offset).toISOString(), recovered_at: new Date(now - offset + 3000).toISOString(), human_intervention: false, result: 'recovered', run_id: null, ...extra });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it('기간 경계와 자동 복구 시간은 감지 시각·사람 개입·결측을 구분한다', () => {
  const items = [incident('boundary', 24 * 3600_000), incident('old', 25 * 3600_000), incident('human', 5000, { human_intervention: true }), incident('unknown', 5000, { detected_at: null }), incident('future', -5000)];
  const filtered = filterIncidents(items, 24, now);
  expect(filtered.map(item => item.id)).toEqual(['boundary', 'human', 'unknown']);
  expect(incidentMetrics(filtered)).toEqual({ total: 1, rate: 0, avg: null });
  expect(metricsCohort(filtered).map(item => item.id)).toEqual(['human']);
  expect(filterIncidents(items, 0, now)).toHaveLength(4);
  expect(incidentMetrics([])).toEqual({ total: 0, rate: null, avg: null });
});
it('활동 로그는 정상 추가 질문과 장애·자동 복구를 시각 순서로 합친다', () => {
  const events: OpsEvent[] = [
    { at: new Date(now - 1000).toISOString(), job_id: 'job-1', agent: 'classifier', status: 'needs_user', failure: 'NEEDS_MORE_INFO', attempt: 1 },
    { at: new Date(now - 9000).toISOString(), job_id: 'job-1', agent: 'verifier', status: 'retry', failure: 'LINK_DEAD', attempt: 2 },
  ];
  const rows = activityLog(events, [incident('i1', 5000)]);
  expect(rows.map(row => row.kind)).toEqual(['normal', 'fault', 'fault', 'fault']);
  expect(rows[1].text).toContain('자동 복구 3.0초');
  expect(rows[3].text).toContain('링크가 안 열림');
});
it('mock 기간별 숫자·차트·최근 기록에 원문이 없다', async () => {
  const api = new MockApi({ now: () => now, opsDemo: true });
  const all = await api.getOpsOverview(), day = await api.getOpsOverview(24), week = await api.getOpsOverview(168);
  expect(all.jobs.total).toBe(184);
  expect(day.jobs.total).toBeLessThan(all.jobs.total);
  expect(week.jobs.total).toBe(all.jobs.total);
  expect(all.services).toHaveLength(9);
  expect(all.recent_jobs).toHaveLength(30);
  expect(all.recent_events).toHaveLength(80);
  expect(all.series.points.length).toBeLessThanOrEqual(48);
  expect(all.series.points.reduce((sum, p) => sum + p.success, 0)).toBe(all.jobs.success);
  expect(all.agents.every(agent => agent.calls === agent.success + agent.retry + agent.failed + agent.needs_user)).toBe(true);
  expect(all.recent_jobs.every(job => !('text' in job))).toBe(true);
});
it('운영 계약에 기간과 AbortSignal을 전달한다', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response('{}'));
  vi.stubGlobal('fetch', fetch);
  const controller = new AbortController();
  await new LiveApi().getOpsOverview(168, controller.signal);
  expect(fetch).toHaveBeenCalledWith('/api/ops/overview?hours=168', { method: 'GET', signal: controller.signal });
});
it('대시보드는 10개 서비스·정상 기록·기간 숫자·필터를 표시한다', async () => {
  const api = new MockApi({ now: () => now, opsDemo: true });
  const request = vi.spyOn(api, 'getOpsOverview');
  const { container } = await act(async () => render(<DashboardPage api={api}/>));
  expect(screen.getByText('10/10 가동 중')).toBeTruthy();
  expect(container.querySelectorAll('.ops-stats > div')).toHaveLength(6);
  expect(container.querySelector('.ops-stats dd')?.textContent).toBe('184건');
  expect(container.querySelectorAll('.ops-stage-dots')).toHaveLength(30);
  const filters = within(screen.getByRole('group', { name: '활동 로그 필터' }));
  fireEvent.click(filters.getByRole('button', { name: '정상' }));
  expect(screen.getByRole('region', { name: '활동 로그 목록' }).textContent).not.toContain('장애 감지');
  fireEvent.click(filters.getByRole('button', { name: '장애' }));
  expect(screen.getByRole('region', { name: '활동 로그 목록' }).textContent).toContain('자동 복구');
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '24시간' })); });
  expect(request).toHaveBeenLastCalledWith(24, expect.any(AbortSignal));
  expect(container.querySelector('.ops-stats dd')?.textContent).not.toBe('184건');
  expect(container.querySelector('details')?.open).toBe(false);
});
it('부분 실패에도 정상 기록을 유지하고 30초 갱신·언마운트 중단한다', async () => {
  vi.useFakeTimers();
  const api = new MockApi({ now: () => now, opsDemo: true });
  const data = await api.getOpsOverview();
  data.services[1].ok = false; data.errors.push('분류 응답 없음');
  const request = vi.spyOn(api, 'getOpsOverview').mockResolvedValue(data);
  vi.spyOn(api, 'getIncidents').mockRejectedValue(new Error('장애 기록 연결 실패'));
  const { unmount, container } = await act(async () => render(<DashboardPage api={api}/>));
  expect(screen.getByRole('alert').textContent).toContain('분류 응답 없음');
  expect(screen.getByText('9/10 가동 중')).toBeTruthy();
  expect(container.querySelector('.ops-stats dd')?.textContent).toBe('184건');
  expect(container.querySelectorAll('.ops-stats dd')[5].textContent).toBe('—');
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
  expect(request).toHaveBeenCalledTimes(2);
  const signal = request.mock.calls[1][1];
  unmount();
  expect(signal?.aborted).toBe(true);
  expect(vi.getTimerCount()).toBe(0);
  await vi.advanceTimersByTimeAsync(60_000);
  expect(request).toHaveBeenCalledTimes(2);
});
it('기간 변경은 이전 요청을 취소하고 늦게 온 응답을 버린다', async () => {
  const api = new MockApi({ now: () => now, opsDemo: true }), data = await api.getOpsOverview();
  let resolveOld: (data: OpsOverview) => void = () => undefined;
  const request = vi.spyOn(api, 'getOpsOverview').mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
  const { container } = await act(async () => render(<DashboardPage api={api}/>));
  const signal = request.mock.calls[0][1];
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '24시간' })); });
  const count = container.querySelector('.ops-stats dd')?.textContent;
  expect(signal?.aborted).toBe(true);
  await act(async () => { resolveOld(data); });
  expect(container.querySelector('.ops-stats dd')?.textContent).toBe(count);
});

it('운영 현황 실패 안내와 탭 재진입 재조회를 지원한다', async () => {
  const api: Api = new MockApi(); const overview = vi.spyOn(api, 'getOpsOverview').mockRejectedValue(new Error('연결 실패'));
  window.location.hash = '#/ops';
  await act(async () => { render(<App api={api}/>); });
  expect(screen.getByRole('alert').textContent).toContain('처리 현황을 갱신하지 못했어요');
  expect(screen.getByText('mock 데이터')).toBeTruthy();
  await act(async () => { window.location.hash = '#/incidents'; window.dispatchEvent(new HashChangeEvent('hashchange')); });
  await act(async () => { window.location.hash = '#/ops'; window.dispatchEvent(new HashChangeEvent('hashchange')); });
  expect(overview).toHaveBeenCalledTimes(2);
});
it('운영 현황에 지표 집계 설명이 보인다', async () => {
  window.location.hash = '#/ops';
  await act(async () => { render(<App api={new MockApi()}/>); });
  fireEvent.click(screen.getByText('숫자는 이렇게 집계돼요', { selector: 'summary' }));
  expect(screen.getByRole('heading', { name: '숫자는 이렇게 집계돼요' })).toBeTruthy();
});

it('주입 시작 경계·실제 감지·차단·확인 필요 표시·사람 개입은 서버와 같은 모집단을 쓴다', () => {
  const since = '2026-10-12T04:26:00Z';
  const items = [incident('recovered', 5000, { injected_at: since }), incident('blocked', 5000, { result: 'blocked' }),
    incident('degraded', 5000, { result: 'degraded' }), incident('failed', 5000, { result: 'failed', recovered_at: null }),
    incident('human', 5000, { human_intervention: true }), incident('old', 5000, { injected_at: '2026-10-12T04:25:59Z' }),
    incident('natural', 5000, { injected_at: null }), incident('pending', 5000, { detected_at: null, recovered_at: null, result: 'injected' })];
  expect(incidentMetrics(items)).toEqual({ total: 5, rate: 60, avg: 3 });
  expect(summarizeIncidents(items)[0]).toMatchObject({ total: 5, autoRecovered: 1, blocked: 1, degraded: 1, avgRecoverySec: 3 });
  expect(metricsCohort(items, '2026-10-13T00:00:00Z')).toEqual([]);
  expect(recentIncidents(items, 8).map(item => item.resultLabel)).toEqual(['자동 복구', '차단', '확인 필요 표시', '미복구', '사람 조치', '자동 복구', 'AI 자체 복구', '미발생']);
  const text = activityLog([], items).map(row => row.text).join('\n');
  for (const label of ['차단', '확인 필요 표시', 'AI 자체 복구', '미발생']) expect(text).toContain(label);
  expect(activityLog([], [items[7]])[0].text).not.toContain('장애 감지');
  const serverRows = items.map(item => ({ ...item, metrics_since: '2026-10-13T00:00:00Z' }));
  expect(incidentMetrics(serverRows)).toEqual({ total: 0, rate: null, avg: null });
  expect(summarizeIncidents(serverRows)).toEqual([]);
});

it.each(reportMetrics.cases)('보고서 fixture와 /metrics의 숫자가 일치한다: $name', ({ groups, expected, frontend_avg }) => {
  const items = groups.flatMap((group, index) => Array.from({ length: group.count }, (_, n) => ({
    id: `${index}-${n}`, job_id: null, target: 'verifier', run_id: null,
    ...reportMetrics.defaults, ...group, metrics_since: reportMetrics.since,
  }))) as (Incident & { metrics_since: string })[];
  const metrics = incidentMetrics(items);
  expect(metrics.total).toBe(expected.total);
  expect(metrics.rate).toBeCloseTo(expected.auto_recovery_rate * 100);
  expect(metrics.rate! * metrics.total / 100).toBeCloseTo(expected.auto_recovered);
  if (frontend_avg === null) expect(metrics.avg).toBeNull();
  else expect(metrics.avg).toBeCloseTo(frontend_avg);
  expect(metrics.rate?.toFixed(1)).toBe((expected.auto_recovery_rate * 100).toFixed(1));
});
