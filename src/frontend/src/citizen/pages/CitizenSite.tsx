import { DemoAccountContext, useDemoAccount } from '../lib/demoAccount';
import { citizenStages, isOutOfScope } from '../lib/scope';
import { useState } from 'react';
import { Activity } from 'lucide-react';
import { LayoutGroup, MotionConfig } from 'motion/react';
import defaultApi, { type Api } from '../../api';
import { useJobRun } from '../../hooks/useJobRun';
import { answerFieldLabel } from '../../lib/labels';
import { LanguageContext } from '../../lib/uiText';
import { AskForm } from '../components/AskForm';
import { HeroLoop } from '../components/hero/HeroLoop';
import { Pipeline } from '../components/Pipeline';
import { CitizenStatus } from '../components/CitizenStatus';
import { QuestionCard } from '../components/QuestionCard';
import { HomeSections, RouteSteps, homeSteps, resultSteps, SiteFooter } from '../components/InfoSections';
import { ResultView } from '../components/ResultView';
import { citizenError, citizenFailures } from '../lib/citizenText';
import '../../styles/tokens.css';
import '../styles/citizen.css';

export function CitizenSite({ api = defaultApi }: { api?: Api }) {
  const [input, setInput] = useState(''), run = useJobRun(api);
  const account = useDemoAccount(), outside = isOutOfScope(run.events, run.result, run.results);
  const running = ['running', 'submitting'].includes(run.phase);
  const sending = Object.values(run.mails).some(mail => ['sending', 'queued'].includes(mail.status));
  const blocked = run.rows.some(row => row.failure === 'INJECTION_BLOCKED');
  // 노선도 5단계를 랜딩 4단계 안내(분류·창구·문구·접수)에 맞춰 지금 어디쯤인지 표시
  const pending = citizenStages(run.stages, outside).findIndex(stage => stage.status !== 'done' && stage.status !== 'recovered' && stage.status !== 'warn');
  const step = pending < 0 ? 3 : [0, 1, 1, 2, 2][pending];
  const status = running ? '안내를 준비하고 있어요' : run.phase === 'success' ? outside ? run.result?.channels.length ? '안내만 했어요' : '확인 필요' : '안내 준비 완료'
    : run.phase === 'needs_user' ? '답변을 기다리고 있어요' : '안내를 마치지 못했어요';
  return <MotionConfig reducedMotion="user"><LanguageContext.Provider value={run.lang}><DemoAccountContext.Provider value={account}>
    <header className="top"><a className="brand" href="#/" onClick={() => run.reset()}><span><Activity/></span>생활 민원 길잡이</a><span className="scope">동작구 가로등·도로·쓰레기 민원</span><div className="demo-account">{account.account ? <><span className="badge ask" data-testid="demo-account">{account.account.name} · {account.account.email}</span><small>시연 계정</small><button data-testid="demo-logout" onClick={account.logout}>로그아웃</button></> : <button data-testid="demo-login" onClick={account.login}>로그인</button>}</div></header>
    {run.phase === 'idle' ? <main className="home"><div><h1>동네에서 불편한 일,<br/>한 줄이면 신고할 곳까지</h1><p className="intro"><span>동네 불편을 평소 말투로 적어 주세요.</span> <span>공식 창구 링크가 열리는지 확인하고 신고 문구까지 써 드려요.</span> <span>접수는 직접 하거나, 가상 발송으로 미리 연습해 볼 수 있어요.</span></p><AskForm value={input} onChange={setInput} onSubmit={() => void run.submit(input)} lang={run.lang} setLang={run.setLang} photo={run.photo} setPhoto={run.setPhoto}/></div><HeroLoop/><HomeSections/></main>
      : <main className="page guide"><div className="question-heading"><h1>“{run.text}”</h1>
        {Object.keys(run.answers).length > 0 && <p className="question-answers">추가 답변 · {Object.entries(run.answers).map(([field, value]) => `${run.answerLabels[field] ?? answerFieldLabel[field] ?? '추가 내용'}: “${value}”`).join(' · ')}</p>}
        <span className={`badge ${run.phase === 'failed' || run.phase === 'error' ? 'fail' : outside ? 'warn' : 'ask'}`}>{status}</span><small className="run-elapsed">{running ? `${run.elapsedSeconds}초째` : `총 ${run.elapsedSeconds}초`}</small></div>
        <Pipeline stages={run.stages} rows={run.rows} events={run.events} running={running || sending} compact={run.phase === 'success'} outside={outside} failed={run.phase === 'failed' || run.phase === 'error'}/>
        <CitizenStatus run={run}/>
        {running && <RouteSteps title="이렇게 진행돼요" steps={outside ? [['범위 밖 판정', '지원 범위 밖이에요'], ['전국 공통 창구 확인', '확인된 창구만 안내해요'], ['문구 작성·문구 검증', '안 함(범위 밖)'], ['접수 안내', '직접 신고해 주세요']] : homeSteps} current={outside ? 1 : step} states={outside ? ['warn', 'current', 'skipped', 'upcoming'] : undefined}/>}
        {run.phase === 'needs_user' && <QuestionCard key={run.events.length} questions={run.questions} answer={run.answer} answers={run.answers}/>}
        {run.phase === 'error' && <section className="panel error" role="alert"><h2>연결을 확인해 주세요</h2><p>{citizenError(run.error, '잠시 뒤 다시 시도해 주세요')}</p><button onClick={() => void run.retry()}>다시 시도</button></section>}
        {run.phase === 'failed' && <><section className="panel error" role="alert"><h2>{blocked ? '입력에 공격 문구가 있어 차단했어요' : '안내를 마치지 못했어요'}</h2><p>{blocked ? citizenFailures.INJECTION_BLOCKED.doing : '잠시 뒤 다시 물어봐 주세요'}</p>{run.confirmNeeded.length > 0 && <ul>{run.confirmNeeded.map((line, i) => <li key={i}>{citizenError(line, '확인이 필요한 내용이 있어요')}</li>)}</ul>}</section>{run.result && run.results.some(result => result.channels.length > 0) && <ResultView key={run.jobId} api={api} run={run} result={run.result}/>}</>}
        {run.phase === 'success' && run.result && <LayoutGroup><ResultView key={run.jobId} api={api} run={run} result={run.result}/>{run.result.category !== 'out_of_scope' && run.result.channels.length > 0 && <RouteSteps title="이제 이렇게 접수해 보세요" steps={resultSteps}/>}</LayoutGroup>}
        <button className="reset" onClick={() => run.reset()}>{running ? '취소하고 다시 묻기' : '다시 묻기'}</button>
      </main>}
    <SiteFooter/>
  </DemoAccountContext.Provider></LanguageContext.Provider></MotionConfig>;
}
