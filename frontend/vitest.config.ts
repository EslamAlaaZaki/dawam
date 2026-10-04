import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Component tests run the screens in jsdom, without Next.js (see src/test/).
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
