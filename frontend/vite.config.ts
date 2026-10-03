import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the Vite server proxies API calls to the backend.
const backend = process.env.DAWAM_API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": backend,
      "/healthz": backend,
      "/readyz": backend,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
