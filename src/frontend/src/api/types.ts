// 백엔드(병훈님) 응답 모양. 구현이 바뀌면 여기만 고친다.
export type Category = 'streetlight' | 'road_damage' | 'illegal_dumping' | 'out_of_scope';
export type Region = { sido: string | null; sigungu: string | null; dong: string | null; place_text: string | null };
export type Details = { location_detail: string | null; occurred_at: string | null; has_photo: boolean | null };
export type Channel = {
  channel_id: string; name: string; org: string; method: 'web' | 'app' | 'phone';
  url: string; jurisdiction: string; verified_at: string; source_url: string;
};
export type FailureCode = 'LLM_OUTPUT_INVALID' | 'NEEDS_MORE_INFO' | 'OUT_OF_SCOPE' | 'NO_CHANNEL'
  | 'LINK_DEAD' | 'NOT_OFFICIAL' | 'STALE_INFO' | 'PII_IN_DRAFT' | 'DRAFT_MISMATCH'
  | 'INJECTION_BLOCKED' | 'SERVICE_UNAVAILABLE' | MailerFailure;
export type Agent = 'classifier' | 'locator' | 'verifier' | 'writer' | 'backend' | MailerAgent;
export type StageStatus = 'success' | 'retry' | 'failed' | 'needs_user';
export type StageResult<T = Record<string, unknown>> = {
  task_id: string; author: Agent; status: StageStatus; data: T;
  failure: FailureCode | null; incident_id: string | null; review?: boolean; index?: number;
};
export type Field = 'category' | 'location_detail' | 'occurred_at' | 'has_photo' | 'pii';
export type Question = { id: string; text: string; field: Field; translatedText?: string };
export type ClassifierData = {
  category: Category; confidence: number; region: Region; details: Details;
  missing: Field[]; questions: Question[];
};
export type VerifierData = {
  verdict: 'PASS' | 'RETRY' | 'FAIL'; checks: { name: string; ok: boolean; detail: string }[];
  reasons: string[]; passed_channel_ids: string[];
};
export type Draft = { title: string; body: string; fill_in: { label: string; placeholder: string }[]; removed_pii: string[] };
export type FinalResult = {
  category: Category; region: Region; channels: Channel[]; draft: Draft;
  confirm_needed: string[]; sources: { url: string; checked_at: string }[];
};
export type JobStatus = 'running' | 'needs_user' | 'success' | 'failed';
export type Lang = 'ko' | 'en' | 'ja' | 'zh' | 'vi';
export type JobOptions = { lang?: Lang; has_photo?: boolean };
export type I18n = { lang: Lang; questions: Record<string, string>; confirm_needed: string[]; results: { channels: Pick<Channel, 'name' | 'org' | 'jurisdiction'>[]; draft: Pick<Draft, 'title' | 'body'> }[] };
export type JobState = {
  lang?: Lang; original_text?: string; has_photo?: boolean; i18n?: I18n;
  question_round: number; confirm_needed: string[]; last_result?: StageResult;
  classification?: ClassifierData; channels?: Channel[]; result?: FinalResult; results?: FinalResult[];
};
export type JobCreated = { id: string; status: JobStatus; state: JobState; text?: string };
export type Job = JobCreated & { text: string; created_at: string; completed_at: string | null };
export type TimelineEvent = {
  id: number; job_id: string; agent: Agent; event_type: StageStatus;
  attempt: number; payload: StageResult; created_at: string;
};
export type Metrics = { total: number; auto_recovered: number; auto_recovery_rate: number; avg_recovery_sec: number };
export type OpsHours = 0 | 24 | 168;
export type OpsEvent = { at: string; job_id: string; agent: Agent; status: StageStatus; failure: string | null; attempt: number; review?: boolean };
export type OpsJob = { id: string; created_at: string; status: JobStatus; duration_sec: number | null;
  category: Category | null; region: string | null; stages: { agent: Agent; status: StageStatus }[]; incident_count: number };
export type OpsOverview = {
  generated_at: string; window_hours: OpsHours;
  services: { id: string; ok: boolean; latency_ms: number | null }[];
  jobs: { total: number; success: number; needs_user: number; failed: number; running: number; avg_duration_sec: number | null; p95_duration_sec: number | null };
  agents: ({ agent: Agent; calls: number } & Record<StageStatus, number>)[];
  series: { step_sec: number; points: { t: string; success: number; needs_user: number; failed: number; incidents: number }[] };
  recent_jobs: OpsJob[]; recent_events: OpsEvent[]; errors: string[];
};
export type ChaosKind = 'llm_invalid' | 'link_dead' | 'stale_info' | 'pii_leak' | 'not_official' | 'injection' | 'delay' | MailerChaos;
export type IncidentFault = ChaosKind | FailureCode;
export type ChaosTarget = Agent;
export type ChaosFlag = { incident_id: string; kind: ChaosKind; injected_at: string; params: Record<string, unknown> };
export type Incident = {
  id: string; job_id: string | null; target: ChaosTarget; fault: IncidentFault;
  injected_at: string | null; detected_at: string | null; recovered_at: string | null;
  human_intervention: boolean; result: string | null; run_id: string | null;
};

// mailer 샘플 기준.
type MailerAgent = 'mailer';
type MailerChaos = 'mail_down';
type MailerFailure = 'MAIL_SEND_FAILED' | 'MAIL_BLOCKED';
export type SendDraft = Pick<Draft, 'title' | 'body'> & { index?: number; citizen?: { name: string; email: string } };
export type MailerData = {
  message_id: string | null; to: string; dept: string; officer: string;
  status: 'sent' | 'queued' | 'blocked'; receipt_no: string | null; attempts: number;
};
export type MailItem = {
  id: string; from: string; to: string[]; subject: string; snippet: string; created: string;
  kind: 'report' | 'reply' | 'other';
};
export type MailpitResponse = { messages: {
  ID: string; From: { Name: string; Address: string }; To: { Name: string; Address: string }[];
  Subject: string; Snippet: string; Created: string;
}[] };

export type ReviewIssue = {
  id: string; field: 'title' | 'body'; start: number | null; end: number | null; severity: 'block' | 'warn';
  kind: 'pii' | 'link' | 'place_changed' | 'number_changed' | 'category_missing' | 'region_missing';
  message: string; fix: { replacement: string } | null;
};
export type DraftReviewResult = { verdict: 'ok' | 'warn' | 'block'; issues: ReviewIssue[]; fixed: SendDraft; verifier_checked: boolean };
