import type { CSSProperties } from 'react';
import { BEATS, LOOP, heroTalkAt, HERO_HANDOFF, HERO_TALK, pace } from './scenario';
import { stages } from '../../../lib/failures';
import { clamp, eo, lerp, mix, prog } from './motion';
const time = (stage: string, kind: string) => BEATS.find(b => b.stage === stage && b.kind === kind)!.t;
const failAt = time('verify_channel', 'fail'), recoverAt = time('verify_channel', 'recover');
const wipe = (p: number): CSSProperties => ({ clipPath: `inset(0 ${100 * (1 - clamp(p))}% 0 0)` });
// All animated styles are a function of time. No accumulated spring state or CSS transitions.
export function seek(seconds: number) {
  const t = ((seconds % LOOP) + LOOP) % LOOP;
  const reset = eo(prog(t, pace(12), .5)), visible = 1 - reset;
  const enter = eo(prog(t, .4, 1.2));
  let position = 0, cardPosition = 0;
  // Retry is shown by the return arrow/ring; keep the card anchored while its note appears.
  for (const b of BEATS.filter(b => b.kind === 'arrive' || b.kind === 'retry')) {
    const target = stages.findIndex(s => s.id === b.stage);
    const progress = eo(prog(t, b.t - .5, .5));
    position = lerp(position, target, progress);
    if (b.kind === 'arrive') cardPosition = lerp(cardPosition, target, progress);
  }
  const expandAt = time('result', 'hold') - .4;
  const expand = eo(prog(t, expandAt, .4));
  position = lerp(position, 3.35, expand);
  cardPosition = lerp(cardPosition, 3.35, expand);
  const failure = eo(prog(t, failAt, .18)), recovery = eo(prog(t, recoverAt + .18, .18));
  const arrival = stages.map(s => time(s.id, 'arrive'));
  const dots = stages.map((s, i) => {
    const done = eo(prog(t, s.id === 'verify_channel' ? recoverAt : time(s.id, 'done'), .5)) * visible;
    let color = mix('#BFC8CD', '#2457D6', done); // 지나온 역 = 노선 색(--route)
    if (i === 2) {
      const recoveredColor = t < recoverAt + .09 ? mix('#D2412F', '#FFFFFF', prog(t, recoverAt, .09)) : mix('#FFFFFF', '#1E8A57', prog(t, recoverAt + .09, .09));
      color = mix(color, recoveredColor, failure * visible);
    }
    const arrived = BEATS.filter(b => b.stage === s.id && (b.kind === 'arrive' || b.kind === 'retry'));
    const ring = Math.max(0, ...arrived.map(b => Math.sin(Math.PI * prog(t, b.t, .65)))) * visible;
    return { dot: { borderColor: color, backgroundColor: mix('#FFFFFF', color, i === 2 ? failure * visible : done) },
      ring: { opacity: ring, transform: `scale(${1 + ring * .55})`, borderColor: color },
      label: { color: mix('#5B6B77', '#14212B', eo(prog(t, arrival[i], .5)) * visible) } };
  });
  const passed = reset === 1 ? 0 : lerp(clamp(position / 4), 1, expand);
  const talk = heroTalkAt(t), working = talk.findLast(item => item.kind === 'working');
  const workingStage = stages.find(s => s.label === working?.from);
  const workingBeat = BEATS.findLast(b => b.stage === workingStage?.id && b.t <= t);
  const workingComplete = workingBeat?.kind === 'done' || workingBeat?.kind === 'recover';
  const handoffs = HERO_HANDOFF;
  const handoff = handoffs.filter(item => item.t <= t).at(-1);
  const decision = talk.findLast(item => item.kind === 'decision');
  const lastEvent = HERO_TALK.filter(b => b.t <= t).at(-1)?.t ?? 0;
  const nextEvent = HERO_TALK.find(b => b.t > t)?.t ?? pace(12);
  const workingOpacity = eo(prog(t, lastEvent, .16)) * (1 - eo(prog(t, nextEvent - .15, .15))) * visible;
  const failureOpacity = failure * (1 - eo(prog(t, recoverAt, .15))) * visible;
  const arrowOpacity = eo(prog(t, pace(5), .2)) * (1 - eo(prog(t, recoverAt, .2))) * visible;
  const chipTime = handoff?.t ?? 0;
  const chipEnd = Math.min(chipTime + .8, (handoffs.find(item => item.t > t)?.t ?? LOOP) - .05);
  const chip = handoff ? eo(prog(t, chipTime, .15)) * (1 - eo(prog(t, chipEnd - .2, .2))) * visible : 0;
  return {
    working,
    workingStyle: { opacity: workingComplete ? 0 : workingOpacity },
    descriptions: stages.map((s, i) => i < 2 ? { ...wipe(reset === 1 ? 0 : eo(prog(t, time(s.id, 'done'), .2))), opacity: visible } : {}),
    handoff: handoff?.item.text ?? '', handoffTo: handoff?.item.to ?? '',
    handoffStyle: { opacity: chip, transform: handoff?.station === 0 ? 'translateY(var(--first-handoff-y))' : `translateY(calc(var(--hero-step) * ${(handoff?.station ?? 0) - 1}))` },
    decision: decision?.text ?? '', decisionStyle: { opacity: eo(prog(t, pace(4.65), .2)) * (1 - eo(prog(t, recoverAt, .15))) * visible },
    dots, line: { transform: `scaleY(${passed})`, opacity: visible },
    card: { transform: `translateY(calc(var(--card-step, var(--hero-step)) * ${reset === 1 ? 0 : cardPosition}))`, opacity: enter * visible },
    cardBase: { opacity: (1 - eo(prog(t, expandAt, .15))) * visible },
    ticket: { opacity: eo(prog(t, expandAt + .18, .22)) * visible },
    shell: { transform: `scaleY(${lerp(.58, 1, expand)})` },
    checkDescription: { opacity: t >= pace(12.5) ? 1 : 1 - eo(prog(t, failAt - .18, .15)) },
    failure: { opacity: failureOpacity }, recovery: { opacity: recovery * visible },
    retry: { opacity: eo(prog(t, pace(5), .2)) * (1 - eo(prog(t, pace(6), .2))) * visible },
    arrow: { strokeDashoffset: 1 - eo(prog(t, pace(5), .3)), opacity: arrowOpacity },
    arrowHead: { opacity: arrowOpacity },
  };
}
