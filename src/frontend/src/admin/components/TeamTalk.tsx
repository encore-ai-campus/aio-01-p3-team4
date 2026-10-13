import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useReducedMotion } from 'motion/react';
import type { TimelineEvent } from '../../api/types';
import { teamTalk } from '../../lib/talk';
import { LayoutReveal } from './LayoutReveal';
export const graphemes = (text: string) => Array.from(new Intl.Segmenter('ko', { granularity: 'grapheme' }).segment(text), item => item.segment);
export function WorkingDots() { return <span className="typing-dots" aria-hidden="true"><i>·</i><i>·</i><i>·</i></span>; }
export function Typed({ text }: { text: string }) {
  const reduce = useReducedMotion(), [count, setCount] = useState(0);
  const chars = graphemes(text);
  useEffect(() => {
    if (reduce) return;
    let frame = 0; const start = performance.now(); setCount(0);
    const tick = (now: number) => { const n = Math.floor((now - start) / 28); setCount(n); if (n < chars.length) frame = requestAnimationFrame(tick); };
    frame = requestAnimationFrame(tick); return () => cancelAnimationFrame(frame);
  }, [text, reduce, chars.length]);
  return <span aria-label={text}><span aria-hidden="true">{reduce ? text : chars.slice(0, count).join('')}<WorkingDots/></span></span>;
}
export function TeamTalk({ events, running, summary }: { events: TimelineEvent[]; running: boolean; summary: string | null }) {
  const items = teamTalk(events, { running }), reduce = useReducedMotion();
  const box = useRef<HTMLDivElement>(null), follow = useRef(true), previousTop = useRef(0);
  const showStarts = (el: HTMLDivElement) => {
    const top = el.getBoundingClientRect().top;
    for (const child of Array.from(el.children) as HTMLElement[]) {
      const rect = child.getBoundingClientRect();
      const tall = rect.height > el.clientHeight;
      child.classList.toggle('tall', tall);
      child.style.visibility = !tall && rect.top < top - 1 && rect.bottom > top ? 'hidden' : '';
    }
  };
  useLayoutEffect(() => {
    const el = box.current; if (!el) return;
    let frame = 0;
    const tick = () => {
      if (!follow.current) return;
      const distance = el.scrollHeight - el.clientHeight - el.scrollTop;
      el.scrollTop += reduce ? distance : Math.min(20, Math.max(1, distance * .15));
      showStarts(el);
      if (distance > 1) frame = requestAnimationFrame(tick);
    };
    const fit = () => {
      if (!el.getClientRects().length) return;
      if (follow.current) {
        const limit = parseFloat(getComputedStyle(el).maxHeight);
        let height = 0;
        for (const child of Array.from(el.children).reverse()) {
          const next = child.getBoundingClientRect().height;
          if (height && height + next > limit) break;
          height += next;
        }
        el.style.height = `${Math.min(limit, height)}px`;
        cancelAnimationFrame(frame); frame = requestAnimationFrame(tick);
      }
      showStarts(el);
    };
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(fit);
    observer?.observe(el); for (const child of el.children) observer?.observe(child);
    fit(); return () => { observer?.disconnect(); cancelAnimationFrame(frame); };
  }, [items.length, events.length, reduce]);
  return <LayoutReveal><aside className="team-talk ink-panel"><h2>에이전트 팀 대화</h2>{summary && <p className="narration">{summary}</p>}
    <div className="talk-scroll" ref={box} role="log" aria-live="polite" tabIndex={0} onScroll={() => { const el = box.current!; follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40 || (follow.current && el.scrollTop >= previousTop.current); previousTop.current = el.scrollTop; showStarts(el); }}>
      {items.map(item => <div key={item.id} className={`talk-item ${item.kind} ${item.tone}`}>
        <small>{item.from}{item.from !== item.to && ` → ${item.to}`}</small><p>{item.kind === 'working' ? <Typed text={item.text}/> : item.text}</p>
      </div>)}
    </div>
  </aside></LayoutReveal>;
}
