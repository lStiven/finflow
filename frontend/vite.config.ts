import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [
    // Before the React plugin, as the router plugin's own docs require: it
    // rewrites route modules that React's plugin would otherwise have already
    // transformed.
    tanstackRouter({ target: "react", autoCodeSplitting: true }),
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "./src") },
  },
  server: {
    // 5173 is already in the backend's `API_CORS_ORIGINS`; a port that drifts
    // means a CORS error that looks like a bug in the client.
    port: 5173,
    strictPort: true,
    // The dev server runs inside the DevContainer, so it has to listen on
    // every interface for the host's browser to reach the forwarded port.
    host: true,
  },
});
