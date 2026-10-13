import { useContext, useState } from 'react';
import type { Question } from '../../api/types';
import { LanguageContext, speechLang, t } from '../../lib/uiText';
import { MicButton } from './MicButton';
export function QuestionCard({ questions, answer, answers = {}, lang: selected }: { questions: Question[]; answer: (answers: Record<string, string>) => Promise<void>; answers?: Record<string, string>; lang?: import('../../api/types').Lang }) {
  const context = useContext(LanguageContext), lang = selected ?? context;
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(questions.map(q => [q.id, answers[q.id] ?? answers[q.field] ?? ''])));
  return <form className="panel question-card" onSubmit={event => { event.preventDefault(); void answer(values); }}>
    <span className="badge ask">추가 질문</span><h2>조금만 더 알려 주세요</h2>
    {questions.map(q => <div key={q.id}><label>{lang !== 'ko' && q.translatedText ? <>{q.translatedText}<small className="original-text">{q.text}</small></> : q.text}<input value={values[q.id] ?? ''} onChange={e => setValues({ ...values, [q.id]: e.target.value })}/></label><MicButton lang={speechLang[lang]} disabled={[...(values[q.id] ?? '')].length > 500} onText={text => setValues(v => ({ ...v, [q.id]: [v[q.id], text].filter(Boolean).join(' ') }))}/></div>)}
    <button className="primary" disabled={!questions.length || questions.some(q => !values[q.id]?.trim() || [...values[q.id]].length > 500)}>{t(lang, 'answer')}</button>
  </form>;
}
