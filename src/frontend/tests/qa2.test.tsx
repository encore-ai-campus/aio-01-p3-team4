import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from '../src/citizen/App';
import { MockApi } from '../src/api/mock';

afterEach(() => { localStorage.clear(); cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });
async function ready(text = '상도동 주민센터 앞 가로등이 꺼졌어요', api?: MockApi) {
  vi.useFakeTimers(); window.history.replaceState({}, '', '/?hero=0#/');
  api ??= new MockApi();
  render(<App api={api}/>);
  fireEvent.change(screen.getByLabelText('민원 설명'), { target: { value: text } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '신고 창구 찾기' })); await vi.advanceTimersByTimeAsync(8000); });
  return api;
}
it('복수 유형 탭은 편집과 동시 가상 발송 상태를 따로 유지해요', async () => {
  const api = await ready('상도동 주민센터 앞 가로등과 쓰레기');
  expect(screen.getAllByRole('tab')).toHaveLength(2);
  const title = screen.getAllByLabelText<HTMLInputElement>('제목')[0].value;
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /로그인하고 보내기|내 계정으로 보내기/ })); });
  fireEvent.click(screen.getByRole('tab', { name: '쓰레기 무단투기' }));
  expect(screen.getAllByLabelText<HTMLInputElement>('제목')[1].value).not.toBe(title);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /로그인하고 보내기|내 계정으로 보내기/ })); });
  await act(async () => { await vi.advanceTimersByTimeAsync(7000); });
  expect(screen.getByRole('button', { name: '보냈어요' })).toBeTruthy();
  fireEvent.click(screen.getByRole('tab', { name: '가로등 고장' }));
  expect(screen.getByRole('button', { name: '보냈어요' })).toBeTruthy();
  expect((await api.getMailbox()).filter(item => item.kind === 'report')).toHaveLength(2);
});
it('수정하면 검토가 필요하고 통과하면 한 번에 보내요', async () => {
  const api = await ready(), review = vi.spyOn(api, 'reviewJob'), send = vi.spyOn(api, 'sendJob');
  fireEvent.change(screen.getByLabelText('제목'), { target: { value: '가로등 수리를 부탁해요' } });
  expect(screen.getByText('검토 필요')).toBeTruthy();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /로그인하고 보내기|내 계정으로 보내기/ })); });
  expect(review).toHaveBeenCalledTimes(1); expect(send).toHaveBeenCalledTimes(1);
  expect(send).toHaveBeenCalledWith('job-mock-1', expect.objectContaining({ title: '가로등 수리를 부탁해요' }));
});
it('개인정보는 빨간 첨삭으로 막고 수정 후 자동으로 다시 검토해요', async () => {
  const api = await ready(), review = vi.spyOn(api, 'reviewJob'), send = vi.spyOn(api, 'sendJob');
  const input = screen.getByLabelText<HTMLInputElement>('제목'), original = input.value;
  fireEvent.change(input, { target: { value: `${original} 010-1234-5678` } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /로그인하고 보내기|내 계정으로 보내기/ })); });
  expect(document.querySelector('mark.issue.block')?.textContent).toBe('010-1234-5678');
  expect(screen.queryByRole('button', { name: '그래도 보내기' })).toBeNull();
  expect(screen.getByRole('button', { name: /로그인하고 보내기|내 계정으로 보내기/ }).hasAttribute('disabled')).toBe(true);
  expect(send).not.toHaveBeenCalled();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '이대로 고치기' })); });
  expect(review).toHaveBeenCalledTimes(2); expect(input.value).toBe(original);
  expect(screen.getByText('검토됨')).toBeTruthy(); expect(send).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: /로그인하고 보내기|내 계정으로 보내기/ }).hasAttribute('disabled')).toBe(false);
});
it('경고는 확인하고 보낼 수 있고 전체 수정 및 늦은 검토 응답도 안전해요', async () => {
  const api = await ready(), send = vi.spyOn(api, 'sendJob'), input = screen.getByLabelText<HTMLInputElement>('제목');
  const fixed = { title: input.value, body: screen.getByLabelText<HTMLTextAreaElement>('본문').value };
  const review = vi.spyOn(api, 'reviewJob');
  review.mockResolvedValueOnce({ verdict: 'warn', verifier_checked: true, fixed, issues: [
    { id: 'i1', field: 'title', start: 0, end: 3, severity: 'warn', kind: 'place_changed', message: '장소가 달라졌어요', fix: { replacement: fixed.title } },
  ] });
  fireEvent.change(input, { target: { value: '사당동 가로등' } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /로그인하고 보내기|내 계정으로 보내기/ })); });
  expect(document.querySelector('mark.issue.warn')).toBeTruthy();
  expect(screen.getByRole('button', { name: '그래도 보내기' }).hasAttribute('disabled')).toBe(false);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '전부 고치기' })); });
  expect(input.value).toBe(fixed.title); expect(review).toHaveBeenCalledTimes(2);
  let resolve: (value: Awaited<ReturnType<typeof api.reviewJob>>) => void = () => undefined;
  review.mockReturnValueOnce(new Promise(done => { resolve = done; }));
  fireEvent.change(input, { target: { value: '가로등 확인 요청' } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /로그인하고 보내기|내 계정으로 보내기/ })); });
  fireEvent.change(input, { target: { value: '다시 고친 가로등' } });
  await act(async () => { resolve({ verdict: 'ok', issues: [], fixed, verifier_checked: true }); });
  expect(send).not.toHaveBeenCalled(); expect(screen.getByText('검토 필요')).toBeTruthy();
});
