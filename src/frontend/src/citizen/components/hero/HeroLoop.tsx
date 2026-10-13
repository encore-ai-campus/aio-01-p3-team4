import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { Check, RotateCcw, Link2Off } from 'lucide-react';
import { stages } from '../../../lib/failures';
import { HERO_COPY, LOOP, pace } from './scenario';
import { seek } from './seek';
import { stationIcons } from '../Pipeline';
import { LayoutReveal } from '../LayoutReveal';

const place = HERO_COPY.classification.split(' · ')[1];
const keepPlace = (text: string) => text.split(place).map((part, i) =>
  <span key={i}>{i > 0 && <span className="hero-place">{place}</span>}{part}</span>);

const probeTime = () => {
  const query = new URLSearchParams(window.location.search).get('hero');
  return query !== null && Number.isFinite(Number(query)) ? Number(query) : null;
};

export function HeroLoop() {
  const root = useRef<HTMLDivElement>(null);
  const [frame, setFrame] = useState(() => seek(probeTime() ?? 0));
  useEffect(() => {
    const probe = probeTime();
    const media = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    let raf = 0, previous = 0, elapsed = 0, visible = true;
    const stop = () => { cancelAnimationFrame(raf); raf = 0; previous = 0; };
    const tick = (now: number) => {
      if (previous) elapsed += (now - previous) / 1000;
      previous = now; setFrame(seek(elapsed)); raf = requestAnimationFrame(tick);
    };
    const sync = () => {
      stop();
      if (probe !== null) setFrame(seek(probe));
      else if (media?.matches) setFrame(seek(pace(11)));
      else if (visible && !document.hidden) raf = requestAnimationFrame(tick);
    };
    const observer = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver(entries => {
      visible = entries[0].isIntersecting; sync();
    });
    if (root.current) observer?.observe(root.current);
    media?.addEventListener('change', sync); document.addEventListener('visibilitychange', sync); sync();
    return () => { stop(); observer?.disconnect(); media?.removeEventListener('change', sync); document.removeEventListener('visibilitychange', sync); };
  }, []);
  return <div ref={root} className="hero-route" role="img" data-loop={LOOP}
    aria-label="가로등 민원을 분류하고 공식 창구를 찾다가 열리지 않는 링크를 제외해 자동 복구한 뒤 신고 문구를 완성하는 예시">
    <div aria-hidden="true"><LayoutReveal mobileOnly unclipped>
      <div className="hero-caption"><span>민원 처리 노선도</span><strong>장애를 넘어 접수 직전까지</strong></div>
      <div className="hero-rail"><i style={frame.line} /></div>
      <svg className="hero-return" viewBox="0 0 60 110" preserveAspectRatio="none"><defs><mask id="return-mask"><path d="M53 102 C0 86 0 28 31 8" pathLength="1" fill="none" stroke="white" strokeWidth="5" strokeDasharray="1" style={frame.arrow}/></mask></defs>
        <path d="M53 102 C0 86 0 28 31 8" fill="none" stroke="var(--fail)" strokeWidth="2" strokeDasharray="4 4" vectorEffect="non-scaling-stroke" mask="url(#return-mask)"/>
        <path d="M20 3 33 8 25 19 Z" fill="var(--fail)" style={frame.arrowHead}/></svg>
      {stages.map((stage, i) => { const Icon = stationIcons[i]; return <div className={`hero-station${i === stages.length - 1 ? ' hero-station-end' : ''}`} key={stage.id} style={{ '--station-index': i } as CSSProperties}>
        <span className="station-dot" style={frame.dots[i].dot}><i style={frame.dots[i].ring}/></span>
        <div style={frame.dots[i].label}><strong><Icon/>{stage.label}</strong><small style={i === 2 ? frame.checkDescription : frame.descriptions[i]}>{i === 0 ? keepPlace(HERO_COPY.classification) : i === 1 ? HERO_COPY.channels : i === 2 ? '공식 주소 · 링크 확인' : i === 3 ? '신고 문구 초안' : '유형 · 개인정보 확인'}</small>
          {i === 2 && <div className="hero-status"><span className="badge fail" style={frame.failure}><Link2Off/>{HERO_COPY.failure}</span><span className="badge recovered" style={frame.recovery}><Check/>{HERO_COPY.recovery}</span></div>}
          {frame.working?.from === stage.label && <p className="hero-working" style={frame.workingStyle}>{keepPlace(frame.working.text)} ···</p>}
        </div>
      </div>; })}
      <div className="hero-card" style={frame.card}>
        <div className="hero-card-shell" style={frame.shell}/>
        <div className="hero-card-base" style={frame.cardBase}><small>가로등 민원 · 예시</small><strong>{HERO_COPY.cardTitle}</strong><span>가로등 고장 · 동작구</span></div>
        <div className="hero-ticket" style={frame.ticket}><small>확인된 공식 창구</small><strong>{HERO_COPY.channel.name}</strong><small>확인일 {HERO_COPY.channel.verified_at}</small><hr/><small>신고 문구 초안</small><p>{keepPlace(HERO_COPY.title)}</p><span><Check/> 안내 준비 완료</span></div>
        <div className="hero-card-notes">
        <span className="hero-retry badge fail" style={frame.retry}><RotateCcw/>{HERO_COPY.retry}</span></div>
      </div>
      <div className="hero-legend"><span><i/>지나온 길</span><span><i className="fail"/>장애 발생</span><span><i className="ok"/>자동 복구</span><span><i className="next"/>남은 길</span></div>
    </LayoutReveal></div>
  </div>;
}
