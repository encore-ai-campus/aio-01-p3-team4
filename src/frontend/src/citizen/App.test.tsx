import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import { MockApi } from '../api/mock';
beforeEach(() => { window.history.replaceState({}, '', '/?hero=0#/'); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it('시민 헤더에 관제 메뉴가 없고 첫 화면에 타임라인이 없다', () => {
  const { container } = render(<App api={new MockApi()}/>);
  const header = within(container.querySelector('header.top')! as HTMLElement);
  for (const name of ['운영 현황', '장애 실험실']) expect(header.queryAllByText(name)).toHaveLength(0);
  expect(container.querySelectorAll('.timeline')).toHaveLength(0);
  expect(screen.getByText('실제 접수는 각 기관의 공식 창구에서 직접 해 주세요.')).toBeTruthy();
});
it.each(['#/lab', '#/ops'])('시민 진입·해시 변경 %s를 관제로 넘긴다', hash => {
  const replace = vi.fn(), mocked = { hash, replace };
  vi.stubGlobal('location', mocked);
  render(<App api={new MockApi()}/>);
  expect(replace).toHaveBeenCalledWith(`/admin/${hash}`);
  replace.mockClear(); window.dispatchEvent(new HashChangeEvent('hashchange'));
  expect(replace).toHaveBeenCalledWith(`/admin/${hash}`);
});
