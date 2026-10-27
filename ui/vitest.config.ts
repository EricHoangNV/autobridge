import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["components/**/*.test.{ts,tsx}", "app/**/*.test.{ts,tsx}", "lib/**/*.test.{ts,tsx}"],
    globals: true,
  },
  resolve: { alias: { "@": path.resolve(__dirname, ".") } },
});
