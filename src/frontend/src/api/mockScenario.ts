import cs from '@contracts/samples/classifier_streetlight_output.json';
import cr from '@contracts/samples/classifier_road_damage_output.json';
import ci from '@contracts/samples/classifier_illegal_dumping_output.json';
import co from '@contracts/samples/classifier_out_of_scope_output.json';
import ns from '@contracts/samples/classifier_streetlight_needs_user_output.json';
import nr from '@contracts/samples/classifier_road_damage_needs_user_output.json';
import ni from '@contracts/samples/classifier_illegal_dumping_needs_user_output.json';
import ls from '@contracts/samples/locator_streetlight_output.json';
import lr from '@contracts/samples/locator_road_damage_output.json';
import li from '@contracts/samples/locator_illegal_dumping_output.json';
import lo from '@contracts/samples/locator_out_of_scope_output.json';
import vs from '@contracts/samples/verifier_streetlight_output.json';
import vr from '@contracts/samples/verifier_road_damage_output.json';
import vi from '@contracts/samples/verifier_illegal_dumping_output.json';
import vo from '@contracts/samples/verifier_out_of_scope_output.json';
import ws from '@contracts/samples/writer_streetlight_output.json';
import wr from '@contracts/samples/writer_road_damage_output.json';
import wi from '@contracts/samples/writer_illegal_dumping_output.json';
import wo from '@contracts/samples/writer_out_of_scope_output.json';
import ds from '@contracts/samples/verifier_streetlight_draft_output.json';
import dr from '@contracts/samples/verifier_road_damage_draft_output.json';
import di from '@contracts/samples/verifier_illegal_dumping_draft_output.json';
import dO from '@contracts/samples/verifier_out_of_scope_draft_output.json';
import fs from '@contracts/samples/final_streetlight_output.json';
import fr from '@contracts/samples/final_road_damage_output.json';
import fi from '@contracts/samples/final_illegal_dumping_output.json';
import invalidClassifier from '@contracts/samples/classifier_llm_invalid_output.json';
import invalidWriter from '@contracts/samples/writer_llm_invalid_output.json';
import dead from '@contracts/samples/verifier_link_dead_output.json';
import unofficial from '@contracts/samples/verifier_not_official_output.json';
import stale from '@contracts/samples/verifier_stale_info_output.json';
import pii from '@contracts/samples/verifier_pii_in_draft_output.json';
import type { Category, Channel, ClassifierData, FailureCode, StageResult, TimelineEvent } from './types';

export function literal<T extends string>(value: string, allowed: readonly T[]): T {
  const found = allowed.find(item => item === value);
  if (found === undefined) throw new Error(`계약 샘플 값 오류: ${value}`);
  return found;
}
export function envelope(raw: { author: string; status: string; data: Record<string, unknown>; failure: string | null }): StageResult {
  return { task_id: '', author: literal<TimelineEvent['agent']>(raw.author, ['classifier', 'locator', 'verifier', 'writer', 'backend', 'mailer']),
    status: literal<StageResult['status']>(raw.status, ['success', 'retry', 'failed', 'needs_user']), data: structuredClone(raw.data),
    failure: raw.failure === null ? null : literal<FailureCode>(raw.failure, ['LLM_OUTPUT_INVALID', 'NEEDS_MORE_INFO', 'OUT_OF_SCOPE', 'NO_CHANNEL', 'LINK_DEAD', 'NOT_OFFICIAL', 'STALE_INFO', 'PII_IN_DRAFT', 'DRAFT_MISMATCH', 'INJECTION_BLOCKED', 'SERVICE_UNAVAILABLE', 'MAIL_SEND_FAILED', 'MAIL_BLOCKED']), incident_id: null };
}
export function channels(raw: typeof ls.data.channels): Channel[] {
  return raw.map(channel => ({ ...channel, method: literal<Channel['method']>(channel.method, ['web', 'app', 'phone']) }));
}
const samples = {
  streetlight: { classifier: cs, needs: ns, locator: ls, verifier: vs, writer: ws, draft: ds, final: fs },
  road_damage: { classifier: cr, needs: nr, locator: lr, verifier: vr, writer: wr, draft: dr, final: fr },
  illegal_dumping: { classifier: ci, needs: ni, locator: li, verifier: vi, writer: wi, draft: di, final: fi },
  out_of_scope: { classifier: co, needs: ns, locator: lo, verifier: vo, writer: wo, draft: dO, final: fs },
};
export const faultSamples = { llm_invalid: invalidClassifier, writer_invalid: invalidWriter, link_dead: dead, not_official: unofficial, stale_info: stale, pii_leak: pii };
export function scenario(text: string) {
  const category: Category = /가로등/.test(text) ? 'streetlight' : /도로|파손|포트홀|움푹|보도블록/.test(text) ? 'road_damage' : /쓰레기|무단투기/.test(text) ? 'illegal_dumping' : 'out_of_scope';
  return { category, ...samples[category] };
}
export function classification(raw: typeof ns.data | typeof cs.data | typeof co.data, category: Category): ClassifierData {
  return { ...structuredClone(raw), category,
    missing: raw.missing.map(value => literal<ClassifierData['missing'][number]>(value, ['category', 'location_detail', 'occurred_at', 'has_photo'])),
    questions: raw.questions.map(question => ({ ...question, field: literal<ClassifierData['questions'][number]['field']>(question.field, ['category', 'location_detail', 'occurred_at', 'has_photo']) })) };
}
