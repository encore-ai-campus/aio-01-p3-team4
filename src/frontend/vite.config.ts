import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
const repo = (p: string) => fileURLToPath(new URL(p, import.meta.url));
export default defineConfig({
  plugins: [react(), { name: 'admin-entry', configureServer(server) {
    server.middlewares.use((req, _res, next) => {
      const url = req.url ?? '', path = url.split('?')[0];
      if ((path === '/admin' || path.startsWith('/admin/')) && !path.split('/').at(-1)!.includes('.')) {
        req.url = '/admin.html' + url.slice(path.length);
      }
      next();
    });
  } }],
  build: { rollupOptions: { input: { citizen: repo('./index.html'), admin: repo('./admin.html') } } },
  resolve: { alias: { '@contracts': repo('../../contracts') } },
  server: {
    fs: { allow: [repo('../..')] },
    proxy: {
      '/api': { target: process.env.BACKEND_URL || 'http://localhost:8000', rewrite: path => path.replace(/^\/api/, '') },
      '/mailbox': { target: process.env.MAILPIT_URL || 'http://localhost:8025', rewrite: path => path.replace(/^\/mailbox/, '') },
    },
  },
  test: { environment: 'jsdom' },
});
