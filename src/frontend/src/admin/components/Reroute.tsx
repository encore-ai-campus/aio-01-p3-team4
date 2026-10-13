import { useId, useLayoutEffect, useState, type RefObject } from 'react';
import { useReducedMotion } from 'motion/react';
import type { IncidentCard } from '../../lib/incidents';
import { stageLabel } from '../../lib/incidents';
import { failureActions } from '../../lib/talk';

export function Reroute({ card, container, compact }: { card: IncidentCard; container: RefObject<HTMLDivElement | null>; compact?: boolean }) {
  const reduce = useReducedMotion(), marker = useId();
  const [path, setPath] = useState('');
  const { row, target, recovery } = card;
  const external = ['user', 'outbox', 'blocked', 'warn', 'input'].includes(target);
  useLayoutEffect(() => {
    const root = container.current;
    if (!root) return;
    const measure = () => {
      const bounds = root.getBoundingClientRect();
      const center = (element: Element | null) => {
        if (!element) return null;
        const box = element.getBoundingClientRect();
        return { x: box.left + box.width / 2 - bounds.left, y: box.top + box.height / 2 - bounds.top };
      };
      const from = center(root.querySelector(`[data-stage="${row.stage}"] .station-dot`));
      const to = center(root.querySelector(external ? '.reroute-destination' : `[data-stage="${target}"] .station-dot`));
      if (!from || !to || bounds.width === 0) { setPath(''); return; }
      const lift = compact ? 36 : 48;
      setPath(external ? `M ${from.x} ${from.y + 12} Q ${from.x + 30} ${to.y} ${to.x} ${to.y - 14}`
        : row.stage === target ? `M ${from.x - 8} ${from.y - 12} C ${from.x - 42} ${from.y - 60}, ${from.x + 42} ${from.y - 60}, ${from.x + 8} ${from.y - 12}`
        : Math.abs(from.x - to.x) < 1 ? `M ${from.x - 12} ${from.y} C ${from.x - 36} ${from.y}, ${to.x - 36} ${to.y}, ${to.x - 12} ${to.y}`
        : `M ${from.x} ${from.y - 12} C ${from.x} ${from.y - lift}, ${to.x} ${to.y - lift}, ${to.x} ${to.y - 12}`);
    };
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(root);
    root.querySelectorAll('li').forEach(element => observer?.observe(element));
    window.addEventListener('resize', measure);
    return () => { observer?.disconnect(); window.removeEventListener('resize', measure); };
  }, [container, row.stage, target, compact, card.attempts, recovery]);
  return <div className={`reroute-shell ${recovery ? 'recovered' : ''}`} data-reduced-motion={reduce || undefined}>
    <p className="reroute-chip"><b>총괄</b> {row.failureName ?? '실패'} → {row.failure ? failureActions[row.failure] : '상태 확인'}
      <span>{row.stage === target && '⟳ '}{stageLabel(target)}{!external && ` ${card.targetAttempt}회차`}{recovery && ' · 복구 완료'}</span></p>
    <svg className={`reroute reroute-svg${recovery ? ' recovered' : ''}`} data-from={row.stage} data-to={target} data-state={recovery ? 'recovered' : 'active'} aria-hidden="true"><defs><marker id={marker} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor"/></marker></defs>
      <path key={`${card.key}-${card.attempts}-${Boolean(recovery)}`} className="reroute-path" d={path} fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray="5 5" markerEnd={`url(#${marker})`}/>
    </svg>
    {external && <span className="reroute-destination">{stageLabel(target)}</span>}
  </div>;
}
