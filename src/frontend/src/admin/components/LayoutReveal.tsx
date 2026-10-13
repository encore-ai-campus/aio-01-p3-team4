import { useLayoutEffect, useRef, type ReactNode } from 'react';
import { cancelFrame, cubicBezier, frame, useReducedMotion } from 'motion/react';
const ease = cubicBezier(.23, 1, .32, 1);
const maxTravel = 11.5;
type Reveal = { el: HTMLDivElement; content: HTMLDivElement; height: number; from: number; target: number; started: number; reduce: boolean | null; mobileOnly: boolean; enter: boolean; measured: boolean };
const reveals = new Set<Reveal>();
let lastUpdate = 0;
function update() {
  const now = performance.now();
  const travel = Math.min(maxTravel, Math.max(0, now - lastUpdate) * maxTravel / (1000 / 60));
  lastUpdate = now;
  const changes = [...reveals].filter(s => s.el.getClientRects().length).map(s => {
    const target = parseFloat(getComputedStyle(s.content).height);
    if (!s.measured) { s.measured = true; s.height = s.from = s.enter && !s.reduce ? 0 : target; s.target = target; s.started = now; }
    if (Math.abs(target - s.target) > .1) { s.from = s.height; s.target = target; s.started = now; }
    const immediate = s.reduce || (s.mobileOnly && window.innerWidth > 800);
    const desired = immediate ? target : s.from + (target - s.from) * ease(Math.min(1, (now - s.started) / 300));
    return { s, immediate, delta: desired - s.height };
  });
  const distance = changes.reduce((sum, { immediate, delta }) => sum + (immediate ? 0 : Math.abs(delta)), 0);
  const fraction = distance > travel ? travel / distance : 1;
  for (const { s, immediate, delta } of changes) {
    s.height += delta * (immediate ? 1 : fraction);
    s.el.style.height = immediate ? 'auto' : `${s.height}px`;
    if (s.enter) { const progress = s.target ? s.height / s.target : 1; s.content.style.opacity = String(progress); s.content.style.transform = `scaleY(${progress})`; }
  }
  if (changes.every(({ s }) => Math.abs(s.target - s.height) < .01)) cancelFrame(update);
}
export function LayoutReveal({ children, className, enter = false, mobileOnly = false, unclipped = false }: { children: ReactNode; className?: string; enter?: boolean; mobileOnly?: boolean; unclipped?: boolean }) {
  const ref = useRef<HTMLDivElement>(null), content = useRef<HTMLDivElement>(null), state = useRef<Reveal | null>(null), reduce = useReducedMotion();
  useLayoutEffect(() => {
    if (typeof ResizeObserver === 'undefined') return;
    const height = content.current!.getBoundingClientRect().height;
    const s: Reveal = { el: ref.current!, content: content.current!, height: enter && !reduce ? 0 : height, from: enter && !reduce ? 0 : height, target: height, started: performance.now(), reduce, mobileOnly, enter, measured: content.current!.getClientRects().length > 0 };
    state.current = s; s.el.style.height = reduce || (mobileOnly && window.innerWidth > 800) ? 'auto' : `${s.height}px`; reveals.add(s);
    const observer = new ResizeObserver(() => { update(); frame.postRender(update, true); }); observer.observe(s.content); frame.postRender(update, true);
    return () => { observer.disconnect(); reveals.delete(s); if (!reveals.size) cancelFrame(update); };
  }, []);
  useLayoutEffect(() => {
    const s = state.current; if (s) { s.reduce = reduce; if (reduce) { s.height = s.from = s.target = parseFloat(getComputedStyle(s.content).height); s.el.style.height = 'auto'; s.content.style.opacity = '1'; s.content.style.transform = ''; } }
    update(); frame.postRender(update, true);
  });
  return <div ref={ref} className={className} style={{ overflow: unclipped ? 'visible' : 'hidden' }}><div ref={content} style={{ display: 'flow-root', transformOrigin: 'top' }}>{children}</div></div>;
}
