import { LiveApi } from './live';
import { MockApi } from './mock';
import type { Api } from './client';

export const api: Api = import.meta.env.VITE_API_MODE === 'mock' ? new MockApi({ async: true, opsDemo: true }) : new LiveApi();
export default api;
export { ApiError } from './client';
export type { Api } from './client';
