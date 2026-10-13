import type { CSSProperties } from 'react';
import { Activity, Construction, Lightbulb, Trash2 } from 'lucide-react';
export const homeSteps = [
  ['민원 분류', '적어 주신 글에서 종류와 동네를 판별해요'],
  ['창구 검증', '공식 창구를 찾고 링크가 열리는지 확인해요'],
  ['문구 작성', '바로 복사할 문구를 쓰고 개인정보를 걸러요'],
  ['접수 안내', '직접 공식 창구에 접수하거나 가상 발송해 봐요'],
];
export const resultSteps = [['초안 복사', '위 초안을 알맞게 고쳐 복사해요'], ['링크 이동', '안내한 공식 창구 링크로 이동해요'], ['접수 완료', '복사한 문구를 붙여 넣고 신고를 마쳐요']];
const categories = [
  { name: '가로등', example: '꺼짐·깜빡임·보안등 고장', kind: 'lamp', Icon: Lightbulb },
  { name: '도로', example: '포트홀·파손·보도블록 들뜸', kind: 'road', Icon: Construction },
  { name: '쓰레기', example: '무단투기·폐기물 방치', kind: 'trash', Icon: Trash2 },
];
const footerText = ['실제 접수는 각 기관의 공식 창구에서 직접 해 주세요.', '가상 발송은 시연용 기능이라 실제 기관에는 전달되지 않아요.'];
const scope = '동작구 가로등·도로·쓰레기 민원';
export function RouteSteps({ title, steps, current, states }: { title: string; steps: string[][]; current?: number; states?: string[] }) {
  return <section className={`route-steps${current === undefined ? '' : ' live'}`}><h2>{title}</h2>
    <ol style={{ '--steps': steps.length } as CSSProperties}>{steps.map(([name, description], i) => <li key={name} className={states?.[i] ?? (current === undefined ? undefined : i < current ? 'passed' : i === current ? 'current' : 'upcoming')} aria-current={i === current ? 'step' : undefined}><span className="route-steps-dot">{i + 1}</span><strong>{name}</strong><p>{description}</p></li>)}</ol>
  </section>;
}
export function HomeSections() {
  return <><RouteSteps title="이렇게 진행돼요" steps={homeSteps}/><div className="home-info">
    <section className="panel support-card"><h2>지금은 동작구의 민원 세 가지만 안내해요</h2><div className="support-tiles">{categories.map(({ name, example, kind, Icon }) => <div className={`support-tile ${kind}`} key={name}><span className="support-icon"><Icon/></span><div><strong>{name}</strong><p>{example}</p></div></div>)}</div><p className="support-note">다른 지역이나 다른 민원은 안내하지 않아요</p></section>
    <section className="panel recovery-card"><h2>진행 중에 문제가 생겨도 괜찮아요</h2><p>링크가 열리지 않으면 다른 창구를 찾아 다시 안내해요. 잠깐 응답이 늦어져도 다시 확인해서 이어갈게요.</p></section>
  </div></>;
}
export function SiteFooter() {
  return <footer className="site-footer"><div className="footer-inner"><div><div className="footer-brand"><span><Activity/></span>생활 민원 길잡이</div><p>{footerText.map(line => <span key={line}>{line}</span>)}</p></div><div className="footer-scope"><small>{scope}</small></div></div></footer>;
}
