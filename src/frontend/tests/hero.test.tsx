import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { HeroLoop } from '../src/citizen/components/hero/HeroLoop';
import { BEATS, HERO_EVENTS, HERO_HANDOFF, HERO_TALK, LOOP, pace } from '../src/citizen/components/hero/scenario';
import { seek } from '../src/citizen/components/hero/seek';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.history.replaceState({}, '', '/'); });

it('입력은 유지하고 단계 간격은 0.8초, 홀드는 2.7초, 루프는 18.5초다', () => {
  expect([pace(0), pace(.4), pace(1.6), pace(1.8)]).toEqual([0, .4, 1.6, 1.8]);
  expect(pace(2.3)).toBeCloseTo(2.6);
  expect(pace(2.8) - pace(2.3)).toBeCloseTo(.8);
  expect(pace(9.3)).toBeCloseTo(13.8);
  expect(pace(12) - pace(9.3)).toBeCloseTo(2.7);
  expect(pace(14) - pace(12)).toBeCloseTo(2);
  expect(LOOP).toBeCloseTo(18.5);
  expect(BEATS.at(-1)).toEqual({ ...BEATS[0], t: LOOP });
});

it('단계·대화·인계가 같은 시간표를 사용한다', () => {
  const times = [2.6, 4.2, 5.8, 7.72, 9.96, 11.4, 13];
  HERO_TALK.forEach((frame, index) => {
    expect(frame.t).toBeCloseTo(times[index]);
    expect(Date.parse(HERO_EVENTS[index].created_at) / 1000).toBeCloseTo(times[index]);
  });
  HERO_HANDOFF.forEach((frame, index) => expect(frame.t).toBeCloseTo([1.3, 2.6, 4.2, 7.48, 8.36, 9.96, 11.4][index]));
  expect(BEATS.find(beat => beat.kind === 'fail')?.t).toBeCloseTo(5.8);
  expect(BEATS.find(beat => beat.kind === 'recover')?.t).toBeCloseTo(9.32);
});

it('결정·재시도·리셋은 늦춘 시각에 시작하고 전환 길이는 유지한다', () => {
  expect(seek(.4).card.opacity).toBe(0);
  expect(seek(1.6).card.opacity).toBe(1);
  expect(seek(6.3).decisionStyle.opacity).toBe(0);
  expect(seek(6.56).decisionStyle.opacity).toBeCloseTo(1);
  expect(seek(6.9).arrow.opacity).toBe(0);
  expect(seek(7.22).retry.opacity).toBe(1);
  expect(seek(8.82).retry.opacity).toBe(0);
  expect(seek(16.5).card.opacity).toBe(1);
  expect(seek(17).card.opacity).toBe(0);
  expect(seek(17).checkDescription.opacity).toBe(1);
  expect(seek(0)).toEqual(seek(LOOP));
});

it('모션 감소는 완성된 결과에 멈추고 hero 쿼리는 실제 초를 사용한다', () => {
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  const { container, unmount } = render(<HeroLoop/>);
  expect((container.querySelector('.hero-ticket') as HTMLElement).style.opacity).toBe('1');
  expect((container.querySelector('.hero-retry') as HTMLElement).style.opacity).toBe('0');
  unmount(); window.history.replaceState({}, '', '/?hero=0');
  const probe = render(<HeroLoop/>);
  expect((probe.container.querySelector('.hero-card') as HTMLElement).style.opacity).toBe('0');
});
