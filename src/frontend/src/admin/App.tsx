import { useEffect, useState } from 'react';
import { Activity } from 'lucide-react';
import { MotionConfig } from 'motion/react';
import defaultApi, { type Api } from '../api';
import { useJobRun } from '../hooks/useJobRun';
import { useLab } from '../hooks/useLab';
import { DashboardPage } from './pages/DashboardPage';
import { LivePage } from './pages/LivePage';
import { LabPage } from './pages/LabPage';
import { IncidentsPage } from './pages/IncidentsPage';
import { JobPage } from './pages/JobPage';
import './styles/admin.css';
type Route = { tab: 'ops' | 'live' | 'lab' | 'incidents' | 'job'; id?: string };
const tabs = [{ id: 'ops', label: '관제 대시보드' }, { id: 'live', label: '실시간 관제' }, { id: 'lab', label: '장애 실험실' }, { id: 'incidents', label: '장애 기록' }] as const;
function route(): Route {
  const hash = window.location.hash, tab = tabs.find(item => hash === `#/${item.id}`);
  if (tab) return { tab: tab.id };
  if (hash === '#/run') return { tab: 'live' };
  const job = /^#\/jobs\/([^/]+)$/.exec(hash);
  if (job) { try { return { tab: 'job', id: decodeURIComponent(job[1]) }; } catch { return { tab: 'ops' }; } }
  return { tab: 'ops' };
}
export default function App({ api = defaultApi }: { api?: Api }) {
  const [current, setCurrent] = useState(route), run = useJobRun(api);
  const lab = useLab(api, async (text, options) => { window.location.hash = '#/live'; await run.submit(text, options); });
  const active = run.phase === 'running' || run.phase === 'submitting';
  useEffect(() => { const update = () => { setCurrent(route()); document.documentElement.scrollTop = 0; document.body.scrollTop = 0; }; window.addEventListener('hashchange', update); return () => window.removeEventListener('hashchange', update); }, []);
  return <MotionConfig reducedMotion="user"><div className="admin"><header className="admin-header">
    <a className="admin-brand" href="#/ops"><Activity aria-hidden="true"/>생활 민원 길잡이 관제</a>
    <nav className="admin-nav" aria-label="관제 메뉴">{tabs.map(tab => <a key={tab.id} href={`#/${tab.id}`} aria-current={current.tab === tab.id ? 'page' : undefined}>{tab.label}</a>)}</nav>
    {active && <a className="experiment-link" href="#/live">실험 진행 중</a>}<a className="citizen-link" href="/">시민 화면</a>
  </header>{current.tab === 'ops' ? <DashboardPage api={api}/> : current.tab === 'live' ? <LivePage api={api} run={run} notice={lab.notice}/> : current.tab === 'lab' ? <LabPage lab={lab} active={active} submit={async text => { window.location.hash = '#/live'; await run.submit(text); }}/> : current.tab === 'incidents' ? <IncidentsPage api={api}/> : <JobPage api={api} id={current.id!}/>}</div></MotionConfig>;
}
