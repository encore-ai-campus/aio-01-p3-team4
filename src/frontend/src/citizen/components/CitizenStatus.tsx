import { useEffect, useState } from 'react';
import { useReducedMotion } from 'motion/react';
import type { useJobRun } from '../../hooks/useJobRun';
import { citizenStatus } from '../lib/citizenText';

const graphemes = (text: string) => Array.from(new Intl.Segmenter('ko', { granularity: 'grapheme' }).segment(text), item => item.segment);
export function WorkingDots() { return <span className="typing-dots" aria-hidden="true"><i>·</i><i>·</i><i>·</i></span>; }
function Typed({ text, dots }: { text: string; dots: boolean }) {
  const reduce = useReducedMotion(), [count, setCount] = useState(0);
  const chars = graphemes(text);
  useEffect(() => {
    if (reduce) return;
    let frame = 0; const start = performance.now(); setCount(0);
    const tick = (now: number) => { const n = Math.floor((now - start) / 28); setCount(n); if (n < chars.length) frame = requestAnimationFrame(tick); };
    frame = requestAnimationFrame(tick); return () => cancelAnimationFrame(frame);
  }, [text, reduce, chars.length]);
  return <span aria-label={text}><span aria-hidden="true">{reduce ? text : chars.slice(0, count).join('')}{dots && <WorkingDots/>}</span></span>;
}
export function CitizenStatus({ run }: { run: ReturnType<typeof useJobRun> }) {
  const text = citizenStatus(run), active = ['running', 'submitting'].includes(run.phase) || Object.values(run.mails).some(mail => ['sending', 'queued'].includes(mail.status));
  return text ? <p className="citizen-status" role="status" aria-live="polite"><Typed key={text} text={text} dots={active}/></p> : null;
}
