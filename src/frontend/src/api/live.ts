import { ApiError, type Api, type RedTeamResult } from './client';
import type { DraftReviewResult, ChaosFlag, ChaosTarget, Incident, Job, JobCreated, MailItem, MailpitResponse, Metrics, OpsHours, OpsOverview, SendDraft, TimelineEvent } from './types';

function detailMessage(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(detailMessage).filter(Boolean).join('; ');
  if (detail && typeof detail === 'object' && 'msg' in detail && typeof detail.msg === 'string') {
    const location = 'loc' in detail && Array.isArray(detail.loc)
      ? detail.loc.filter((part) => typeof part === 'string' || typeof part === 'number').join('.') : '';
    return location ? `${location}: ${detail.msg}` : detail.msg;
  }
  return '';
}

export class LiveApi implements Api {
  readonly mode = 'live';
  constructor(private readonly base = '/api') {}

  private async request<T>(path: string, method = 'GET', body?: unknown, fallback?: string, signal?: AbortSignal): Promise<T> {
    const response = await fetch(this.base + path, {
      method,
      ...(signal ? { signal } : {}),
      ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    });
    const payload: unknown = response.status === 204 ? undefined : await response.json().catch((error: unknown) => {
      if (response.ok) throw error;
      return undefined;
    });
    if (!response.ok) {
      const detail = payload && typeof payload === 'object' && 'detail' in payload ? payload.detail : undefined;
      throw new ApiError(response.status, detailMessage(detail) || fallback || response.statusText || `요청 실패 (${response.status})`);
    }
    // JSON 경계에서 백엔드 계약 타입으로 받아들인다.
    return payload as T;
  }

  createJob(text: string, options: import('./types').JobOptions = {}) { return this.request<JobCreated>('/jobs', 'POST', { text, ...options }); }
  getJob(id: string) { return this.request<Job>(`/jobs/${encodeURIComponent(id)}`); }
  getTimeline(id: string) { return this.request<TimelineEvent[]>(`/jobs/${encodeURIComponent(id)}/timeline`); }
  answerJob(id: string, answers: Record<string, string>) {
    return this.request<JobCreated>(`/jobs/${encodeURIComponent(id)}/answers`, 'POST', { answers });
  }
  reviewJob(id: string, draft: SendDraft) {
    return this.request<DraftReviewResult>(`/jobs/${encodeURIComponent(id)}/review`, 'POST', draft);
  }
  async sendJob(id: string, draft: SendDraft): Promise<void> {
    const response = await fetch(`${this.base}/jobs/${encodeURIComponent(id)}/send`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: draft.title, body: draft.body, ...(draft.index === undefined ? {} : { index: draft.index }), ...(draft.citizen ? { citizen: draft.citizen } : {}) }),
    });
    if (!response.ok) {
      const payload: unknown = await response.json().catch(() => undefined);
      // 백엔드에 발송 경로가 아직 없을 때(404·405·501)는 서버 원문 대신 준비 중 안내
      if ([404, 405, 501].includes(response.status)) throw new ApiError(response.status, '가상 발송은 아직 준비 중이에요');
      throw new ApiError(response.status, detailMessage(payload && typeof payload === 'object' && 'detail' in payload ? payload.detail : undefined) || '가상 발송 요청이 실패했어요');
    }
  }
  async getMailbox(): Promise<MailItem[] | null> {
    let response: Response;
    try { response = await fetch('/mailbox/api/v1/messages?limit=20'); }
    catch { return null; }
    if (response.status === 404 || response.status === 502) return null;
    if (!response.ok) throw new ApiError(response.status, '메일함을 읽지 못했어요');
    const payload: MailpitResponse = await response.json();
    return payload.messages.map((message): MailItem => ({
      id: message.ID, from: message.From.Address, to: message.To.map(to => to.Address),
      subject: message.Subject, snippet: message.Snippet, created: message.Created,
      kind: message.Subject.includes('접수번호') ? 'reply' : message.To.some(to => to.Address.endsWith('.example')) ? 'report' : 'other',
    })).sort((a, b) => Date.parse(b.created) - Date.parse(a.created));
  }
  async getMetrics(): Promise<Metrics | null> {
    try { return await this.request<Metrics>('/metrics'); }
    catch (error) { if (error instanceof ApiError && error.status === 501) return null; throw error; }
  }
  getIncidents(signal?: AbortSignal) { return this.request<Incident[]>('/incidents', 'GET', undefined, undefined, signal); }
  getOpsOverview(hours: OpsHours = 0, signal?: AbortSignal) { return this.request<OpsOverview>(`/ops/overview?hours=${hours}`, 'GET', undefined, undefined, signal); }
  setChaosFlag(target: ChaosTarget, flag: ChaosFlag) { return this.request<void>(`/chaos/flags/${target}`, 'PUT', flag); }
  async runRedTeam(): Promise<RedTeamResult> {
    try {
      const selection = await this.request<Omit<Extract<RedTeamResult, { ready: true }>, 'ready'>>('/chaos/run', 'POST', {}, '레드팀은 준비 중입니다');
      return { ...selection, ready: true };
    }
    catch (error) {
      if (error instanceof ApiError && error.status === 501) return { ready: false, message: error.message };
      throw error;
    }
  }
}
