import mailerSample from '@contracts/samples/mailer_output.json';
import queuedSample from '@contracts/samples/mailer_mail_down_output.json';
import unofficialInput from '@contracts/samples/verifier_not_official_input.json';
import staleInput from '@contracts/samples/verifier_stale_info_input.json';
import { ApiError, type Api, type RedTeamResult } from './client';
import type { DraftReviewResult, ChaosFlag, ChaosTarget, Draft, Incident, Job, JobCreated, MailerData, MailItem, Metrics, OpsHours, OpsJob, SendDraft, StageResult, TimelineEvent } from './types';
import { channels, classification, envelope, faultSamples, scenario } from './mockScenario';
import { chaosCatalogue } from '../lib/failures';
import { demoOps, mockOverview } from './mockOps';

type Run = { job: Job; events: TimelineEvent[]; clock: number; due: number; flow?: Generator<number, void>; deliveries: Record<number, { flow?: Generator<number, void>; due: number }>; channels: ReturnType<typeof channels>; draft: Draft };
const redTeam = chaosCatalogue.filter((item): item is Extract<typeof chaosCatalogue[number], { target: ChaosTarget }> => item.target !== undefined);
const iso = (time: number) => new Date(time).toISOString();
// Mock 증거는 계약 fixture와 이 실행의 모의 값만 사용한다. 실제 서비스 실행 증거와 구별한다.
const mockEvidence = (values: { broken?: string; fixed?: string }) => Object.fromEntries(
  Object.entries(values).map(([key, value]) => [key, `[mock] ${value}`.slice(0, 500)]));

// database/seeds/mock_contacts.csv와 같은 가상 담당자(mock 전용 사본). 실제 발송은 mailer가 CSV를 읽는다.
const MOCK_CONTACTS: Record<string, { dept: string; officer: string; email: string }> = {
  'national-all-01': { dept: '국민신문고 민원처리과(가상)', officer: '김가상 주무관', email: 'epeople-desk@minwon.example' },
  'national-all-02': { dept: '안전신문고 신고처리과(가상)', officer: '이가상 주무관', email: 'safety-desk@minwon.example' },
  'dongjak-streetlight-01': { dept: '동작구청 가로등 담당(가상)', officer: '박가상 주무관', email: 'dongjak-streetlight@minwon.example' },
  'dongjak-road_damage-01': { dept: '동작구청 도로관리과(가상)', officer: '최가상 주무관', email: 'dongjak-road@minwon.example' },
  'dongjak-illegal_dumping-01': { dept: '동작구청 청소행정과(가상)', officer: '정가상 주무관', email: 'dongjak-clean@minwon.example' },
};
function mockContact(channelId: string | undefined) {
  return (channelId && MOCK_CONTACTS[channelId]) || { dept: '가상 민원실', officer: '가상 담당자', email: `${channelId ?? 'unknown'}@minwon.example` };
}

