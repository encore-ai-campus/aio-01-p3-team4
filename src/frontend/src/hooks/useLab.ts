import { useRef, useState } from 'react';
import type { Api } from '../api/client';
import type { ChaosKind } from '../api/types';
import { chaosCatalogue, exampleQuestions, makeFlag } from '../lib/failures';
import { agentLabel, kindLabel } from '../lib/labels';
import type { useJobRun } from './useJobRun';

export function useLab(api: Api, run: ReturnType<typeof useJobRun>['submit']) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const locked = useRef(false);
  async function perform(action: () => Promise<{ prefill: string } | void>) {
    if (locked.current) return;
    locked.current = true; setBusy(true); setNotice(null); setError(null);
    try { return await action(); }
    catch (error) { setError(error instanceof Error ? error.message : '장애 실험을 진행하지 못했어요'); }
    finally { locked.current = false; setBusy(false); }
  }
  function trigger(kind: ChaosKind) {
    return perform(async () => {
      const item = chaosCatalogue.find(item => item.kind === kind);
      if (!item) throw new Error(`알 수 없는 장애 유형: ${kind}`);
      if (item.target === undefined) return { prefill: item.text };
      const inject = () => api.setChaosFlag(item.target!, makeFlag(kind, item.params));
      if (kind !== 'mail_down') await inject();
      setNotice(`장애 실험: ${item.label} (${agentLabel[item.target]})`);
      await run(item.text, { autoSend: kind === 'mail_down', beforeSend: kind === 'mail_down' ? inject : undefined });
    });
  }
  function redTeam() {
    return perform(async () => {
      const selection = await api.runRedTeam();
      if (!selection.ready) { setNotice('레드팀은 준비 중이에요'); return; }
      setNotice(`레드팀이 고른 장애: ${kindLabel(selection.kind)} (${agentLabel[selection.target]}) · ${selection.reason}`);
      await run(chaosCatalogue.find(item => item.kind === selection.kind)?.text ?? exampleQuestions[0].text, { autoSend: selection.kind === 'mail_down' });
    });
  }
  return { busy, notice, error, trigger, redTeam };
}
