import { createContext } from 'react';
import type { Lang } from '../api/types';
export const LanguageContext = createContext<Lang>('ko');
export const speechLang = { ko: 'ko-KR', en: 'en-US', ja: 'ja-JP', zh: 'zh-CN', vi: 'vi-VN' };
const labels = {
  placeholder: ['예: 집 앞 횡단보도 가로등이 며칠째 꺼져 있어요', 'Describe the problem and its location'],
  find: ['신고 창구 찾기', 'Find where to report'], mic: ['말로 입력', 'Speak'],
  listening: ['듣고 있어요… 누르면 멈춤', 'Listening… click to stop'],
  photo: ['사진 첨부', 'Attach photo'], remove: ['제거', 'Remove'], language: ['언어', 'Language'],
  answer: ['답변 보내기', 'Send answers'], send: ['가상 담당자에게 보내기', 'Send to virtual officer'],
  review: ['검토하고 보내기', 'Review and send'], anyway: ['그래도 보내기', 'Send anyway'],
  fix: ['이대로 고치기', 'Apply fix'], fixAll: ['전부 고치기', 'Fix all'],
  photoNotice: ['사진은 가상 발송에는 담기지 않아요. 실제 창구에 접수할 때 함께 첨부해 주세요.', 'Photos are not included in virtual delivery. Attach the photo when submitting to the actual office.'],
  preview: ['번역 미리보기', 'Translated preview (what you are sending, in Korean below)'],
} as const;
export const t = (lang: string, key: keyof typeof labels) => labels[key][lang.startsWith('ko') ? 0 : 1];
