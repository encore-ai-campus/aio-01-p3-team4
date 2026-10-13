import { useEffect, useRef, useState } from 'react';
import { api as defaultApi } from '../api';
import type { Api } from '../api/client';
import type { ClassifierData, FinalResult, JobCreated, SendDraft, TimelineEvent } from '../api/types';
import { deriveRows, deriveStages } from '../lib/timeline';
import { replaySchedule } from '../lib/replay';
import { answerFieldLabel } from '../lib/labels';
import type { I18n, Lang } from '../api/types';

type Phase = 'idle' | 'submitting' | 'running' | 'needs_user' | 'success' | 'failed' | 'error';
type MailState = { status: 'idle' | 'sending' | 'queued' | 'sent' | 'error'; receiptNo: string | null; error: string | null };
interface RunState {
  lang: Lang; photo: File | null; i18n?: I18n;
  startedAt: number | null; elapsedSeconds: number;
  phase: Phase; jobId: string | null; text: string; events: TimelineEvent[];
  answers: Record<string, string>; answerLabels: Record<string, string>;
  questions: ClassifierData['questions']; result: FinalResult | null; results: FinalResult[];
  confirmNeeded: string[]; error: string | null;
  mail: MailState; mails: Record<number, MailState>;
}
const initial = (): RunState => ({ lang: 'ko', photo: null, startedAt: null, elapsedSeconds: 0, phase: 'idle', jobId: null, text: '', answers: {}, answerLabels: {}, events: [], questions: [], result: null, results: [], mails: {}, confirmNeeded: [], error: null, mail: { status: 'idle', receiptNo: null, error: null } });
function questionsFrom(value: unknown): ClassifierData['questions'] {
  if (!Array.isArray(value)) return [];
  return value.filter((q: unknown): q is ClassifierData['questions'][number] => {
    if (typeof q !== 'object' || q === null) return false;
    return 'id' in q && typeof q.id === 'string' && 'text' in q && typeof q.text === 'string' &&
      'field' in q && typeof q.field === 'string' && ['category', 'location_detail', 'occurred_at', 'has_photo', 'pii'].includes(q.field);
  });
}

