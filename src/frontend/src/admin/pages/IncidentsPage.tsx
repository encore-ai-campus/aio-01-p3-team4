import { useEffect, useState } from 'react';
import type { Api } from '../../api/client';
import type { Incident, OpsHours } from '../../api/types';
import { filterIncidents, incidentMetrics, metricsCohort, opsTime } from '../../lib/ops';
import { IncidentTable } from '../components/IncidentTable';

const windows: { value: OpsHours; label: string }[] = [{ value: 24, label: '24시간' }, { value: 168, label: '7일' }, { value: 0, label: '전체' }];
export function IncidentsPage({ api }: { api: Api }) {
  const [hours, setHours] = useState<OpsHours>(0);
  const [state, setState] = useState<{ items: Incident[] | null; loading: boolean; error: string; at: number }>({ items: null, loading: true, error: '', at: Date.now() });
  useEffect(() => {
    let active = true, timer: ReturnType<typeof setTimeout> | undefined, timeout: ReturnType<typeof setTimeout> | undefined, controller: AbortController;
    setState({ items: null, loading: true, error: '', at: Date.now() });
    const refresh = async () => {
      controller = new AbortController(); timeout = setTimeout(() => controller.abort(), 15_000);
      try {
        const items = await api.getIncidents(controller.signal);
        if (active) setState({ items, loading: false, error: '', at: Date.now() });
      } catch {
        if (active) setState(previous => ({ ...previous, loading: false, error: '장애 기록을 갱신하지 못했어요. 마지막으로 받은 기록을 표시해요.' }));
      } finally {
        clearTimeout(timeout);
        if (active) timer = setTimeout(() => { void refresh(); }, 30_000);
      }
    };
    void refresh();
    return () => { active = false; clearTimeout(timer); clearTimeout(timeout); controller?.abort(); };
  }, [api]);
  const incidents = filterIncidents(state.items ?? [], hours, state.at), metrics = incidentMetrics(incidents), cohort = metricsCohort(incidents), loaded = state.items !== null;
  const tiles = [['감지된 주입 장애', loaded ? String(metrics.total) : '—', '건'], ['자동 처리율', metrics.rate === null ? '—' : metrics.rate.toFixed(1), '%'], ['평균 복구 시간', metrics.avg === null ? '—' : metrics.avg.toFixed(1), '초'],
    ['사람 개입', loaded ? String(cohort.filter(item => item.human_intervention).length) : '—', '건'], ['미복구', loaded ? String(cohort.filter(item => item.recovered_at === null).length) : '—', '건']];
  return <main className="page incidents"><header className="ops-heading"><div><h1>장애 기록</h1><p>장애 감지부터 복구와 사람 조치까지 확인해요.{api.mode === 'mock' && <span className="mock-label">mock 데이터</span>}</p></div><span className="ops-updated">{loaded ? `${opsTime(new Date(state.at).toISOString())} 갱신` : state.loading ? '불러오는 중' : '갱신 대기'} · 30초마다 자동 갱신</span><div className="ops-toggle" role="group" aria-label="조회 기간">{windows.map(window => <button key={window.value} aria-pressed={hours === window.value} onClick={() => setHours(window.value)}>{window.label}</button>)}</div></header>
    {state.error && <p className="ops-banner" role="alert">{state.error}</p>}<dl className="ops-stats incident-stats">{tiles.map(([label, value, unit]) => <div key={label}><dt>{label}</dt><dd>{value}<small>{value !== '—' && unit}</small></dd></div>)}</dl>
    {state.loading ? <p className="ops-empty" role="status">장애 기록을 불러오는 중</p> : loaded ? <IncidentTable incidents={incidents}/> : <p className="ops-empty">장애 기록을 확인할 수 없어요</p>}
  </main>;
}
