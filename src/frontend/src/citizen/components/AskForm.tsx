import { ArrowRight, Globe, Plus, X } from 'lucide-react';
import { exampleQuestions } from '../../lib/failures';
import { useRef, useState } from 'react';
import type { Lang } from '../../api/types';
import { speechLang, t } from '../../lib/uiText';
import { MicButton } from './MicButton';
import { PhotoPreview } from './PhotoPreview';
export function AskForm({ value, onChange, onSubmit, lang = 'ko', setLang, photo = null, setPhoto }: { value: string; onChange: (text: string) => void; onSubmit: () => void; lang?: Lang; setLang?: (lang: Lang) => void; photo?: File | null; setPhoto?: (photo: File | null) => void }) {
  const [photoError, setPhotoError] = useState('');
  const photoInput = useRef<HTMLInputElement>(null);
  const count = [...value].length;
  return <><form className="ask-form" onSubmit={event => { event.preventDefault(); onSubmit(); }}>
    {photo && <div className="composer-photo"><PhotoPreview photo={photo}/><button type="button" aria-label={t(lang, 'remove')} title={t(lang, 'remove')} onClick={() => { setPhoto?.(null); setPhotoError(''); }}><X aria-hidden="true"/></button></div>}
    <label className="sr-only" htmlFor="complaint">민원 설명</label>
    <textarea id="complaint" value={value} onChange={event => onChange(event.target.value)} placeholder={t(lang, 'placeholder')} aria-describedby="count"/>
    <input ref={photoInput} hidden aria-label={t(lang, 'photo')} type="file" accept="image/*" onChange={e => { const file = e.target.files?.[0]; e.target.value = ''; if (!file) return; if (file.size > 10 * 1024 * 1024) { setPhotoError('10MB 이하 사진만 붙일 수 있어요'); return; } setPhotoError(''); setPhoto?.(file); }}/>
    <div className="composer-toolbar">
      <div className="composer-tools"><button className="composer-icon" type="button" aria-label={t(lang, 'photo')} title={t(lang, 'photo')} onClick={() => photoInput.current?.click()}><Plus aria-hidden="true"/></button>
        <label className="composer-language"><Globe aria-hidden="true"/><select aria-label="언어" title={t(lang, 'language')} value={lang} onChange={e => setLang?.(e.target.value as Lang)}>{Object.entries({ ko: '한국어', en: 'English', ja: '日本語', zh: '中文', vi: 'Tiếng Việt' }).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label></div>
      <span id="count" className={`count${count > 500 ? ' danger' : count > 400 ? ' near-limit' : ''}`}>{count} / 500</span>
      <MicButton compact lang={speechLang[lang]} disabled={count > 500} onText={text => onChange([value, text].filter(Boolean).join(' '))}/>
      <button className="primary composer-send" disabled={!value.trim() || count > 500} title={t(lang, 'find')}><span>{t(lang, 'find')}</span><ArrowRight aria-hidden="true"/></button>
    </div>
    {photoError && <p className="composer-error" role="alert">{photoError}</p>}
  </form><div className="examples"><p>이렇게 적어 보세요</p>{exampleQuestions.map(item => <button key={item.label} data-category={item.category} onClick={() => onChange(item.text)}><strong>{item.label}</strong><span>{item.text}</span></button>)}</div></>;
}