export class MockApi implements Api {
  readonly mode = 'mock';
  private jobs = new Map<string, Run>();
  private flags = new Map<ChaosTarget, ChaosFlag>();
  private incidents: Incident[] = [];
  private mailbox: MailItem[] = [];
  private mailSequence = 0;
  private sequence = 0;
  private eventId = 0;
  private redIndex = 0;
  private readonly now: () => number;
  private readonly asyncMode: boolean;
  private readonly demo: ReturnType<typeof demoOps>;
  constructor(options: { async?: boolean; now?: () => number; opsDemo?: boolean } = {}) {
    this.now = options.now ?? Date.now;
    this.asyncMode = options.async ?? false;
    this.demo = options.opsDemo ? demoOps(this.now()) : { jobs: [], events: [], incidents: [] };
  }
  private lookup(id: string): Run {
    const run = this.jobs.get(id);
    if (!run) throw new ApiError(404, '작업을 찾지 못했어요');
    this.advance(run);
    return run;
  }
  private emit(run: Run, agent: ChaosTarget, payload: StageResult, attempt = 1): string {
    const at = iso(run.clock);
    const result = { ...payload, task_id: payload.task_id || run.job.id };
    run.events.push({ id: ++this.eventId, job_id: run.job.id, agent, event_type: result.status, attempt, payload: result, created_at: at });
    run.job.state.last_result = result;
    return at;
  }
  private finish(run: Run, status: Job['status']) {
    run.job.status = status;
    run.job.completed_at = status === 'success' || status === 'failed' ? iso(run.clock) : null;
  }
  private advance(run: Run) {
    // 제너레이터를 미리 끝내면 아직 발생하지 않은 결과와 복구 지표가 노출된다.
    while (run.flow && (!this.asyncMode || run.due <= this.now())) {
      run.clock = run.due;
      const next = run.flow.next();
      if (next.done) run.flow = undefined;
      else run.due = run.clock + next.value;
    }
    Object.values(run.deliveries).forEach(delivery => {
      while (delivery.flow && delivery.due <= this.now()) {
        run.clock = delivery.due;
        const next = delivery.flow.next();
        if (next.done) delivery.flow = undefined;
        else delivery.due = run.clock + next.value;
      }
    });
  }
  private start(run: Run, answers?: Record<string, string>) {
    run.clock = Math.max(this.now(), run.clock);
    const from = run.clock;
    run.job.status = 'running';
    run.job.completed_at = null;
    run.flow = this.pipeline(run, answers);
    const first = run.flow.next();
    if (!first.done) run.due = run.clock + first.value;
    this.advance(run);
    // 동기 응답은 지금 백엔드처럼 이미 끝난 기록이어야 한다 → 흉내 낸 소요 시간만큼 이번 실행 기록을 과거로 당긴다
    const ahead = run.clock - this.now();
    if (!this.asyncMode && ahead > 0) this.rewind(run, from, ahead);
  }
  private rewind(run: Run, from: number, ms: number) {
    const back = (value: string | null) => value !== null && Date.parse(value) >= from ? iso(Date.parse(value) - ms) : value;
    run.events.forEach(event => { event.created_at = back(event.created_at) ?? event.created_at; });
    run.job.created_at = back(run.job.created_at) ?? run.job.created_at;
    run.job.completed_at = back(run.job.completed_at);
    this.incidents.filter(item => item.job_id === run.job.id).forEach(item => {
      item.detected_at = back(item.detected_at); item.recovered_at = back(item.recovered_at);
    });
    run.clock -= ms;
  }
  private *call(run: Run, target: ChaosTarget, normal: StageResult, duration: number, draftMode = false, attempt = 1): Generator<number, StageResult> {
    yield duration;
    const pending = this.flags.get(target);
    // 개인정보 장애는 작성 단계의 출력 가드에서 복구한다.
    const flag = pending?.kind === 'pii_leak' && target !== 'writer' && !draftMode ? undefined : pending;
    if (!flag) { this.emit(run, target, normal, attempt); return normal; }
    this.flags.delete(target);
    const incident: Incident = { id: flag.incident_id, job_id: run.job.id, target, fault: flag.kind,
      injected_at: flag.injected_at, detected_at: iso(run.clock), recovered_at: null,
      human_intervention: false, result: null, run_id: null };
    this.incidents.push(incident);
    const sample = flag.kind === 'llm_invalid' ? (target === 'writer' ? faultSamples.writer_invalid : faultSamples.llm_invalid)
      : flag.kind === 'link_dead' ? faultSamples.link_dead : flag.kind === 'not_official' ? faultSamples.not_official
      : flag.kind === 'stale_info' ? faultSamples.stale_info : flag.kind === 'pii_leak' ? { ...faultSamples.pii_leak, author: 'writer' } : null;
    const fault: StageResult = flag.kind === 'delay'
      ? { task_id: run.job.id, author: 'backend', status: 'retry', data: {}, failure: 'SERVICE_UNAVAILABLE', incident_id: flag.incident_id }
      : sample ? { ...envelope(sample), incident_id: flag.incident_id } : { ...normal, incident_id: flag.incident_id };
    const check = (fault.data.checks as { ok: boolean; detail: string }[] | undefined)?.find(item => !item.ok);
    const channel = flag.kind === 'not_official' ? unofficialInput.inputs.channels[0]
      : flag.kind === 'stale_info' ? staleInput.inputs.channels[0] : run.channels[0];
    const sourceEvidence = fault.data.evidence as { broken?: string } | undefined;
    const broken = flag.kind === 'delay' ? `${typeof flag.params.seconds === 'number' ? `지연 설정 ${flag.params.seconds}초` : '지연 주입'} · 모의 호출 ${duration / 1000}초에서 ${fault.failure}`
      : flag.kind === 'pii_leak' ? '연락처 010-1234-5678 (실험용 가짜 번호)'
      : flag.kind === 'stale_info' ? `${channel.name} · 확인일 ${channel.verified_at} (${Math.floor((run.clock - Date.parse(channel.verified_at)) / 86_400_000)}일 전, 기준 90일)`
      : flag.kind === 'link_dead' || flag.kind === 'not_official' ? sourceEvidence?.broken ?? `${channel.name} ${channel.url} · ${check?.detail ?? fault.failure}`
      : sourceEvidence?.broken;
    if (broken) fault.data = { ...fault.data, evidence: mockEvidence({ broken,
      ...(flag.kind === 'stale_info' ? { fixed: '확인 필요 표시를 붙여 안내' } : {}) }) };
    this.emit(run, target, fault, attempt);
    if (flag.kind === 'stale_info') {
      run.job.state.confirm_needed.push('신고 창구 확인일이 오래되었습니다');
      incident.recovered_at = iso(run.clock); incident.result = 'recovered';
      return fault;
    }
    if (flag.kind === 'link_dead' || flag.kind === 'not_official') {
      const rejected = run.channels[0]?.channel_id;
      run.channels = run.channels.filter(channel => channel.channel_id !== rejected);
      const relocated: StageResult = { task_id: '', author: 'locator', status: 'success',
        data: { channels: run.channels, fallback_level: 'national' }, failure: null, incident_id: flag.incident_id };
      yield* this.call(run, 'locator', relocated, 800, false, 2);
      normal = { ...normal, data: { ...normal.data, passed_channel_ids: run.channels.map(channel => channel.channel_id) } };
    } else if (flag.kind === 'pii_leak') {
      const injected = `${run.draft.body}\n연락처 010-1234-5678`;
      run.draft = { ...run.draft, body: injected.replace('010-1234-5678', '[개인정보 삭제]'), removed_pii: ['전화번호'] };
      normal = { ...normal, data: run.draft };
    }
    yield flag.kind === 'delay' ? 15_000 : 800;
    normal = { ...normal, incident_id: flag.incident_id };
    const fixed = flag.kind === 'link_dead' || flag.kind === 'not_official'
      ? run.channels.map(item => `${item.name} ${item.url} · 검증 통과`).join(' / ')
      : flag.kind === 'delay' ? `재호출 성공까지 ${(run.clock - Date.parse(incident.detected_at!)) / 1000}초`
      : flag.kind === 'pii_leak' ? `연락처 ${run.draft.body.split('연락처 ').at(-1)} · 지운 항목: ${run.draft.removed_pii.join(', ')}`
      : `mock-fixture · ${normal.data.category ?? normal.data.title} · ${(normal.data.region as { dong?: string } | undefined)?.dong ?? '작성 완료'}`;
    if (normal.status === 'success') normal.data = { ...normal.data, evidence: mockEvidence({ fixed }) };
    this.emit(run, target, normal, attempt + 1);
    if (normal.status === 'success') { incident.recovered_at = iso(run.clock); incident.result = 'recovered'; }
    return normal;
  }
  private *pipeline(run: Run, answers?: Record<string, string>): Generator<number, void> {
    const selected = scenario(run.job.text);
    const attack = run.job.text.match(/이전\s*지시.*무시|시스템\s*프롬프트|###\s*SYSTEM|관리자야/i);
    if (attack) {
      yield 1400;
      const excerpt = attack[0].match(/이전\s*지시(?:는|를)?(?:\s*무시)?|시스템\s*프롬프트|###\s*SYSTEM|관리자야/i)![0];
      this.emit(run, 'backend', { task_id: '', author: 'backend', status: 'failed', data: { evidence: mockEvidence({
        broken: `감지 문구: ${excerpt}`, fixed: '차단 · 에이전트 호출 0' }) }, failure: 'INJECTION_BLOCKED', incident_id: null });
      this.finish(run, 'failed'); return;
    }
    if (this.flags.has('backend')) yield* this.call(run, 'backend', { task_id: '', author: 'backend', status: 'success', data: {}, failure: null, incident_id: null }, 800);
    const roadNames = [...run.job.text.matchAll(/(?:^|\s)([가-힣○]{2,20}(?:로|길))(?:\s|\d|$)/g)];
    const located = /[가-힣]{1,6}(?:동|역)/.test(run.job.text) || roadNames.some(match => match[1] !== '도로');
    const needs = selected.category !== 'out_of_scope' && !answers && !located;
    const raw = needs ? selected.needs : selected.classifier;
    const classified = classification(raw.data, selected.category);
    if (run.job.state.has_photo) classified.details.has_photo = true;
    if (answers) {
      classified.region = { ...selected.final.region, place_text: Object.values(answers).join(' ') };
      classified.details.location_detail = classified.region.place_text;
    }
    const result = yield* this.call(run, 'classifier', { ...envelope(raw), data: classified }, 1400);
    if (result.status === 'needs_user') {
      run.job.state.question_round += 1;
      this.finish(run, 'needs_user'); return;
    }
    run.job.state.classification = classified;
    if (selected.category === 'out_of_scope') run.job.state.confirm_needed.push('지원 범위 밖입니다. 공식 창구에서 지원 범위를 확인해 주세요.');
    yield* this.call(run, 'locator', envelope(selected.locator), 500);
    yield* this.call(run, 'verifier', envelope(selected.verifier), 900);
    run.job.state.channels = run.channels;
    if (selected.category !== 'out_of_scope') {
      yield* this.call(run, 'writer', { ...envelope(selected.writer), data: run.draft }, 2200);
      yield* this.call(run, 'verifier', envelope(selected.draft), 600, true);
    } else run.draft = { title: '', body: '', fill_in: [], removed_pii: [] };
    run.job.state.result = { ...structuredClone(selected.final), category: selected.category, region: classified.region,
      channels: run.channels, draft: run.draft,
      confirm_needed: [...selected.final.confirm_needed, ...run.job.state.confirm_needed],
      sources: run.channels.map(channel => ({ url: channel.source_url, checked_at: channel.verified_at })) };
    const extras = ['가로등', '포트홀', '쓰레기'].filter(word =>
      (word === '가로등' ? /가로등|보안등/ : word === '포트홀' ? /포트홀|도로.?파손|보도블록/ : /쓰레기|무단.?투기|폐기물/).test(run.job.text))
      .map(word => scenario(word)).filter(item => item.category !== selected.category);
    run.job.state.results = [run.job.state.result, ...extras.map(item => ({ ...structuredClone(item.final), category: item.category, channels: channels(item.final.channels) }))];
    this.finish(run, 'success');
  }
  async createJob(text: string, options: import('./types').JobOptions = {}): Promise<JobCreated> {
    if ([...text].length === 0 || [...text].length > 500) throw new ApiError(422, '민원 내용은 1자 이상 500자 이하로 적어 주세요');
    const selected = scenario(text);
    const time = this.now();
    const job: Job = { id: `job-mock-${++this.sequence}`, text, status: 'running', state: { question_round: 0, confirm_needed: [] }, created_at: iso(time), completed_at: null };
    const run: Run = { job, events: [], deliveries: {}, clock: time, due: time, channels: channels(selected.locator.data.channels), draft: structuredClone(selected.writer.data) };
    if (options.lang && options.lang !== 'ko') Object.assign(job.state, { lang: options.lang, original_text: text });
    if (options.has_photo) { job.state.has_photo = true; run.draft.body += ' 현장 사진을 보유하고 있습니다.'; }
    this.jobs.set(job.id, run); this.start(run);
    return this.created(run);
  }
  private created(run: Run): JobCreated { return structuredClone({ id: run.job.id, status: run.job.status, state: run.job.state }); }
  async getJob(id: string): Promise<Job> { return structuredClone(this.lookup(id).job); }
  async getTimeline(id: string): Promise<TimelineEvent[]> { return structuredClone(this.lookup(id).events); }
  async answerJob(id: string, answers: Record<string, string>): Promise<JobCreated> {
    const run = this.lookup(id);
    if (run.job.status !== 'needs_user') throw new ApiError(409, '추가 질문 대기 중인 작업이 아닙니다');
    if (!Object.keys(answers).length || Object.entries(answers).some(([key, value]) => key !== 'q1' || !value.trim())) throw new ApiError(422, '답하지 않은 질문이 있는지 확인해 주세요');
    this.start(run, answers);
    return this.created(run);
  }
  async reviewJob(id: string, draft: SendDraft): Promise<DraftReviewResult> {
    const run = this.lookup(id), original = run.job.state.results?.[draft.index ?? 0]?.draft;
    if (!original) throw new ApiError(409, '검토할 초안이 없어요');
    const response: DraftReviewResult = { verdict: 'ok', issues: [], fixed: { title: draft.title, body: draft.body }, verifier_checked: true };
    if (draft.title !== original.title || draft.body !== original.body) {
      for (const field of ['title', 'body'] as const) {
        for (const match of draft[field].matchAll(/01[016789][-. ]?\d{3,4}[-. ]?\d{4}/g)) {
          response.issues.push({ id: `i${response.issues.length + 1}`, field, start: [...draft[field].slice(0, match.index)].length,
            end: [...draft[field].slice(0, match.index + match[0].length)].length, severity: 'block', kind: 'pii',
            message: '전화번호가 들어 있어요. 신고 문구에 개인 연락처는 넣지 않아요', fix: { replacement: '' } });
        }
        response.fixed[field] = draft[field].replace(/01[016789][-. ]?\d{3,4}[-. ]?\d{4}/g, '').replace(/[ \t]{2,}/g, ' ').trim();
      }
    }
    if (response.issues.length) response.verdict = 'block';
    run.clock = this.now();
    this.emit(run, 'verifier', { task_id: id, author: 'verifier', status: response.verdict === 'block' ? 'retry' : 'success',
      failure: response.verdict === 'block' ? 'PII_IN_DRAFT' : null, incident_id: null, data: {}, review: true, index: draft.index ?? 0 });
    return response;
  }
  async sendJob(id: string, draft: SendDraft): Promise<void> {
    const run = this.lookup(id);
    const index = draft.index ?? 0;
    if (run.job.status !== 'success' || !run.job.state.results?.[index]?.channels.length || run.deliveries[index]?.flow) throw new ApiError(409, '발송 가능한 작업이 아닙니다');
    run.clock = this.now();
    const delivery = { flow: this.sendMail(run, draft), due: run.clock };
    run.deliveries[index] = delivery;
    const first = delivery.flow.next();
    if (!first.done) delivery.due = run.clock + first.value;
  }
  private *sendMail(run: Run, draft: SendDraft): Generator<number, void> {
    const flag = this.flags.get('mailer');
    if (flag?.kind === 'mail_down') this.flags.delete('mailer');
    const serial = ++this.mailSequence;
    const contact = mockContact(run.job.state.results?.[draft.index ?? 0]?.channels?.[0]?.channel_id);
    const to = contact.email;
    const messageId = `<${run.job.id}.${serial}.report@minwon.example>`;
    const data: MailerData & { evidence?: Record<string, string> } = { ...mailerSample.data, ...contact, to, message_id: messageId, status: 'sent', receipt_no: null };
    const payload = (status: StageResult['status'], failure: StageResult['failure']): StageResult => ({
      task_id: draft.index ? `${run.job.id}:${draft.index}` : run.job.id, author: 'mailer', status, data: { ...data }, failure, incident_id: flag?.kind === 'mail_down' ? flag.incident_id : null,
    });
    yield 800;
    let incident: Incident | undefined;
    if (flag?.kind === 'mail_down') {
      incident = { id: flag.incident_id, job_id: run.job.id, target: 'mailer', fault: 'mail_down', injected_at: flag.injected_at,
        detected_at: iso(run.clock), recovered_at: null, human_intervention: false, result: null, run_id: null };
      this.incidents.push(incident);
      Object.assign(data, queuedSample.data, { message_id: data.message_id });
      data.evidence = mockEvidence({ broken: queuedSample.data.evidence.broken });
      this.emit(run, 'mailer', payload('retry', 'MAIL_SEND_FAILED'));
      yield 3200;
      incident.recovered_at = iso(run.clock); incident.result = 'recovered';
    }
    const day = iso(run.clock).slice(0, 10).replace(/-(\d\d)-(\d\d)/, '-$1$2');
    data.status = 'sent'; data.attempts = 1; data.receipt_no = `${day}-${String(serial).padStart(4, '0')}`;
    if (incident) data.evidence = mockEvidence({ fixed: `편지함 대기 후 재발송 성공 ${iso(run.clock)} · 접수번호 ${data.receipt_no}` });
    this.emit(run, 'mailer', payload('success', null), incident ? 2 : 1);
    this.mailbox.push({ id: messageId, from: 'minwon-guide@minwon.example', to: [to], subject: `[가상 민원] ${draft.title}`,
      snippet: draft.body, created: iso(run.clock), kind: 'report' });
    this.mailbox.push({ id: `${data.message_id}-reply`, from: to, to: [draft.citizen?.email ?? 'citizen@minwon.example'],
      subject: `[가상 회신] 접수되었습니다 · 접수번호 ${data.receipt_no}`, snippet: '가상 담당자가 민원을 접수했어요', created: iso(run.clock + (incident ? 3000 : 2200)), kind: 'reply' });
  }
  async getMailbox(): Promise<MailItem[]> {
    this.jobs.forEach(run => this.advance(run));
    return structuredClone(this.mailbox.filter(item => Date.parse(item.created) <= this.now()).sort((a, b) => Date.parse(b.created) - Date.parse(a.created)));
  }
  async getIncidents(signal?: AbortSignal): Promise<Incident[]> {
    signal?.throwIfAborted();
    this.jobs.forEach(run => this.advance(run));
    return structuredClone([...this.incidents, ...this.demo.incidents]).sort((a, b) => (b.injected_at ?? '').localeCompare(a.injected_at ?? ''));
  }
  async getOpsOverview(hours: OpsHours = 0, signal?: AbortSignal) {
    const incidents = await this.getIncidents(signal);
    const runs = [...this.jobs.values()];
    const jobs: OpsJob[] = runs.map(({ job, events }) => ({ id: job.id, created_at: job.created_at, status: job.status,
      duration_sec: job.completed_at ? (Date.parse(job.completed_at) - Date.parse(job.created_at)) / 1000 : null,
      category: job.state.classification?.category ?? null, region: job.state.classification ? [job.state.classification.region.sido, job.state.classification.region.sigungu, job.state.classification.region.dong].filter(Boolean).join(' ') || null : null,
      stages: events.map(event => ({ agent: event.agent, status: event.payload.status })), incident_count: incidents.filter(item => item.job_id === job.id).length }));
    return mockOverview(this.now(), hours, [...jobs, ...this.demo.jobs], [...runs.flatMap(run => run.events.map(event => ({ at: event.created_at, job_id: event.job_id, agent: event.agent, status: event.payload.status, failure: event.payload.failure, attempt: event.attempt, review: event.payload.review }))), ...this.demo.events], incidents);
  }
  async getMetrics(): Promise<Metrics> {
    const all = await this.getIncidents();
    const recovered = all.filter(incident => incident.recovered_at !== null && !incident.human_intervention);
    const durations = recovered.map(incident => incident.recovered_at && incident.detected_at ? (Date.parse(incident.recovered_at) - Date.parse(incident.detected_at)) / 1000 : 0);
    return { total: all.length, auto_recovered: recovered.length, auto_recovery_rate: all.length ? recovered.length / all.length : 0,
      avg_recovery_sec: durations.length ? durations.reduce((sum, value) => sum + value, 0) / durations.length : 0 };
  }
  async setChaosFlag(target: ChaosTarget, flag: ChaosFlag): Promise<void> {
    this.jobs.forEach(run => this.advance(run));
    this.flags.set(target, structuredClone(flag));
  }
  async runRedTeam(): Promise<RedTeamResult> {
    const selected = redTeam[this.redIndex++ % redTeam.length];
    await this.setChaosFlag(selected.target, { incident_id: `inc-mock-${this.now()}-${this.redIndex}`, kind: selected.kind, injected_at: iso(this.now()), params: selected.params ?? {} });
    return { ready: true, target: selected.target, kind: selected.kind, reason: 'mock: 순서대로 고름' };
  }
}
