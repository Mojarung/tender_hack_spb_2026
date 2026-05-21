import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3001, // run on 3001 to avoid conflicts with 3000 if frontend is running there
    host: true
  }
});
