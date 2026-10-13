import { citizenError } from '../lib/citizenText';
import { DEMO_ACCOUNT, useAccount } from '../lib/demoAccount';
import { PhotoPreview } from './PhotoPreview';
import { t } from '../../lib/uiText';
import { DraftReview, fixIssue } from './DraftReview';
import { LayoutReveal } from './LayoutReveal';
import { useEffect, useRef, useState } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import { Check, Copy, ExternalLink, Globe, Mail, Phone, Send, Smartphone } from 'lucide-react';
import type { Api } from '../../api/client';
import type { DraftReviewResult, ReviewIssue, SendDraft, FinalResult } from '../../api/types';
import type { useJobRun } from '../../hooks/useJobRun';
import { useMailbox } from '../../hooks/useMailbox';
import { categoryLabel, methodLabel, regionText } from '../../lib/labels';
const methods = { web: Globe, app: Smartphone, phone: Phone };
const receiptTitle = (subject: string) => subject.split(/(\d{4}-\d{4}-\d{4})/).map((part, i) =>
  i % 2 ? <span className="receipt-id" key={i}>{part}</span> : part);
export function ResultView({ result, run, api }: { result: FinalResult; run: ReturnType<typeof useJobRun>; api: Api }) {
  const [selected, setSelected] = useState(0);
  const results = run.results.length ? run.results : [result];
  if (results.length === 1) return <ResultCard result={result} run={run} api={api} index={0}/>;
  return <><div className="result-tabs" role="tablist" aria-label="민원 유형">{results.map((item, index) =>
    <button key={item.category} role="tab" id={`result-tab-${index}`} aria-controls={`result-panel-${index}`} aria-selected={selected === index} onClick={() => setSelected(index)}>{categoryLabel[item.category]}</button>)}</div>
    {results.map((item, index) => <div key={item.category} id={`result-panel-${index}`} role="tabpanel" aria-labelledby={`result-tab-${index}`} hidden={selected !== index}>
      <ResultCard result={item} run={run} api={api} index={index}/></div>)}</>;
}
function ResultCard({ result, run, api, index }: { result: FinalResult; run: ReturnType<typeof useJobRun>; api: Api; index: number }) {
  const { account, login } = useAccount(), outside = result.category === 'out_of_scope', failed = run.phase === 'failed';
  const localized = run.lang !== 'ko' ? run.i18n?.results[index] : undefined;
  const bilingual = (original: string, translated?: string) => translated ? <>{translated}<small className="original-text">{original}</small></> : original;
  const mail = run.mails[index] ?? { status: 'idle', receiptNo: null, error: null };
  const taskId = index ? `${run.jobId}:${index}` : run.jobId;
  const [title, setTitle] = useState(result.draft.title), [body, setBody] = useState(result.draft.body);
  const [reviewed, setReviewed] = useState<SendDraft | null>(result.draft), [checked, setChecked] = useState<DraftReviewResult | null>(null);
  const [reviewing, setReviewing] = useState(false), [reviewError, setReviewError] = useState('');
  const revision = useRef(0);
  useEffect(() => () => { revision.current++; }, []);
  const needsReview = !reviewed || reviewed.title !== title || reviewed.body !== body;
  function edit(field: 'title' | 'body', value: string) {
    revision.current++; setReviewing(false); setChecked(null); setReviewed(null); setReviewError('');
    if (field === 'title') setTitle(value); else setBody(value);
  }
  async function verify(draft: SendDraft, citizen?: typeof DEMO_ACCOUNT) {
    const token = ++revision.current;
    setReviewing(true); setReviewError('');
    try {
      const response = await run.review(index, draft);
      if (token !== revision.current) return;
      setChecked(response);
      if (response.verdict === 'ok') {
        setReviewed(draft);
        if (citizen) await run.send({ ...draft, citizen }, index);
      }
    } catch (error) { if (token === revision.current) setReviewError(error instanceof Error ? error.message : '검토하지 못했어요. 다시 시도해 주세요'); }
    finally { if (token === revision.current) setReviewing(false); }
  }
  function send(anyway = false) {
    const citizen = account ?? login();
    return needsReview && !anyway ? verify({ title, body }, citizen) : run.send({ title, body, citizen }, index);
  }
  function fix(issue?: ReviewIssue) {
    if (!checked) return;
    const next = issue ? fixIssue({ title, body }, issue) : checked.fixed;
    setTitle(next.title); setBody(next.body); setReviewed(null); setChecked(null);
    void verify(next);
  }
  const [copied, setCopied] = useState(false), [copyError, setCopyError] = useState('');
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined), reduce = useReducedMotion();
  const active = !!account && !outside && mail.status !== 'idle', mailbox = useMailbox(api, { active });
  useEffect(() => () => clearTimeout(timer.current), []);
  const runStartedAt = Date.parse(run.events[0]?.created_at ?? '');
  // 이번 실행 메일만: 실행 시작 전 메일은 빼고, 회신은 이번 접수번호가 든 것만(mock은 연속 실행 시각이 겹침)
  const messages = mailbox.messages.filter(item => !(Date.parse(item.created) < runStartedAt) && (item.kind === 'reply' ? !!mail.receiptNo && item.subject.includes(mail.receiptNo) : item.subject === `[가상 민원] ${title}`));
  const mailData = run.events.filter(e => e.agent === 'mailer' && e.payload.task_id === taskId && e.payload.status === 'success').at(-1)?.payload.data;
  const text = (key: string) => typeof mailData?.[key] === 'string' ? mailData[key] : '';
  async function copy() {
    try { await navigator.clipboard.writeText(`${title}\n\n${body}`); setCopied(true); setCopyError(''); clearTimeout(timer.current); timer.current = setTimeout(() => setCopied(false), 1500); }
    catch { setCopyError('복사하지 못했어요. 문구를 선택해 직접 복사해 주세요'); }
  }
  return <>{outside && <section className="panel scope-banner" data-testid="out-of-scope-banner"><h2 className="badge warn">이 민원은 지원 범위 밖이에요</h2><p>저희가 처리한 게 아니라 <strong>전국 공통 창구를 안내만</strong> 드려요.</p><p>지원 범위: 서울 동작구의 가로등 고장·도로 파손·쓰레기 무단투기</p></section>}<div className="result-columns"><div>
    <p className="result-category">{categoryLabel[result.category]}<span> · {regionText(result.region)}</span></p>
    <section className="panel channels"><h2>공식 신고 창구</h2><p className="muted">신고는 직접 해 주세요. 아래 창구에서 제출할 수 있어요.</p>
      {!result.channels.length && <p>확인된 창구가 없어요. 확인 필요 안내를 살펴봐 주세요.</p>}
      {result.channels.map((channel, i) => { const Icon = methods[channel.method]; return <div className="channel-row" key={channel.channel_id}>
        <Icon/><div><h3>{bilingual(channel.name, localized?.channels[i]?.name)}</h3><p>{bilingual(channel.org, localized?.channels[i]?.org)} · {methodLabel[channel.method]}</p><small>확인일 {channel.verified_at}</small></div><a className="open-link" href={channel.url} target="_blank" rel="noopener noreferrer">열기<ExternalLink/></a>
      </div>; })}
    </section>
    {outside && result.channels.length > 0 && <p>여기서 직접 신고하세요</p>}
    {!outside && !failed && <section className="panel draft"><h2>신고 문구 초안</h2><p className="muted">내용을 확인하고 바로 고쳐 쓰세요.</p>
      {localized && title === result.draft.title && body === result.draft.body && <details className="translated-preview"><summary>{t(run.lang, 'preview')}</summary><strong>{localized.draft.title}</strong><p>{localized.draft.body}</p></details>}
      <label>제목<input maxLength={200} value={title} disabled={['sending', 'queued', 'sent'].includes(mail.status)} onChange={e => edit('title', e.target.value)}/></label>
      <label>본문<textarea maxLength={5000} rows={9} value={body} disabled={['sending', 'queued', 'sent'].includes(mail.status)} onChange={e => edit('body', e.target.value)}/></label>
      {run.photo && <div><PhotoPreview photo={run.photo}/><p>{t(run.lang, 'photoNotice')}</p></div>}
      <p className="muted" role="status">{reviewing ? '검토 중이에요' : needsReview ? '검토 필요' : '검토됨'}</p>
      {checked && checked.verdict !== 'ok' && <DraftReview lang={run.lang} draft={{ title, body }} review={checked} onFix={fix} disabled={reviewing || ['sending', 'queued', 'sent'].includes(mail.status)}/>}
      {reviewError && <p className="danger" role="alert">{citizenError(reviewError, '검토하지 못했어요. 다시 시도해 주세요')}</p>}
      {result.draft.fill_in.length > 0 && <div className="fill-in"><strong>보내기 전 채워 주세요</strong><ul>{result.draft.fill_in.map((field, i) => <li key={i}>{field.label}: {field.placeholder}</li>)}</ul></div>}
      <div className="actions wrap"><button onClick={() => void copy()}>{copied ? <Check/> : <Copy/>}{copied ? '복사됨' : '복사'}</button></div>
      {copyError && <p role="status">{copyError}</p>}
      <div className="virtual-send">
      <div className="actions wrap"><button className={`primary${mail.status === 'sent' ? ' done' : mail.status === 'queued' ? ' waiting' : ''}`} onClick={() => void send()} disabled={reviewing || (!!checked && checked.verdict !== 'ok') || !title.trim() || !body.trim() || !result.channels.length || ['sending', 'queued', 'sent'].includes(mail.status)}><Send/>{mail.status === 'sent' ? '보냈어요' : mail.status === 'queued' ? '보낼 편지함에서 대기 중' : mail.status === 'sending' ? '보내는 중' : reviewing ? '검토 중이에요' : account ? '내 계정으로 보내기' : '로그인하고 보내기'}</button>
      {checked?.verdict === 'warn' && <button disabled={reviewing || !title.trim() || !body.trim() || ['sending', 'queued', 'sent'].includes(mail.status)} onClick={() => void send(true)}>{t(run.lang, 'anyway')}</button>}</div>
      <small className="virtual-note">이 사이트의 메일 발송으로 <strong>{DEMO_ACCOUNT.name} 명의</strong>로 보내요. 실제 기관으로는 가지 않아요(시연)</small>
      {mail.status === 'error' && <p className="danger" role="alert">{mail.error === 'MAIL_BLOCKED' ? '안전장치로 차단했어요. 가상 담당자에게만 보낼 수 있어요.' : citizenError(mail.error, '가상 발송을 마치지 못했어요. 잠시 뒤 다시 보내 주세요')}</p>}
      {mailData && <LayoutReveal enter className="receipt-reveal"><div className="receipt"><span className="badge recovered">보냈어요{mail.receiptNo && <> · 접수번호 <span className="receipt-id">{mail.receiptNo}</span></>}</span><h3>{text('dept')}</h3><p>{text('officer')} · <span className="receipt-email">{text('to')}</span></p></div></LayoutReveal>}</div>
    </section>}
  </div><aside className="result-aside">
    {!failed && run.confirmNeeded.length > 0 && <section className="panel result-confirm"><span className="badge warn">확인 필요</span><ul>{run.confirmNeeded.map((line, i) => <li key={i}>{bilingual(line, run.lang !== 'ko' ? run.i18n?.confirm_needed[i] : undefined)}</li>)}</ul></section>}
    <section className="panel sources"><h2>안내의 근거</h2>{result.sources.map((source, i) => <p key={i}><a href={source.url} target="_blank" rel="noopener noreferrer">{source.url}<ExternalLink/></a><small>확인일 {source.checked_at}</small></p>)}</section>
    {!outside && !failed && <LayoutReveal className="mailbox-reveal"><section className="ink-panel mailbox"><h2><Mail/>내 메일함{account && <> — {account.email}</>}</h2>
      <div role="log" aria-live="polite">{!account ? <p className="empty">로그인하면 보낸 민원과 회신이 여기에 보여요</p> : <>
        {!active ? <p className="empty">보내면 메일과 회신이 여기에 도착해요</p> : !mailbox.connected ? <p className="empty">메일함에 연결할 수 없어요</p> : !messages.length && <p className="empty">아직 도착한 메일이 없어요</p>}
        {(['report', 'reply'] as const).map(kind => <section key={kind}><h3>{kind === 'report' ? '보낸 민원' : '받은 회신'}</h3>{messages.filter(item => item.kind === kind && (kind !== 'reply' || item.to.includes(account.email))).map(item => <motion.article layout={!reduce} initial={reduce ? false : { y: -20 }} animate={{ y: 0 }} transition={{ duration: reduce ? 0 : .3, ease: [.23, 1, .32, 1] }} className={`mail-item ${item.kind}`} key={item.id}>
          <small>{new Date(item.created).toLocaleTimeString('ko-KR')}</small><h3>{receiptTitle(item.subject.replace(/^\[가상 (민원|회신)\] /, ''))}</h3><p>{item.snippet.replaceAll('가상 ', '')}</p><small className="mail-addresses">{kind === 'report' ? '받는 곳(To)' : '받는 계정'}: {item.to.join(', ')}</small>
        </motion.article>)}</section>)}</>}</div>
      <p className="muted-light">시연용 메일 서버라 실제 기관엔 가지 않아요</p>
    </section></LayoutReveal>}
  </aside></div></>;
}
