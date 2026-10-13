import type { DraftReviewResult, ChaosFlag, ChaosKind, ChaosTarget, Incident, Job, JobCreated, MailItem, Metrics, OpsHours, OpsOverview, SendDraft, TimelineEvent } from './types';

export type RedTeamResult = { ready: true; target: ChaosTarget; kind: ChaosKind; reason: string }
  | { ready: false; message: string };
export interface Api {
  mode: 'live' | 'mock';
  createJob(text: string, options?: import('./types').JobOptions): Promise<JobCreated>;
  getJob(id: string): Promise<Job>;
  getTimeline(id: string): Promise<TimelineEvent[]>;
  answerJob(id: string, answers: Record<string, string>): Promise<JobCreated>;
  reviewJob(id: string, draft: SendDraft): Promise<DraftReviewResult>;
  sendJob(id: string, draft: SendDraft): Promise<void>;
  getMailbox(): Promise<MailItem[] | null>;
  getMetrics(): Promise<Metrics | null>;
  getIncidents(signal?: AbortSignal): Promise<Incident[]>;
  getOpsOverview(hours?: OpsHours, signal?: AbortSignal): Promise<OpsOverview>;
  setChaosFlag(target: ChaosTarget, flag: ChaosFlag): Promise<void>;
  runRedTeam(): Promise<RedTeamResult>;
}
export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}
