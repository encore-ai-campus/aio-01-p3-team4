import { t } from '../../lib/uiText';
import type { ReactNode } from 'react';
import type { DraftReviewResult, ReviewIssue, SendDraft } from '../../api/types';
import { LayoutReveal } from './LayoutReveal';

export function fixIssue(draft: SendDraft, issue: ReviewIssue): SendDraft {
  if (!issue.fix) return draft;
  const value = [...draft[issue.field]], replacement = issue.fix.replacement;
  const next = issue.start === null ? replacement + draft[issue.field]
    : value.slice(0, issue.start).join('') + replacement + value.slice(issue.end ?? issue.start).join('');
  return { ...draft, [issue.field]: next.replace(/[ \t]{2,}/g, ' ').trim() };
}
function highlight(value: string, issues: ReviewIssue[]) {
  const text = [...value], parts: ReactNode[] = [], spans = issues.filter(i => i.start !== null && i.end !== null);
  const points = [...new Set([0, text.length, ...spans.flatMap(i => [i.start!, i.end!])])].sort((a, b) => a - b);
  for (let i = 0; i < points.length - 1; i++) {
    const [start, end] = [points[i], points[i + 1]], covering = spans.filter(s => s.start! <= start && s.end! >= end);
    const content = text.slice(start, end).join('');
    parts.push(covering.length ? <mark key={start} className={`issue ${covering.some(s => s.severity === 'block') ? 'block' : 'warn'}`}>{content}</mark> : content);
  }
  return parts;
}
export function DraftReview({ draft, review, onFix, disabled, lang = 'ko' }: { lang?: import('../../api/types').Lang; draft: SendDraft; review: DraftReviewResult; onFix: (issue?: ReviewIssue) => void; disabled: boolean }) {
  return <LayoutReveal enter><section className="draft-review" aria-label="첨삭 결과"><div className="actions wrap"><h3>첨삭 결과</h3>
    <button disabled={disabled || !review.issues.some(i => i.fix)} onClick={() => onFix()}>{t(lang, 'fixAll')}</button></div>
    {(['title', 'body'] as const).map(field => <div key={field} className="review-preview"><strong>{field === 'title' ? '제목' : '본문'}</strong><p>{highlight(draft[field], review.issues.filter(i => i.field === field))}</p></div>)}
    <ul>{review.issues.map(issue => <li key={issue.id}><span aria-label={issue.severity === 'block' ? '필수 수정' : '경고'}>{issue.severity === 'block' ? '⛔' : '⚠'}</span> {issue.message}
      {issue.fix && <button disabled={disabled} onClick={() => onFix(issue)}>{t(lang, 'fix')}</button>}</li>)}</ul>
  </section></LayoutReveal>;
}
