import type { Agent, Category, Channel, ChaosKind, Draft, FailureCode, IncidentFault, Region } from '../api/types';
import { chaosCatalogue, failureNames } from './failures';

export const categoryLabel: Record<Category, string> = {
  streetlight: '가로등 고장', road_damage: '도로 파손', illegal_dumping: '쓰레기 무단투기', out_of_scope: '지원 범위 밖 · 안내만',
};
export const answerFieldLabel: Record<string, string> = {
  q_what: '불편 내용', q_confirm_location: '위치 확인',
  location_detail: '위치', category: '유형', occurred_at: '발생 시점', has_photo: '사진', pii: '다시 적은 내용',
};
export const methodLabel: Record<Channel['method'], string> = { web: '웹사이트', app: '앱', phone: '전화' };
export const agentLabel: Record<Agent, string> = {
  classifier: '분류', locator: '창구 찾기', verifier: '검증', writer: '문구 작성', backend: '총괄', mailer: '발송',
};
export const VIRTUAL_SEND_NOTICE = '가상 발송: 실제 기관에는 보내지 않아요';
export function kindLabel(kind: ChaosKind): string {
  const item = chaosCatalogue.find(item => item.kind === kind);
  if (!item) throw new Error(`알 수 없는 장애 유형: ${kind}`);
  return item.label;
}
export function incidentFaultLabel(fault: IncidentFault): string {
  const chaos = chaosCatalogue.find(item => item.kind === fault);
  if (chaos) return chaos.label;
  return failureNames[fault as FailureCode] ?? `알 수 없는 장애 유형: ${fault}`;
}
export function regionText(region?: Region): string {
  // 관할은 행정구역만, 장소 설명은 행정구역이 없을 때만 ("상도동 상도동 주민센터 앞" 겹침 방지)
  // 공격 문구 차단처럼 분류 전에 끝난 작업은 결과에 지역이 없다
  if (!region) return '지역 확인 필요';
  return [region.sido, region.sigungu, region.dong].filter(Boolean).join(' ') || region.place_text || '지역 확인 필요';
}
export function draftCopyText(draft: Draft): string { return `${draft.title}\n\n${draft.body}`; }
export function formatOffset(ms: number): string {
  const tenths = Math.floor(Math.max(0, ms) / 100);
  return `${String(Math.floor(tenths / 600)).padStart(2, '0')}:${String(Math.floor(tenths / 10) % 60).padStart(2, '0')}.${tenths % 10}`;
}

export function failureLabel(failure: FailureCode, data: Record<string, unknown>): string {
  return failure === 'LLM_OUTPUT_INVALID' && data.error_type === 'TimeoutError'
    ? 'AI 응답 시간 초과' : failureNames[failure];
}
