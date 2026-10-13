import { useState } from 'react';
import { FlaskConical, ShieldAlert } from 'lucide-react';
import type { useLab } from '../../hooks/useLab';
import { chaosCatalogue } from '../../lib/failures';
import { agentLabel } from '../../lib/labels';
const descriptions = {
  llm_invalid: '실제 모델이 형식을 깨뜨리면 대체 모델로 복구해요.', link_dead: '열리지 않는 링크를 제외하고 다른 공식 창구를 찾아요.',
  stale_info: '확인일이 오래된 창구에 확인 필요를 표시해요.', pii_leak: '연락받고 싶다는 민원에서 작성 문구에 연락처가 섞이는 장애를 넣어요. 검사가 찾아 지워요.',
  not_official: '공식 주소가 아닌 창구를 제외하고 다시 찾아요.', delay: '응답을 늦춰 기다렸다 다시 부르게 해요. 실행에 15초 넘게 걸려요.',
  mail_down: '메일 서버를 잠시 멈춰요. 발송에 실패하면 서버 복귀 후 보낼 편지함에서 재발송해요.', injection: '공격 문구를 입력창에 넣어요. 직접 보내면 차단을 확인할 수 있어요.',
};
export function LabPage({ lab, active, submit }: { lab: ReturnType<typeof useLab>; active: boolean; submit: (text: string) => Promise<void> }) {
  const [input, setInput] = useState<string | null>(null), disabled = active || lab.busy;
  return <main className="page lab"><div className="page-heading"><h1>장애 실험실</h1><p>일부러 망가뜨리고, 복구를 지켜보세요</p><p>장애마다 그 장애에 맞는 민원 예시로 실행해요. 빨강에서 초록으로 바뀌는 과정을 따라가 보세요.</p></div>
    {active && <p className="lab-running"><a href="#/live">실험 진행 중 — 실시간 관제에서 보기</a></p>}
    {lab.notice && <p className="notice" role="status">{lab.notice}</p>}{lab.error && <p role="alert" className="danger">{lab.error}</p>}
    <section className="panel lab-list" aria-label="장애 실험 목록">{chaosCatalogue.map(item => <div className="lab-row" key={item.kind}>
      <span className="station-dot"/><div><h2>{item.label}<small>{item.target ? agentLabel[item.target] : '입력 안전장치'}</small></h2><p>{descriptions[item.kind]}</p></div>
      <button type="button" disabled={disabled} aria-label={`${item.label} ${item.kind === 'injection' ? '입력하기' : '일으키기'}`} onClick={() => { void lab.trigger(item.kind).then(result => { if (result) setInput(result.prefill); }); }}>{item.kind === 'injection' ? <ShieldAlert/> : <FlaskConical/>}{item.kind === 'injection' ? '입력하기' : '일으키기'}</button>
    </div>)}<div className="lab-row red-team"><span className="station-dot"/><div><h2>레드팀에게 맡기기<small>장애 선택</small></h2><p>레드팀이 고른 장애와 이유를 확인하고 실행해요.</p></div><button type="button" disabled={disabled} onClick={() => void lab.redTeam()}>레드팀 실행</button></div></section>
    {input !== null && <form className="lab-inject panel" onSubmit={event => { event.preventDefault(); if (!disabled && input.trim()) void submit(input); }}><label htmlFor="lab-injection-text">공격 문구</label><textarea id="lab-injection-text" value={input} onChange={event => setInput(event.target.value)} disabled={disabled} rows={3} maxLength={500}/><button type="submit" disabled={disabled || !input.trim()}>보내기</button></form>}
    <p className="lab-guide">장애를 선택하고 실행하면 실시간 관제에서 감지·우회·복구와 DB 기록을 확인할 수 있어요.</p>
  </main>;
}
