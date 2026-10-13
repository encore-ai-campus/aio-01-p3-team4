import { createContext, useContext, useState } from 'react';

export const DEMO_ACCOUNT = { name: '김민원', email: 'minwon.kim@minwon.example' };
const key = 'minwon.demo_account';
export function useDemoAccount() {
  const [account, setAccount] = useState<typeof DEMO_ACCOUNT | null>(() => {
    try { return localStorage.getItem(key) === JSON.stringify(DEMO_ACCOUNT) ? DEMO_ACCOUNT : null; }
    catch { return null; }
  });
  function login() {
    try { localStorage.setItem(key, JSON.stringify(DEMO_ACCOUNT)); } catch { /* 저장 불가 시 이번 화면에서만 유지 */ }
    setAccount(DEMO_ACCOUNT);
    return DEMO_ACCOUNT;
  }
  function logout() {
    try { localStorage.removeItem(key); } catch { /* 저장 불가 시에도 화면에서는 로그아웃 */ }
    setAccount(null);
  }
  return { account, login, logout };
}
export const DemoAccountContext = createContext<ReturnType<typeof useDemoAccount> | null>(null);
export const useAccount = () => useContext(DemoAccountContext)!;
