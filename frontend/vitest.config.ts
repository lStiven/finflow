import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "./src") },
  },
  test: {
    // The money and date rules are pure functions over strings; none of them
    // needs a DOM, and not pulling one in keeps the suite instant.
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
