import final from '@contracts/samples/final_streetlight_output.json';
import locator from '@contracts/samples/locator_streetlight_output.json';
import { exampleQuestions, failureNames, stages, type StageId } from '../../../lib/failures';
import { categoryLabel } from '../../../lib/labels';

const STAGE_START = 1.8, STAGE_END = 9.3, STAGE_SCALE = 1.6;
export const pace = (t: number) => t <= STAGE_START ? t : t <= STAGE_END ? STAGE_START + (t - STAGE_START) * STAGE_SCALE : t + (STAGE_END - STAGE_START) * (STAGE_SCALE - 1);
export const LOOP = pace(14);
const remaining = final.channels.filter(channel => channel.channel_id !== locator.data.channels[0].channel_id);
export const HERO_COPY = {
  input: exampleQuestions[0].text,
  cardTitle: `${final.region.place_text} ${categoryLabel.streetlight}`,
  classification: `${categoryLabel.streetlight} · ${[final.region.sigungu, final.region.dong].filter(Boolean).join(' ')}`,
  channels: `공식 창구 ${locator.data.channels.length}곳`,
  failure: `첫 창구 · ${failureNames.LINK_DEAD}`,
  retry: '그 창구 빼고 다시', recovery: '자동 복구',
  channel: remaining[0], title: final.draft.title,
};
export type Beat = { t: number; stage: StageId | 'input' | 'result';
  kind: 'enter' | 'arrive' | 'done' | 'fail' | 'retry' | 'recover' | 'hold' | 'reset'; label?: string };
function stageLabel(id: StageId): string {
  const stage = stages.find(stage => stage.id === id);
  if (!stage) throw new Error(`알 수 없는 단계: ${id}`);
  return stage.label;
}
export const BEATS: readonly Beat[] = ([
  { t: 0, stage: 'input', kind: 'reset' },
  { t: .4, stage: 'input', kind: 'enter', label: HERO_COPY.input },
  { t: 1.6, stage: 'input', kind: 'done', label: HERO_COPY.input },
  { t: 1.8, stage: 'classify', kind: 'arrive', label: stageLabel('classify') },
  { t: 2.3, stage: 'classify', kind: 'done', label: HERO_COPY.classification },
  { t: 2.8, stage: 'locate', kind: 'arrive', label: stageLabel('locate') },
  { t: 3.3, stage: 'locate', kind: 'done', label: HERO_COPY.channels },
  { t: 3.8, stage: 'verify_channel', kind: 'arrive', label: stageLabel('verify_channel') },
  { t: 4.3, stage: 'verify_channel', kind: 'fail', label: HERO_COPY.failure },
  { t: 5, stage: 'locate', kind: 'retry', label: HERO_COPY.retry },
  { t: 5.5, stage: 'locate', kind: 'done', label: `공식 창구 ${remaining.length}곳` },
  { t: 6, stage: 'verify_channel', kind: 'arrive', label: stageLabel('verify_channel') },
  { t: 6.5, stage: 'verify_channel', kind: 'recover', label: HERO_COPY.recovery },
  { t: 7.2, stage: 'write', kind: 'arrive', label: stageLabel('write') },
  { t: 7.8, stage: 'write', kind: 'done', label: HERO_COPY.title },
  { t: 8.3, stage: 'verify_draft', kind: 'arrive', label: stageLabel('verify_draft') },
  { t: 8.8, stage: 'verify_draft', kind: 'done', label: stageLabel('verify_draft') },
  { t: 9.3, stage: 'result', kind: 'hold', label: `${HERO_COPY.channel.name} · ${HERO_COPY.channel.verified_at} · ${HERO_COPY.title}` },
  { t: 12, stage: 'result', kind: 'reset' },
  { t: 14, stage: 'input', kind: 'reset' },
] satisfies Beat[]).map(beat => ({ ...beat, t: pace(beat.t) }));

// Demo conversation comes from the same contract events as the running application.
import classified from '@contracts/samples/classifier_streetlight_output.json';
import verified from '@contracts/samples/verifier_streetlight_output.json';
import dead from '@contracts/samples/verifier_link_dead_output.json';
import written from '@contracts/samples/writer_streetlight_output.json';
import draftChecked from '@contracts/samples/verifier_streetlight_draft_output.json';
import { envelope } from '../../../api/mockScenario';
import { teamTalk } from '../../../lib/talk';
import type { TimelineEvent } from '../../../api/types';
const remainingIds = remaining.map(c => c.channel_id);
const demoPayloads = [classified, locator, dead,
  { ...locator, data: { ...locator.data, channels: remaining } },
  { ...verified, data: { ...verified.data, checks: verified.data.checks.filter(c => remainingIds.some(id => c.detail.startsWith(id))), passed_channel_ids: remainingIds } },
  written, draftChecked];
const demoTimes = [2.3, 3.3, 4.3, 5.5, 6.9, 7.8, 8.8].map(pace);
export const HERO_EVENTS: TimelineEvent[] = demoPayloads.map((sample, index) => {
  const payload = envelope(sample);
  return { id: index + 1, job_id: 'hero-example', agent: payload.author, event_type: payload.status,
    attempt: index === 3 || index === 4 ? 2 : 1, payload, created_at: new Date(demoTimes[index] * 1000).toISOString() };
});
export const HERO_HANDOFF = [1.3, 2.3, 3.3, 5.35, 5.9, 6.9, 7.8].map((t, index) => ({
  t: pace(t), station: [0, 1, 2, 1, 2, 3, 4][index], item: teamTalk(HERO_EVENTS.slice(0, index + 1), { running: false }).findLast(item => item.kind === 'handoff')!,
}));
export const HERO_TALK = demoTimes.map((t, index) => ({ t, items: teamTalk(HERO_EVENTS.slice(0, index + 1), { running: index < demoTimes.length - 1 }) }));
export function heroTalkAt(t: number) {
  return HERO_TALK.filter(frame => frame.t <= t).at(-1)?.items ?? teamTalk([], { running: true });
}
