import { defineConfig, type Plugin } from 'vite';
import type { IncomingMessage, ServerResponse } from 'node:http';
import react from '@vitejs/plugin-react';

const mainDocument = (request: IncomingMessage, _response: ServerResponse, next: () => void) => {
  const url = new URL(request.url || '/', 'http://vite.local');
  if ((request.method === 'GET' || request.method === 'HEAD') && request.headers.accept?.includes('text/html') &&
      (url.pathname === '/smarteye' || url.pathname === '/smarteye/')) {
    request.url = '/index.html' + url.search;
  }
  next();
};

const smarteyeEntry: Plugin = {
  name: 'smarteye-document-entry',
  configureServer(server) { server.middlewares.use(mainDocument); },
  configurePreviewServer(server) { server.middlewares.use(mainDocument); },
};

export default defineConfig({
  plugins: [smarteyeEntry, react()],
  build: {
    rollupOptions: {
      input: { main: 'index.html', smarteye: 'smarteye/index.html' },
    },
  },
  server: {
    host: '0.0.0.0',
    port: 3000,
    strictPort: true,
    proxy: {
      '/api': {
        target: process.env.PLATFORM_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
      '/uploads': {
        target: process.env.PLATFORM_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});
