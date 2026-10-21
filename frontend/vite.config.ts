/// <reference types="vitest/config" />
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// The build removes the license comments from the bundled code, so the notices of everything
// the panel ships (THIRD_PARTY_NOTICES.txt, made by docker/third_party_notices.py) go into
// dist/ beside it. The build fails if the file is missing.
function thirdPartyNotices(): Plugin {
  const file = fileURLToPath(new URL("../THIRD_PARTY_NOTICES.txt", import.meta.url));
  return {
    name: "third-party-notices",
    apply: "build",
    generateBundle() {
      this.emitFile({ type: "asset", fileName: "THIRD_PARTY_NOTICES.txt", source: readFileSync(file) });
    },
  };
}

// The built app is served by Django at http://127.0.0.1:8000/ (config/frontend.py):
// index.html for the panel's pages, and the hashed files under /assets/.
// `npm run dev` serves it on http://localhost:5173 and passes /api to a running bot
// (the Docker one on port 8000, or ALERTBOT_API, e.g. the demo server on 8765).
const api = process.env.ALERTBOT_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), thirdPartyNotices()],
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
