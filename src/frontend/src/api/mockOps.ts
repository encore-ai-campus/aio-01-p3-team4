import type { Agent, Incident, OpsEvent, OpsHours, OpsJob, OpsOverview, StageStatus } from './types';
import { filterIncidents } from '../lib/ops';

const agents: Agent[] = ['classifier', 'locator', 'verifier', 'writer', 'verifier'];
const iso = (at: number) => new Date(at).toISOString();
export function demoOps(now: number) {
  const jobs: OpsJob[] = [], events: OpsEvent[] = [], incidents: Incident[] = [];
  for (let i = 0; i < 184; i++) {
    const at = now - (i * 21 + 2) * 60_000, id = `demo-${i}`, status = i % 11 === 0 ? 'failed' : i === 1 || i === 2 ? 'needs_user' : 'success';
    const duration = 8 + i % 17, incident = i % 3 === 0, stages = agents.slice(0, status === 'needs_user' ? 1 : 5).map((agent, n) => ({ agent, status: (n === (status === 'needs_user' ? 0 : 4) ? status : 'success') as StageStatus }));
    jobs.push({ id, created_at: iso(at), status, duration_sec: duration, category: (['streetlight', 'road_damage', 'illegal_dumping'] as const)[i % 3], region: ['서울 동작구 상도동', '서울 동작구 사당동', '서울 동작구 흑석동'][i % 3], stages, incident_count: Number(incident) });
    stages.forEach((stage, n) => events.push({ at: iso(at + (n + 1) * 1000), job_id: id, ...stage, failure: stage.status === 'failed' ? 'SERVICE_UNAVAILABLE' : null, attempt: 1 }));
    if (incident) {
      events.push({ at: iso(at + 2500), job_id: id, agent: 'verifier', status: 'retry', failure: 'LINK_DEAD', attempt: 1 });
      incidents.push({ id: `incident-${id}`, job_id: id, target: 'verifier', fault: (['link_dead', 'pii_leak', 'delay'] as const)[Math.floor(i / 3) % 3], injected_at: iso(at), detected_at: iso(at + 2500), recovered_at: status === 'success' ? iso(at + 5500 + i % 8 * 1000) : null, human_intervention: false, result: status === 'success' ? 'recovered' : null, run_id: null });
    }
  }
  return { jobs, events, incidents };
}
export function mockOverview(now: number, hours: OpsHours, allJobs: OpsJob[], allEvents: OpsEvent[], allIncidents: Incident[]): OpsOverview {
  const since = hours ? now - hours * 3600_000 : -Infinity;
  const jobs = allJobs.filter(job => Date.parse(job.created_at) >= since && Date.parse(job.created_at) <= now).sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  const events = allEvents.filter(event => Date.parse(event.at) >= since && Date.parse(event.at) <= now).sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  const incidents = filterIncidents(allIncidents, hours, now), durations = jobs.flatMap(job => job.duration_sec === null ? [] : [job.duration_sec]).sort((a, b) => a - b);
  const start = hours ? since : Math.min(now, ...jobs.map(job => Date.parse(job.created_at)), ...events.map(event => Date.parse(event.at)));
  const step = [300, 900, 3600, 21600, 86400].find(sec => Math.ceil((now - start) / (sec * 1000)) + 1 <= 48) ?? 86400;
  const first = Math.max(Math.floor(start / (step * 1000)) * step * 1000, Math.floor(now / (step * 1000)) * step * 1000 - 47 * step * 1000);
  const points = Array.from({ length: Math.min(48, Math.floor((now - first) / (step * 1000)) + 1) }, (_, i) => ({ t: iso(first + i * step * 1000), success: 0, needs_user: 0, failed: 0, incidents: 0 }));
  jobs.forEach(job => { const point = points[Math.floor((Date.parse(job.created_at) - first) / (step * 1000))]; if (point && job.status !== 'running') point[job.status]++; });
  incidents.forEach(item => { const point = points[Math.floor((Date.parse(item.detected_at ?? item.injected_at ?? '') - first) / (step * 1000))]; if (point) point.incidents++; });
  return { generated_at: iso(now), window_hours: hours,
    services: ['backend', 'classifier', 'locator', 'verifier', 'writer', 'mailer', 'chaos', 'data_pg', 'data_redis'].map((id, i) => ({ id, ok: true, latency_ms: 2 + i * 3 })),
    jobs: { total: jobs.length, success: jobs.filter(j => j.status === 'success').length, failed: jobs.filter(j => j.status === 'failed').length,
      needs_user: jobs.filter(j => j.status === 'needs_user').length, running: jobs.filter(j => j.status === 'running').length,
      avg_duration_sec: durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : null, p95_duration_sec: durations[Math.ceil(durations.length * .95) - 1] ?? null },
    agents: (['classifier', 'locator', 'verifier', 'writer', 'mailer', 'backend'] as Agent[]).map(agent => { const calls = events.filter(e => e.agent === agent); return { agent, calls: calls.length,
      success: calls.filter(e => e.status === 'success').length, retry: calls.filter(e => e.status === 'retry').length, failed: calls.filter(e => e.status === 'failed').length, needs_user: calls.filter(e => e.status === 'needs_user').length }; }),
    series: { step_sec: step, points }, recent_jobs: jobs.slice(0, 30), recent_events: events.slice(0, 80), errors: [] };
}
