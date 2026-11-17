import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    host: '127.0.0.1',
    fs: {
      deny: ['.env', '.env.*', '*.{crt,pem}', '**/.git/**', '**/.pulse/**', '**/*.db*', '**/*.sqlite*'],
    },
    proxy: { '/api': 'http://127.0.0.1:8765', '/ws': { target:'ws://127.0.0.1:8765', ws:true } },
  },
});
