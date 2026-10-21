/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built app is served by Django at http://127.0.0.1:8000/ (config/frontend.py):
// index.html for the panel's pages, and the hashed files under /assets/.
// `npm run dev` serves it on http://localhost:5173 and passes /api to a running bot
// (the Docker one on port 8000, or ALERTBOT_API, e.g. the demo server on 8765).
const api = process.env.ALERTBOT_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    assetsDir: "assets",
    sourcemap: false,
    // Fonts and icons are files, never inlined as data: URLs (the page's CSP allows 'self' only).
    assetsInlineLimit: 0,
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": { target: api, changeOrigin: false } },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
    restoreMocks: true,
  },
});