export function useJobRun(api: Api = defaultApi) {
  const [state, setState] = useState<RunState>(initial);
  const current = useRef(state);
  const generation = useRef(0);
  const autoSend = useRef(false);
  const beforeSend = useRef<(() => Promise<void>) | undefined>(undefined);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const deliveries = useRef(new Map<number, { timer?: ReturnType<typeof setTimeout>; deadline?: ReturnType<typeof setTimeout> }>());
  function update(change: Partial<RunState>) {
    if (change.phase && !['running', 'submitting'].includes(change.phase) && current.current.startedAt !== null) {
      change = { ...change, elapsedSeconds: Math.floor((Date.now() - current.current.startedAt) / 1000), startedAt: null };
    }
    current.current = { ...current.current, ...change };
    setState(current.current);
  }
  function cancel() {
    generation.current += 1;
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
    deliveries.current.forEach(delivery => { clearTimeout(delivery.timer); clearTimeout(delivery.deadline); });
    deliveries.current.clear();
    return generation.current;
  }
  useEffect(() => () => { cancel(); }, []);
  useEffect(() => {
    if (state.startedAt === null) return;
    const started = state.startedAt;
    const clock = setInterval(() => {
      if (current.current.startedAt !== started) { clearInterval(clock); return; }
      update({ elapsedSeconds: Math.floor((Date.now() - started) / 1000) });
    }, 1000);
    return () => clearInterval(clock);
  }, [state.startedAt]);
  function fail(error: unknown, token: number) {
    if (generation.current === token) update({ phase: 'error', error: error instanceof Error ? error.message : '작업을 진행하지 못했습니다' });
  }
  function finish(job: JobCreated) {
    const result = job.state.result ?? job.state.results?.[0] ?? null;
    // 스텁 백엔드는 재시도 미구현이라 계약 밖 상태('retry')로 끝낼 수 있다 → 실패로 보여 준다
    const phase = job.status === 'needs_user' || job.status === 'success' ? job.status : 'failed';
    update({ phase, jobId: job.id, result, results: job.state.results ?? (result ? [result] : []),
      i18n: job.state.i18n, questions: questionsFrom(job.state.last_result?.data.questions).map(q => ({ ...q, translatedText: job.state.i18n?.questions[q.id] })),
      confirmNeeded: [...new Set([...(job.state.confirm_needed ?? []), ...(result?.confirm_needed ?? [])])],
    });
    if (phase === 'success' && result && autoSend.current) { autoSend.current = false; void send(result.draft, 0, beforeSend.current); }
  }
  function reveal(events: TimelineEvent[]) {
    const seen = new Set(current.current.events.map(e => e.id));
    update({ events: [...current.current.events, ...events.filter(e => !seen.has(e.id))] });
  }
  async function progress(job: JobCreated, token: number, retries = 0) {
    if (generation.current !== token) return;
    update({ phase: 'running', jobId: job.id, error: null });
    if (job.status === 'running') {
      let failures = 0;
      const poll = async () => {
        try {
          const [latest, events] = await Promise.all([api.getJob(job.id), api.getTimeline(job.id)]);
          if (generation.current !== token) return;
          failures = 0;
          reveal(events);
          if (latest.status !== 'running') finish(latest);
          else timer.current = setTimeout(() => { void poll(); }, 1000);
        } catch (error) {
          if (generation.current !== token) return;
          if (error instanceof TypeError && failures++ < 3) timer.current = setTimeout(() => { void poll(); }, 1000);
          else fail(error, token);
        }
      };
      timer.current = setTimeout(() => { void poll(); }, 1000);
      return;
    }
    let events: TimelineEvent[];
    try { events = await api.getTimeline(job.id); }
    catch (error) {
      if (generation.current !== token) return;
      if (error instanceof TypeError && retries < 3) timer.current = setTimeout(() => { void progress(job, token, retries + 1); }, 1000);
      else fail(error, token);
      return;
    }
    if (generation.current !== token) return;
    const seen = new Set(current.current.events.map(e => e.id));
    const fresh = events.filter(e => !seen.has(e.id));
    const schedule = replaySchedule(fresh);
    if (!fresh.length) { finish(job); return; }
    const play = (index: number) => {
      timer.current = setTimeout(() => {
        if (generation.current !== token) return;
        reveal([fresh[index]]);
        if (index === fresh.length - 1) finish(job);
        else play(index + 1);
      }, schedule[index] - (index ? schedule[index - 1] : 0));
    };
    play(0);
  }
  async function review(index: number, draft: SendDraft) {
    const { jobId } = current.current, token = generation.current;
    if (!jobId) throw new Error('작업을 찾지 못했어요');
    const result = await api.reviewJob(jobId, { ...draft, index });
    void api.getTimeline(jobId).then(events => {
      if (generation.current === token) reveal(events);
    }).catch(() => undefined);
    return result;
  }
  async function send(draft: SendDraft, index = draft.index ?? 0, prepare?: () => Promise<void>) {
    const { jobId, phase } = current.current;
    const mail = current.current.mails[index] ?? initial().mail;
    if (!jobId || phase !== 'success' || mail.status === 'sending' || mail.status === 'queued') return;
    const token = generation.current, delivery: { timer?: ReturnType<typeof setTimeout>; deadline?: ReturnType<typeof setTimeout> } = {};
    deliveries.current.set(index, delivery);
    const valid = () => generation.current === token && deliveries.current.get(index) === delivery;
    const stop = () => { clearTimeout(delivery.timer); clearTimeout(delivery.deadline); deliveries.current.delete(index); };
    const setMail = (mail: MailState) => update({ mail, mails: { ...current.current.mails, [index]: mail } });
    const taskId = index ? `${jobId}:${index}` : jobId;
    const seen = new Set(current.current.events.map(event => event.id));
    const error = (value: unknown) => {
      if (valid()) { stop(); setMail({ status: 'error', receiptNo: null, error: value instanceof Error ? value.message : '가상 발송에 실패했어요' }); }
    };
    setMail({ status: 'sending', receiptNo: null, error: null });
    // 초기 발송은 30초, 편지함 재발송은 회신을 기다리며 최대 2분 확인한다.
    delivery.deadline = setTimeout(() => {
      if (current.current.mails[index].status === 'queued') stop();
      else error(new Error('메일 발송 확인 시간이 지났어요'));
    }, 30_000);
    const poll = async () => {
      if (!valid()) return;
      try {
        const events = await api.getTimeline(jobId);
        if (!valid()) return;
        reveal(events);
        const latest = events.filter(event => event.agent === 'mailer' && event.payload.task_id === taskId && !seen.has(event.id)).at(-1);
        if (latest) {
          const sent = latest.payload.status === 'success', data = latest.payload.data;
          if (latest.payload.status === 'retry' && current.current.mails[index].status !== 'queued') {
            if (delivery.deadline !== null) clearTimeout(delivery.deadline);
            delivery.deadline = setTimeout(() => { stop(); }, 120_000);
          }
          setMail({ status: sent ? 'sent' : latest.payload.status === 'retry' ? 'queued' : 'error',
            receiptNo: typeof data.receipt_no === 'string' ? data.receipt_no : null,
            error: sent ? null : latest.payload.failure });
          if (sent || latest.payload.status === 'failed') { stop(); return; }
        }
        const { status, receiptNo } = current.current.mails[index];
        if (status === 'queued' && receiptNo) {
          const messages = await api.getMailbox().catch(() => null);
          if (!valid()) return;
          if (messages?.some(message => message.kind === 'reply' && message.subject.endsWith(`접수번호 ${receiptNo}`))) {
            stop();
            setMail({ status: 'sent', receiptNo, error: null });
            return;
          }
        }
        delivery.timer = setTimeout(() => { void poll(); }, 1000);
      } catch (value) { error(value); }
    };
    try {
      if (prepare) await prepare();
      if (!valid()) return;
      await api.sendJob(jobId, index ? { ...draft, index } : draft);
      if (valid()) delivery.timer = setTimeout(() => { void poll(); }, 1000);
    } catch (value) { error(value); }
  }
  async function submit(text: string, options: { autoSend?: boolean; beforeSend?: () => Promise<void> } = {}) {
    const token = cancel();
    const { lang, photo } = current.current;
    autoSend.current = options.autoSend ?? false;
    beforeSend.current = options.beforeSend;
    current.current = initial();
    update({ lang, photo, text, answers: {}, phase: 'submitting', startedAt: Date.now() });
    if ([...text].length > 500) { update({ phase: 'error', error: '민원 설명은 500자까지 입력해 주세요' }); return; }
    try {
      const job = await (lang === 'ko' && !photo ? api.createJob(text) : api.createJob(text, { lang, has_photo: !!photo }));
      // 개인정보 게이트에 걸리면 서버가 가린 원문을 준다 → 제목도 가린 문장으로
      if (generation.current === token && job.text) update({ text: job.text });
      await progress(job, token);
    }
    catch (error) { fail(error, token); }
  }
  async function retry() {
    const { jobId, text, elapsedSeconds } = current.current;
    if (!jobId) { await submit(text); return; }
    const token = cancel();
    update({ startedAt: Date.now() - elapsedSeconds * 1000 });
    await progress({ id: jobId, status: 'running', state: { question_round: 0, confirm_needed: [] } }, token);
  }
  async function answer(answers: Record<string, string>) {
    const id = current.current.jobId;
    if (!id || current.current.phase !== 'needs_user') return;
    const token = cancel();
    // 개인정보 되묻기 답은 원문을 새로 적은 것이라 '추가 답변'이 아니라 제목을 바꾼다
    const rewrite = answers.q_pii, extra = Object.fromEntries(Object.entries(answers).filter(([key]) => key !== 'q_pii'));
    update({ answerLabels: { ...current.current.answerLabels, ...Object.fromEntries(Object.keys(extra).map(key => [key,
      answerFieldLabel[key] ?? answerFieldLabel[current.current.questions.find(q => q.id === key)?.field ?? key.replace(/^q_/, '')] ?? '추가 내용'])) } });
    update({ answers: { ...current.current.answers, ...Object.fromEntries(Object.entries(extra).filter(([, value]) => value.trim())) } });
    update({ phase: 'submitting', startedAt: Date.now() - current.current.elapsedSeconds * 1000, questions: [], result: null, error: null });
    try {
      const job = await api.answerJob(id, answers);
      if (generation.current === token && rewrite !== undefined) update({ text: job.text ?? rewrite });
      await progress(job, token);
    }
    catch (error) { fail(error, token); }
  }
  function reset() { cancel(); const { lang } = current.current; current.current = { ...initial(), lang }; setState(current.current); }
  const rows = deriveRows(state.events, state.phase === 'success');
  const stages = deriveStages(rows, !['running', 'submitting'].includes(state.phase));
  return { ...state, rows, stages, submit, retry, answer, send, review, reset, setLang: (lang: Lang) => update({ lang }), setPhoto: (photo: File | null) => update({ photo }) };
}
