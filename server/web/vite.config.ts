import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Builds into the FastAPI app's static folder. During development, `npm run dev`
// proxies the API to a server on port 8080.
export default defineConfig({
  plugins: [react()],
  build: { outDir: '../app/static', emptyOutDir: true, chunkSizeWarningLimit: 1500 },
  server: {
    proxy: {
      '/api': 'http://localhost:8080',
      '/media': 'http://localhost:8080',
    },
  },
});
