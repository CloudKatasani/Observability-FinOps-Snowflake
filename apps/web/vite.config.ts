/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

// Dev-server proxy targets the local API; in containers the nginx config in
// deploy/docker/nginx.conf plays the same role (R10: config, not code branches).
const apiTarget = process.env.SNOWOBS_API_PROXY ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    // The chart vendor chunk is deliberately one cached file rather than many
    // lazy ones: this is an analytical tool people keep open for hours, so a
    // single cold download beats a stall on every chart.
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        // The chart library is the largest dependency and changes far less
        // often than the app; giving it its own chunk keeps it cached across
        // deployments and the app chunk small.
        manualChunks: {
          echarts: [
            "echarts/core",
            "echarts/charts",
            "echarts/components",
            "echarts/renderers",
            "echarts-for-react/lib/core",
          ],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": apiTarget,
      "/healthz": apiTarget,
      "/readyz": apiTarget,
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test/setup.ts"],
  },
});
