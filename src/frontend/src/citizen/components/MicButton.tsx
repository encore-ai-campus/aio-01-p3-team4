import { useEffect, useRef, useState } from 'react';
import { Mic, Square } from 'lucide-react';
import { t } from '../../lib/uiText';
type Recognition = {
  lang: string; continuous: boolean; interimResults: boolean; start(): void; stop(): void; abort(): void;
  onresult: ((event: { resultIndex: number; results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }) => void) | null;
  onerror: ((event: { error: string }) => void) | null; onend: (() => void) | null;
};
declare global { interface Window { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition } }
export function MicButton({ lang = 'ko-KR', onText, disabled, compact = false }: { lang?: string; onText: (text: string) => void; disabled?: boolean; compact?: boolean }) {
  const [listening, setListening] = useState(false), [interim, setInterim] = useState(''), [error, setError] = useState('');
  const recognition = useRef<Recognition | null>(null), callback = useRef(onText), pending = useRef('');
  callback.current = onText;
  useEffect(() => () => {
    const mic = recognition.current;
    if (mic) { mic.onresult = mic.onerror = mic.onend = null; mic.abort(); recognition.current = null; }
  }, [lang, disabled]);
  useEffect(() => { setListening(false); setInterim(''); }, [lang, disabled]);
  const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Speech) return null;
  function toggle() {
    if (recognition.current) { recognition.current.stop(); return; }
    // http로 열면 크롬이 마이크를 막는다(권한 문제가 아님) → 권한 안내 대신 https 안내
    if (window.isSecureContext === false) { setError('https 주소에서 열어야 마이크를 쓸 수 있어요'); return; }
    const mic = new Speech!(); recognition.current = mic; pending.current = '';
    mic.lang = lang; mic.continuous = true; mic.interimResults = true;
    // 정지를 누르면 크롬이 마지막 말을 최종 결과로 안 넘기고 끝낼 때가 있다 → 남은 중간 결과를 그대로 입력칸에 넣는다
    mic.onend = () => { const rest = pending.current.trim(); pending.current = ''; if (rest) callback.current(rest); recognition.current = null; setListening(false); setInterim(''); };
    mic.onerror = event => { setError(event.error === 'not-allowed' ? '마이크 권한을 허용해 주세요' : '음성 입력을 다시 시도해 주세요'); mic.onend?.(); mic.onresult = mic.onerror = mic.onend = null; mic.abort(); };
    mic.onresult = event => {
      const final: string[] = [], partial: string[] = [];
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i]; (result.isFinal ? final : partial).push(result[0].transcript);
      }
      if (final.length) callback.current(final.join(' '));
      pending.current = partial.join(' '); setInterim(pending.current);
    };
    setError(''); setListening(true);
    try { mic.start(); } catch { mic.onerror?.({ error: 'start-failed' }); }
  }
  const label = t(lang, listening ? 'listening' : 'mic');
  return <span className="mic-control"><button type="button" className={compact ? `composer-icon composer-mic${listening ? ' listening' : ''}` : undefined} disabled={disabled} aria-pressed={listening} aria-label={label} title={label} onClick={toggle}>{compact ? (listening ? <Square aria-hidden="true"/> : <Mic aria-hidden="true"/>) : <>{listening ? <i className="mic-dot"/> : '🎤'} {label}</>}</button><small className={compact ? 'composer-interim' : undefined} aria-live="polite">{interim}</small>{error && <small className={compact ? 'composer-error' : undefined} role="alert">{error}</small>}</span>;
}
