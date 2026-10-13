import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import final from '@contracts/samples/final_streetlight_output.json';
import type { Incident } from '../src/api/types';
import { ApiError } from '../src/api/client';
import { LiveApi } from '../src/api/live';
import { MockApi } from '../src/api/mock';
import { useCountUp } from '../src/hooks/useCountUp';
import { useJobRun } from '../src/hooks/useJobRun';
import { useLab } from '../src/hooks/useLab';
import { chaosCatalogue, exampleQuestions, failureNames } from '../src/lib/failures';
import { agentLabel, categoryLabel, draftCopyText, formatOffset, incidentFaultLabel, kindLabel, methodLabel, regionText } from '../src/lib/labels';
import { ratePercent, recentIncidents, summarizeIncidents } from '../src/lib/ops';
import { deriveRows, narrate } from '../src/lib/timeline';
import { BEATS, HERO_COPY, LOOP } from '../src/citizen/components/hero/scenario';
import { clamp, eio, eo, lerp, mix, prog, pulse, spring } from '../src/citizen/components/hero/motion';

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('화면 라벨·운영 공식·장애 설명과 히어로 데이터는 계약 값을 보존한다', async () => {
  const incident: Incident = { id: '1', job_id: null, target: 'classifier', fault: 'llm_invalid',
    injected_at: '2026-10-12T04:26:00Z', detected_at: '2026-10-12T04:26:00Z', recovered_at: '2026-10-12T04:26:02Z',
    human_intervention: false, result: 'recovered', run_id: null };
  const incidents = [incident, { ...incident, id: '2', recovered_at: '2026-10-12T04:26:06Z' },
    { ...incident, id: '3', human_intervention: true, recovered_at: '2026-10-12T04:27:30Z' },
    { ...incident, id: '4', recovered_at: null },
    { ...incident, id: '5', fault: 'link_dead' as const, detected_at: '2026-10-12T04:26:10Z', recovered_at: null }];
  expect(summarizeIncidents(incidents)).toEqual([
    { kind: 'llm_invalid', label: kindLabel('llm_invalid'), total: 4, autoRecovered: 2, blocked: 0, degraded: 0, avgRecoverySec: 4 },
    { kind: 'link_dead', label: kindLabel('link_dead'), total: 1, autoRecovered: 0, blocked: 0, degraded: 0, avgRecoverySec: null },
  ]);
  expect(summarizeIncidents([{ ...incident, detected_at: null }])).toEqual([]);
  expect(summarizeIncidents([])).toEqual([]);
  expect(recentIncidents(incidents, 2).map(item => item.id)).toEqual(['5', '1']);
  expect(recentIncidents(incidents).map(item => item.resultLabel)).toEqual(['미복구', '자동 복구', '자동 복구', '사람 조치', '미복구']);
  expect(recentIncidents(incidents)[0]).toMatchObject({ targetLabel: '분류', kindLabel: kindLabel('link_dead') });
  expect(recentIncidents(incidents, 0)).toEqual([]);
  const naturalFailure: Incident = { ...incident, id: '6', fault: 'SERVICE_UNAVAILABLE' };
  expect(incidentFaultLabel(naturalFailure.fault)).toBe(failureNames.SERVICE_UNAVAILABLE);
  expect(summarizeIncidents([naturalFailure])[0]).toMatchObject({
    kind: 'SERVICE_UNAVAILABLE', label: '에이전트가 응답 없음', total: 1,
  });
  expect(recentIncidents([naturalFailure])[0].kindLabel).toBe('에이전트가 응답 없음');
  expect(incidents.map(item => item.id)).toEqual(['1', '2', '3', '4', '5']);
  expect(ratePercent(.876)).toBe('88%');
  expect([ratePercent(-1), ratePercent(2)]).toEqual(['0%', '100%']);
  expect(regionText({ sido: null, sigungu: null, dong: null, place_text: null })).toBe('지역 확인 필요');
  expect(regionText({ sido: '서울특별시', sigungu: '동작구', dong: '상도동', place_text: null })).toBe('서울특별시 동작구 상도동');
  expect(regionText({ sido: null, sigungu: null, dong: null, place_text: '주민센터 앞' })).toBe('주민센터 앞');
  expect(draftCopyText(final.draft)).toBe(`${final.draft.title}\n\n${final.draft.body}`);
  expect([formatOffset(1400), formatOffset(60000), formatOffset(59999)]).toEqual(['00:01.4', '01:00.0', '00:59.9']);
  expect(categoryLabel).toEqual({ streetlight: '가로등 고장', road_damage: '도로 파손', illegal_dumping: '쓰레기 무단투기', out_of_scope: '지원 범위 밖 · 안내만' });
  expect(methodLabel).toEqual({ web: '웹사이트', app: '앱', phone: '전화' });
  expect(agentLabel.backend).toBe('총괄');
  for (const item of chaosCatalogue) expect(kindLabel(item.kind)).toBe(item.label);

  for (const kind of ['link_dead', 'llm_invalid', 'pii_leak'] as const) {
    const api = new MockApi();
    const target = kind === 'llm_invalid' ? 'classifier' : 'verifier';
    await api.setChaosFlag(target, { incident_id: kind, kind, injected_at: new Date().toISOString(), params: {} });
    const job = await api.createJob(exampleQuestions[0].text);
    const rows = deriveRows(await api.getTimeline(job.id));
    const failedIndex = rows.findIndex(row => row.tone === 'fail');
    const recoveredIndex = rows.findIndex(row => row.tone === 'recovered');
    const messages = kind === 'link_dead'
      ? ['창구 검증에서 링크가 열리지 않아 그 창구를 빼고 다시 찾고 있어요', '다른 창구로 다시 찾았고, 새 창구는 검증을 통과했어요']
      : kind === 'llm_invalid' ? ['AI 답 모양이 깨져서 같은 단계를 다시 시키고 있어요', '다시 시켜서 정상 답을 받았어요']
        : ['문구에 개인정보가 섞여서 지우고 다시 쓰고 있어요', '개인정보를 지운 문구가 검증을 통과했어요'];
    expect(narrate(rows.slice(0, failedIndex + 1))).toBe(messages[0]);
    expect(narrate(rows.slice(0, recoveredIndex + 1))).toBe(messages[1]);
    expect(narrate(rows)).toBe(messages[1]);
  }
  const api = new MockApi();
  const plain = await api.createJob(exampleQuestions[0].text);
  expect(narrate(deriveRows(await api.getTimeline(plain.id)))).toBeNull();
  expect(narrate([])).toBeNull();
  expect(BEATS.every((beat, index) => beat.t <= LOOP && (!index || beat.t >= BEATS[index - 1].t))).toBe(true);
  expect(BEATS.at(-1)).toEqual({ ...BEATS[0], t: LOOP });
  expect(HERO_COPY.channel).toEqual(final.channels[1]);
  expect(HERO_COPY.failure).toBe('첫 창구 · 링크가 안 열림');
  expect([clamp(2), lerp(0, 10, .5), prog(1, 0, 2), eo(.5), eio(.5), spring(0, 0)]).toEqual([1, 5, .5, .875, .5, 0]);
  expect(spring(10, 0)).toBeCloseTo(1);
  expect(pulse(0, 0)).toBe(0);
  expect(mix('red', 'blue', 2)).toBe('color-mix(in srgb, blue 100%, red)');
});

