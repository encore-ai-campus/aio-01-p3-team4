import type { TimelineEvent } from '../api/types';

export interface ReplayOptions { firstMs?: number; scale?: number; minMs?: number; maxMs?: number }

export function replaySchedule(events: TimelineEvent[], opts: ReplayOptions = {}): number[] {
  const { firstMs = 300, scale = 0.5, minMs = 450, maxMs = 1500 } = opts;
  let elapsed = firstMs;
  return events.map((event, index) => {
    if (index > 0) {
      const gap = (Date.parse(event.created_at) - Date.parse(events[index - 1].created_at)) * scale;
      elapsed += Math.min(maxMs, Math.max(minMs, gap));
    }
    return elapsed;
  });
}
