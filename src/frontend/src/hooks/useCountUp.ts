import { useEffect, useRef, useState } from 'react';

const reducedMotion = () => typeof window.matchMedia === 'function'
  && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
export function useCountUp(target: number | null, { durationMs = 900 } = {}) {
  const [display, setDisplay] = useState<number | null>(() => target === null || reducedMotion() ? target : 0);
  const current = useRef(display ?? 0);
  useEffect(() => {
    let frame = 0;
    const media = typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
    function update(value: number | null) { current.current = value ?? 0; setDisplay(value); }
    function start() {
      cancelAnimationFrame(frame);
      if (target === null || media?.matches || durationMs <= 0) { update(target); return; }
      const from = current.current, to = target, began = performance.now();
      function tick(now: number) {
        const p = Math.min(1, Math.max(0, (now - began) / durationMs));
        update(from + (to - from) * (1 - (1 - p) ** 3));
        if (p < 1) frame = requestAnimationFrame(tick);
      }
      frame = requestAnimationFrame(tick);
    }
    start();
    media?.addEventListener('change', start);
    return () => { cancelAnimationFrame(frame); media?.removeEventListener('change', start); };
  }, [target, durationMs]);
  return target === null || reducedMotion() ? target : display ?? 0;
}
