import { useEffect, useState } from 'react';
import type { Api } from '../api/client';
import type { MailItem } from '../api/types';

export function useMailbox(api: Api, { active }: { active: boolean }) {
  const [state, setState] = useState<{ messages: MailItem[]; connected: boolean; error: string | null }>({ messages: [], connected: false, error: null });
  useEffect(() => {
    if (!active) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const messages = await api.getMailbox();
        if (!stopped) setState({ messages: messages ?? [], connected: messages !== null, error: null });
      } catch (error) {
        if (!stopped) setState({ messages: [], connected: false, error: error instanceof Error ? error.message : '메일함을 읽지 못했어요' });
      }
      if (!stopped) timer = setTimeout(() => { void poll(); }, 2000);
    };
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [api, active]);
  return state;
}
