import react from '@vitejs/plugin-react';
// vitest/config re-exports Vite's defineConfig with the `test` key typed;
// importing from 'vite' makes `test` a type error.
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // Same-origin in dev, so the httpOnly auth cookie the API sets from M1
      // is accepted by the browser without SameSite gymnastics.
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
});
