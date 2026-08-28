/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// URUDHI_API lets a second dev server point at another backend (e.g. a mock-batch API on :8001).
const backend = process.env.URUDHI_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": backend,
      "/inbound": backend,
      "/health": backend,
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/__tests__/setup.ts"],
    include: ["src/__tests__/**/*.test.tsx", "src/__tests__/**/*.test.ts"],
    css: false,
  },
});
