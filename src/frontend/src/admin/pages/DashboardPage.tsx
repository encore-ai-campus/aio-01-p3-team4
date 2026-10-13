import { useEffect, useState } from 'react';
import type { Api } from '../../api/client';
import type { Incident, OpsHours, OpsOverview, StageStatus } from '../../api/types';
import { activityLog, filterIncidents, incidentMetrics, opsColors, opsStatus, opsTime, serviceLabels, summarizeIncidents } from '../../lib/ops';
import { agentLabel, categoryLabel } from '../../lib/labels';
import { OpsGlossary } from '../components/OpsGlossary';
import { StageDots } from '../components/StageDots';

const statuses: StageStatus[] = ['success', 'retry', 'failed', 'needs_user'];
const windows: { value: OpsHours; label: string }[] = [{ value: 24, label: '24시간' }, { value: 168, label: '7일' }, { value: 0, label: '전체' }];
const seconds = (value: number | null) => value === null ? '—' : `${value.toFixed(1)}초`;
function Legend({ retry = false }: { retry?: boolean }) {
  return <div className="ops-legend">{statuses.filter(status => retry || status !== 'retry').map(status => <span key={status}><i style={{ background: opsColors[status] }}/>{opsStatus[status]}</span>)}</div>;
}
function AgentChart({ agents }: { agents: OpsOverview['agents'] }) {
  const max = Math.max(1, ...agents.map(agent => agent.calls));
  return <><svg className="ops-chart" viewBox={`0 0 380 ${Math.max(120, agents.length * 29 + 6)}`} role="img" aria-label="에이전트별 성공 재시도 실패 추가 질문 누적 호출 수">
    {agents.map((agent, i) => <g key={agent.agent}><text x="0" y={i * 29 + 20}>{agentLabel[agent.agent]}</text>{statuses.map((status, n) => {
      const before = statuses.slice(0, n).reduce((sum, key) => sum + agent[key], 0);
      return <rect key={status} x={78 + before / max * 235} y={i * 29 + 7} width={agent[status] / max * 235} height="17" rx="2" fill={opsColors[status]}><title>{agentLabel[agent.agent]} · {opsStatus[status]} {agent[status]}회</title></rect>;
    })}<text x="372" y={i * 29 + 20} textAnchor="end">{agent.calls}회</text></g>)}
    {!agents.length && <text x="190" y="65" textAnchor="middle">아직 호출 기록이 없어요</text>}
  </svg><Legend retry/></>;
}
function TimeChart({ series }: { series: OpsOverview['series'] }) {
  const points = series.points, max = Math.max(1, ...points.map(p => p.success + p.failed + p.needs_user)), width = 326 / Math.max(1, points.length);
  const keys = ['success', 'needs_user', 'failed'] as const;
  return <><svg className="ops-chart" viewBox="0 0 380 186" role="img" aria-label="시간대별 성공 추가 질문 실패 누적 처리 수와 장애 건수">
    {[0, .5, 1].map(f => <g key={f}><line x1="32" x2="358" y1={137 - f * 117} y2={137 - f * 117} className="ops-gridline"/><text x="26" y={141 - f * 117} textAnchor="end">{Math.round(max * f)}</text></g>)}
    {points.map((point, i) => <g key={point.t}>{keys.map((key, n) => <rect key={key} x={32 + i * width} y={137 - keys.slice(0, n + 1).reduce((sum, k) => sum + point[k], 0) / max * 117} width={width * .78} height={point[key] / max * 117} fill={opsColors[key]}><title>{opsTime(point.t, true)} · {opsStatus[key]} {point[key]}건 · 장애 {point.incidents}건</title></rect>)}
      {point.incidents > 0 && <g><circle cx={32 + (i + .39) * width} cy="148" r="2.5" fill="var(--warn)"/><title>{opsTime(point.t, true)} 장애 {point.incidents}건</title></g>}
    </g>)}
    {!!points.length && <><text x="32" y="178">{opsTime(points[0].t, true)}</text><text x="358" y="178" textAnchor="end">{opsTime(points.at(-1)!.t, true)}</text></>}
    {!points.some(point => point.success + point.failed + point.needs_user) && <text x="195" y="85" textAnchor="middle">아직 처리 기록이 없어요</text>}
  </svg><Legend/><p className="ops-note">{series.step_sec >= 3600 ? `${series.step_sec / 3600}시간` : `${series.step_sec / 60}분`} 단위 · <span className="ops-incident-key">●</span> 장애 {points.reduce((sum, p) => sum + p.incidents, 0)}건</p></>;
}
function FaultChart({ incidents }: { incidents: Incident[] }) {
  const groups = summarizeIncidents(incidents), max = Math.max(1, ...groups.map(group => group.total));
  return <div className="ops-fault-scroll" tabIndex={0} aria-label="장애 유형별 집계"><svg className="ops-chart" viewBox={`0 0 380 ${Math.max(160, groups.length * 57 + 4)}`} role="img" aria-label="장애 유형별 복구 차단 확인 필요 표시와 평균 복구 시간">
    {groups.map((group, i) => <g key={group.kind}><title>{group.label} · {group.total}건 · 복구 {group.autoRecovered}건 · 차단 {group.blocked}건 · 확인 필요 표시 {group.degraded}건 · {seconds(group.avgRecoverySec)}</title><text x="0" y={i * 57 + 13}>{group.label.length > 16 ? `${group.label.slice(0, 15)}…` : group.label}</text><text x="375" y={i * 57 + 13} textAnchor="end">{group.total}건</text><text x="0" y={i * 57 + 32} fontSize="11">복구 {group.autoRecovered} · 차단 {group.blocked} · 확인 필요 표시 {group.degraded} · {seconds(group.avgRecoverySec)}</text><rect x="0" y={i * 57 + 43} width={group.total / max * 375} height="7" rx="3" fill="var(--fail)"/><rect x="0" y={i * 57 + 43} width={(group.autoRecovered + group.blocked) / max * 375} height="7" rx="3" fill="var(--ok)"/></g>)}
    {!groups.length && <text x="190" y="80" textAnchor="middle">이 기간에는 장애가 없어요</text>}
  </svg></div>;
}
export function DashboardPage({ api }: { api: Api }) {
  const [hours, setHours] = useState<OpsHours>(0), [filter, setFilter] = useState<'all' | 'normal' | 'fault'>('all');
  const [state, setState] = useState<{ overview: OpsOverview | null; incidents: Incident[] | null; loading: boolean; errors: string[] }>({ overview: null, incidents: null, loading: true, errors: [] });
  useEffect(() => {
    let active = true, timer: ReturnType<typeof setTimeout> | undefined, timeout: ReturnType<typeof setTimeout> | undefined, controller: AbortController;
    setState({ overview: null, incidents: null, loading: true, errors: [] });
    const refresh = async () => {
      controller = new AbortController();
      timeout = setTimeout(() => controller.abort(), 15_000);
      const results = await Promise.allSettled([api.getOpsOverview(hours, controller.signal), api.getIncidents(controller.signal)]);
      clearTimeout(timeout);
      if (!active) return;
      const [overview, incidents] = results;
      setState(previous => ({ overview: overview.status === 'fulfilled' ? overview.value : previous.overview,
        incidents: incidents.status === 'fulfilled' ? incidents.value : previous.incidents, loading: false,
        errors: [...(overview.status === 'fulfilled' ? overview.value.errors : ['처리 현황을 갱신하지 못했어요. 마지막으로 받은 기록을 표시해요.']), ...(incidents.status === 'rejected' ? ['장애 기록을 갱신하지 못했어요.'] : [])] }));
      timer = setTimeout(() => { void refresh(); }, 30_000);
    };
    void refresh();
    return () => { active = false; clearTimeout(timer); clearTimeout(timeout); controller?.abort(); };
  }, [api, hours]);
  const data = state.overview, incidents = filterIncidents(state.incidents ?? [], hours, data ? Date.parse(data.generated_at) : Date.now());
  const metrics = incidentMetrics(incidents), jobs = data?.jobs;
  const services = Object.keys(serviceLabels).map(id => id === 'frontend' ? { id, ok: true, latency_ms: null } : data?.services.find(service => service.id === id) ?? { id, ok: null, latency_ms: null });
  const log = activityLog(data?.recent_events ?? [], incidents).filter(row => filter === 'all' || row.kind === filter);
  const tiles = [ ['처리 건수', jobs ? jobs.total.toLocaleString() : '—', '건'], ['성공률', jobs?.total ? (jobs.success / jobs.total * 100).toFixed(1) : '—', '%'], ['평균 처리 시간', jobs?.avg_duration_sec == null ? '—' : jobs.avg_duration_sec.toFixed(1), '초'],
    ['자동 처리율', state.incidents !== null && metrics.rate !== null ? metrics.rate.toFixed(1) : '—', '%'], ['평균 복구 시간', state.incidents !== null && metrics.avg !== null ? metrics.avg.toFixed(1) : '—', '초'], ['감지된 주입 장애', state.incidents !== null ? String(metrics.total) : '—', '건'] ];
  return <main className="page ops"><header className="ops-heading"><div><h1>관제 대시보드</h1><p>정상 처리부터 장애 복구까지 한눈에 봐요.{api.mode === 'mock' && <span className="mock-label">mock 데이터</span>}</p><a href="#/live">실시간 관제 →</a></div><span className="ops-updated">{data ? `${opsTime(data.generated_at)} 갱신` : state.loading ? '불러오는 중' : '갱신 대기'} · 30초마다 자동 갱신</span><div className="ops-toggle" role="group" aria-label="조회 기간">{windows.map(window => <button key={window.value} aria-pressed={hours === window.value} onClick={() => setHours(window.value)}>{window.label}</button>)}</div></header>
    {!!state.errors.length && <div className="ops-banner" role="alert">일부 정보를 확인하지 못했어요 · {state.errors.join(' · ')}</div>}
    <section className="ops-services" aria-label="서비스 상태"><strong>{services.filter(service => service.ok).length}/10 가동 중</strong>{services.map(service => <span key={service.id} className="ops-service" title={service.latency_ms === null ? service.ok === null ? '확인 대기' : service.ok ? '화면 표시 중' : '응답 없음' : `${service.latency_ms.toFixed(1)}ms`}><i className={service.ok === null ? 'unknown' : service.ok ? 'up' : 'down'}/>{serviceLabels[service.id]}<span className="sr-only">{service.ok === null ? '확인 대기' : service.ok ? '가동 중' : '응답 없음'}</span></span>)}</section>
    <dl className="ops-stats">{tiles.map(([label, value, unit]) => <div key={label}><dt>{label}</dt><dd>{value}<small>{value !== '—' && unit}</small></dd></div>)}</dl>
    <div className="ops-charts"><section className="ops-card"><h2>에이전트별 처리 <small>호출 수</small></h2><AgentChart agents={data?.agents ?? []}/></section><section className="ops-card"><h2>시간대별 처리 <small>한국 시각</small></h2><TimeChart series={data?.series ?? { step_sec: 3600, points: [] }}/></section><section className="ops-card"><h2>장애 유형별 <small>건수 · 복구 · 차단 · 확인 필요 표시</small></h2>{state.incidents === null ? <p className="ops-empty">장애 기록을 기다리고 있어요</p> : <FaultChart incidents={incidents}/>}</section></div>
    <div className="ops-streams"><section className="ops-card ops-stream"><h2>최근 처리 <small>최신 {data?.recent_jobs.length ?? 0}건</small></h2><div className="ops-scroll" tabIndex={0} role="region" aria-label="최근 처리 목록"><table className="ops-jobs"><thead><tr><th>시각</th><th>유형 · 지역</th><th>결과</th><th>소요</th><th>단계</th></tr></thead><tbody>{data?.recent_jobs.map(job => <tr key={job.id}><td><time dateTime={job.created_at}>{opsTime(job.created_at, true)}</time></td><td><a href={`#/jobs/${encodeURIComponent(job.id)}`}><strong>{job.category ? categoryLabel[job.category] ?? '유형 확인 중' : '유형 확인 중'}</strong><small>{job.region ?? '지역 확인 중'}</small></a></td><td><span className={`ops-badge ${job.status}`}>{opsStatus[job.status]}</span>{job.incident_count > 0 && <small className={job.status === 'success' ? 'ops-recovered' : ''}>{job.status === 'success' ? '복구' : '장애'} {job.incident_count}</small>}</td><td>{seconds(job.duration_sec)}</td><td><StageDots job={job}/></td></tr>)}</tbody></table>{!data?.recent_jobs.length && <p className="ops-empty">{state.loading ? '처리 기록을 불러오는 중' : '이 기간에는 처리 기록이 없어요'}</p>}</div></section>
      <section className="ops-card ops-stream"><h2>활동 로그 <small>최신순</small><span className="ops-toggle" role="group" aria-label="활동 로그 필터">{(['all', 'normal', 'fault'] as const).map((value, i) => <button key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>{['전체', '정상', '장애'][i]}</button>)}</span></h2><div className="ops-scroll ops-log" tabIndex={0} role="region" aria-label="활동 로그 목록">{log.map(row => <div className="ops-log-row" key={row.id}><i style={{ background: opsColors[row.tone] }}/><time dateTime={row.at}>{opsTime(row.at, true)}</time><strong>{row.agent}</strong><span>{row.text}</span></div>)}{!log.length && <p className="ops-empty">{state.loading ? '활동 기록을 불러오는 중' : '이 조건에는 활동 기록이 없어요'}</p>}</div></section></div>
    <details className="ops-definitions"><summary>숫자는 이렇게 집계돼요</summary><OpsGlossary/></details>
  </main>;
}
