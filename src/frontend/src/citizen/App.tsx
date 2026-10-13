import { useEffect } from 'react';
import type { Api } from '../api';
import { CitizenSite } from './pages/CitizenSite';
function redirectAdmin() {
  if (location.hash === '#/ops' || location.hash === '#/lab') location.replace(`/admin/${location.hash}`);
}
redirectAdmin();
export default function App({ api }: { api?: Api }) {
  useEffect(() => { redirectAdmin(); window.addEventListener('hashchange', redirectAdmin); return () => window.removeEventListener('hashchange', redirectAdmin); }, []);
  return <CitizenSite api={api}/>;
}
