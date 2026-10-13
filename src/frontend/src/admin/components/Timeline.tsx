import { LayoutReveal } from './LayoutReveal';
import { Send } from 'lucide-react';
import type { TimelineEvent } from '../../api/types';
import { stages, SEND_STAGE } from '../../lib/failures';
import { formatOffset } from '../../lib/labels';
import type { TimelineRow } from '../../lib/timeline';
export function rowLabel(row: TimelineRow) { return row.stage === 'send' ? SEND_STAGE.label : row.stage === 'input' ? '입력 확인' : stages.find(s => s.id === row.stage)!.label; }
export function Timeline({ rows, events }: { rows: TimelineRow[]; events: TimelineEvent[] }) {
  const descriptions = { classify: '민원 유형과 위치 확인', locate: '공식 신고 창구 찾기', verify_channel: '공식 주소 · 링크 · 확인일 검증', write: '신고 문구 초안 작성', verify_draft: '유형 · 개인정보 검증', input: '입력 내용 검사', send: '가상 담당자에게 발송' };
  return <LayoutReveal mobileOnly><div className="timeline panel" role="log" aria-label="에이전트 타임라인" aria-live="polite" aria-relevant="additions">
    {!rows.length && <p className="empty">민원을 받아 첫 단계를 준비하고 있어요</p>}
    {rows.map(row => {
      const data = events.find(e => e.id === row.id)?.payload.data;
      const attempts = typeof data?.attempts === 'number' ? data.attempts : row.attempt;
      const note = row.failure === 'MAIL_SEND_FAILED' ? `보낼 편지함에 넣음 · 시도 ${attempts}회` : row.note ?? descriptions[row.stage];
      const badge = row.tone === 'recovered' ? '자동 복구' : row.tone === 'warn' ? '확인 필요' : row.tone === 'ask' ? '추가 질문' : row.tone === 'fail' ? row.failure === 'MAIL_BLOCKED' ? '안전장치로 차단' : `${row.attempt}차 실패` : `${row.attempt}차 성공`;
      return <div className={`log-row ${row.tone}`} key={row.id} data-tone={row.tone}>
        <time>{formatOffset(Date.parse(row.at) - Date.parse(rows[0].at))}</time><strong><span className="log-icon">{row.stage === 'send' && <Send/>}</span>{rowLabel(row)}</strong><span className="log-description">{row.failure && <span>{row.failure} · {row.failureName}<br/></span>}{note}</span><span className={`badge ${row.tone}`}>{badge}</span>
      </div>;
    })}
  </div></LayoutReveal>;
}
