import { useCallback, useEffect, useState } from 'react';
import { ApiError, type Api } from '../../api/client';
import type { Job, TimelineEvent } from '../../api/types';
import { deriveRows, deriveStages } from '../../lib/timeline';

type Snapshot = { id: string | null; job: Job | null; events: TimelineEvent[]; error: string | null; notFound: boolean };
const empty = (id: string | null): Snapshot => ({ id, job: null, events: [], error: null, notFound: false });
export function useJobWatch(api: Api, id: string | null) {
  const [state, setState] = useState<Snapshot>(() => empty(id)), [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision(value => value + 1), []);
  useEffect(() => {
    let active = true, timer: ReturnType<typeof setTimeout> | undefined;
    setState(empty(id));
    if (!id) return;
    const refresh = async () => {
      try {
        const [job, events] = await Promise.all([api.getJob(id), api.getTimeline(id)]);
        if (!active) return;
        setState({ id, job, events, error: null, notFound: false });
        if (job.status !== 'failed') timer = setTimeout(() => { void refresh(); }, job.status === 'success' ? 10000 : job.status === 'needs_user' ? 2500 : 1500);
      } catch (error) {
        if (!active) return;
        const notFound = error instanceof ApiError && error.status === 404;
        setState(previous => ({ ...previous, error: notFound ? null : error instanceof Error ? error.message : '작업 기록을 불러오지 못했어요', notFound }));
        if (!notFound) timer = setTimeout(() => { void refresh(); }, 2500);
      }
    };
    void refresh();
    return () => { active = false; clearTimeout(timer); };
  }, [api, id, revision]);
  const snapshot = state.id === id ? state : empty(id), { job, events, error, notFound } = snapshot;
  const rows = deriveRows(events, job?.status === 'success'), running = job?.status === 'running', settled = !!job && !running;
  return { job, events, rows, stages: deriveStages(rows, !running), running, settled, error, notFound, reload };
}
