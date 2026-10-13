import { citizenStages, isOutOfScope } from '../lib/scope';
import { useRef } from 'react';
import { Tags, Search, ShieldCheck, PenLine, ScanText, Send, TriangleAlert } from 'lucide-react';
import type { TimelineEvent } from '../../api/types';
import type { PipelineStage, TimelineRow } from '../../lib/timeline';
import { incidentCards } from '../../lib/incidents';
import { recoveredText } from '../lib/citizenText';
import { Reroute } from './Reroute';

export const stationIcons = [Tags, Search, ShieldCheck, PenLine, ScanText];
const labels = { pending: '대기', active: '확인 중', fail: '다른 길 찾는 중', done: '완료', warn: '확인 필요', skipped: '안 함(범위 밖)' };
export function Pipeline({ stages, rows, events, running, compact, outside = isOutOfScope(events), failed = false }: { stages: PipelineStage[]; rows: TimelineRow[]; events: TimelineEvent[]; running: boolean; compact?: boolean; outside?: boolean; failed?: boolean }) {
  const container = useRef<HTMLDivElement>(null), route = incidentCards(events, rows).filter(card => card.row.failure !== 'OUT_OF_SCOPE').at(-1);
  const sent = rows.filter(row => row.stage === 'send').at(-1);
  const visible = citizenStages(stages, outside);
  const stations = sent ? [...visible, { id: 'send', label: '가상 발송', status: sent.tone === 'ok' ? 'done' : sent.tone === 'ask' ? 'active' : sent.tone } as const] : visible;
  return <div ref={container} className={`pipeline-wrap${route ? ' has-reroute' : ''}`}>
    {route && <Reroute card={route} container={container} compact={compact} failed={failed}/>}
    <ol className={`pipeline${compact ? ' compact' : ''}${running ? ' running' : ''}${sent ? ' has-send' : ''}`} aria-label="민원 처리 단계">{stations.map((stage, i) => {
      const Icon = stage.status === 'warn' ? TriangleAlert : stationIcons[i] ?? Send;
      return <li key={stage.id} className={stage.status} data-stage={stage.id}><span className="station-dot" data-pulse={stage.status === 'fail' && !failed ? 'on' : undefined}>{stage.status === 'recovered' && <span aria-hidden="true">✓</span>}</span><strong><Icon/>{stage.label}</strong><small>{failed && stage.status === 'fail' ? '안내 중단' : stage.id === 'classify' && stage.status === 'warn' ? '지원 범위 밖' : stage.status === 'recovered' ? recoveredText(stage.id) : labels[stage.status]}</small></li>;
    })}</ol>
  </div>;
}
