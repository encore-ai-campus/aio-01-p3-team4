import { LayoutReveal } from './LayoutReveal';
import { useRef } from 'react';
import { Tags, Search, ShieldCheck, PenLine, ScanText, Send } from 'lucide-react';
import type { TimelineEvent } from '../../api/types';
import type { PipelineStage, TimelineRow } from '../../lib/timeline';
import { teamTalk } from '../../lib/talk';
import { WorkingDots } from './TeamTalk';
import { incidentCards } from '../../lib/incidents';
import { Reroute } from './Reroute';
export const stationIcons = [Tags, Search, ShieldCheck, PenLine, ScanText];
const labels = { pending: '대기', active: '진행 중', fail: '실패', recovered: '자동 복구', done: '성공', warn: '확인 필요' };
export function Pipeline({ stages, rows, events, running, compact }: { stages: PipelineStage[]; rows: TimelineRow[]; events: TimelineEvent[]; running: boolean; compact?: boolean }) {
  const container = useRef<HTMLDivElement>(null), incident = incidentCards(events, rows).at(-1);
  const sent = rows.filter(row => row.stage === 'send').at(-1);
  const stations = sent ? [...stages, { id: 'send', label: '가상 발송', status: sent.tone === 'ok' ? 'done' : sent.tone === 'ask' ? 'active' : sent.tone } as const] : stages;
  const talk = teamTalk(events, { running }), working = talk.findLast(item => item.kind === 'working');
  const handoff = talk.findLast(item => item.kind === 'handoff'), decision = talk.findLast(item => item.kind === 'decision');
  return <div ref={container} className={`pipeline-wrap${incident ? ' has-reroute' : ''}`}>
    {incident && <Reroute card={incident} container={container} compact={compact}/>}
    <ol className={`pipeline${compact ? ' compact' : ''}${running ? ' running' : ''}${sent ? ' has-send' : ''}`} aria-label="민원 처리 단계">{stations.map((stage, i) => {
    const attempts = Math.max(0, ...rows.filter(row => row.stage === stage.id).map(row => row.attempt));
    const Icon = stationIcons[i] ?? Send;
    return <li key={stage.id} className={stage.status} data-stage={stage.id}><span className="station-dot" data-pulse={stage.status === 'fail' ? 'on' : undefined}>{stage.status === 'recovered' && <span aria-hidden="true">✓</span>}</span><strong><Icon/>{stage.label}</strong><small>{attempts > 0 && `${attempts}회 · `}{labels[stage.status]}</small>
      <LayoutReveal mobileOnly unclipped className="station-notes"><div className="station-notes-content">{stage.status === 'fail' && decision && <p key={decision.id} className="decision-bubble">{decision.text}</p>}
      {working?.from === stage.label && <p key={working.id} className="station-working"><span>{working.text}</span><WorkingDots/></p>}
      {!compact && handoff?.to === stage.label && <span key={handoff.id} className="handoff-chip">총괄 → {stage.label} · {handoff.text}</span>}
      </div></LayoutReveal>
    </li>;
  })}</ol></div>;
}