it('장애 훅은 실제 mock 실행·501 준비 상태·오류를 처리하고 숫자 훅은 현재 값에서 이어진다', async () => {
  vi.useFakeTimers();
  const api = new MockApi();
  const flag = vi.spyOn(api, 'setChaosFlag');
  const create = vi.spyOn(api, 'createJob');
  const { result } = renderHook(() => {
    const job = useJobRun(api);
    return { job, lab: useLab(api, job.submit) };
  });
  await act(async () => { await result.current.lab.trigger('llm_invalid'); });
  expect(flag).toHaveBeenCalledWith('classifier', expect.objectContaining({ kind: 'llm_invalid', params: {} }));
  expect(create).toHaveBeenCalledWith(chaosCatalogue.find(item => item.kind === 'llm_invalid')!.text);
  expect(result.current.lab).toMatchObject({ busy: false, error: null, notice: '장애 실험: AI 응답 깨짐 (분류)' });
  await act(async () => { await vi.runAllTimersAsync(); });
  expect(result.current.job.rows.slice(0, 2).map(row => [row.agent, row.status, row.tone])).toEqual([
    ['classifier', 'retry', 'fail'], ['classifier', 'success', 'recovered'],
  ]);
  const prefill = await act(async () => result.current.lab.trigger('injection'));
  expect(prefill).toEqual({ prefill: chaosCatalogue.find(item => item.kind === 'injection')?.text });
  expect(flag).toHaveBeenCalledTimes(1);
  expect(create).toHaveBeenCalledTimes(1);
  await act(async () => { await result.current.lab.redTeam(); });
  expect(result.current.lab.notice).toBe('레드팀이 고른 장애: AI 응답 깨짐 (분류) · mock: 순서대로 고름');
  await act(async () => { await vi.runAllTimersAsync(); });
  flag.mockRejectedValueOnce(new ApiError(500, '플래그 실패'));
  await act(async () => { await result.current.lab.trigger('delay'); });
  expect(result.current.lab).toMatchObject({ busy: false, error: '플래그 실패', notice: null });

  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: '아직 구현 전' }), { status: 501 }));
  vi.stubGlobal('fetch', fetch);
  const run = vi.fn().mockResolvedValue(undefined);
  const live = renderHook(() => useLab(new LiveApi(), run));
  await act(async () => { await live.result.current.redTeam(); });
  expect(live.result.current).toMatchObject({ notice: '레드팀은 준비 중이에요', error: null, busy: false });
  expect(run).not.toHaveBeenCalled();
  expect(fetch).toHaveBeenCalledWith('/api/chaos/run', expect.objectContaining({ method: 'POST' }));

  const frames = new Map<number, FrameRequestCallback>();
  let frameId = 0;
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { frames.set(++frameId, callback); return frameId; });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => { frames.delete(id); });
  const now = vi.spyOn(performance, 'now').mockReturnValue(0);
  function frame(time: number) {
    const callbacks = [...frames.values()]; frames.clear();
    act(() => { callbacks.forEach(callback => callback(time)); });
  }
  const count = renderHook(({ target }: { target: number | null }) => useCountUp(target), { initialProps: { target: 100 as number | null } });
  expect(count.result.current).toBe(0);
  frame(450);
  expect(count.result.current).toBe(87.5);
  now.mockReturnValue(450);
  count.rerender({ target: 200 });
  expect(count.result.current).toBe(87.5);
  expect(frames.size).toBe(1);
  frame(1350);
  expect(count.result.current).toBe(200);
  count.rerender({ target: null });
  expect(count.result.current).toBeNull();
  expect(frames.size).toBe(0);
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  count.rerender({ target: 300 });
  expect(count.result.current).toBe(300);
  expect(frames.size).toBe(0);
});

it('지역이 없는 결과(공격 문구 차단)도 지역 문구에서 멈추지 않는다', async () => {
  const { regionText } = await import('../src/lib/labels');
  expect(regionText(undefined)).toBe('지역 확인 필요');
});
