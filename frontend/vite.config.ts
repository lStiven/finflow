import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

// Binding every interface makes Vite advertise a second, "Network" URL: the
// container's address on Docker's bridge, `172.17.x.x`. It is printed in the
// same list as the working one, looks more specific, and is the one a person
// reaches for — but it exists only inside Docker's network and answers
// nothing from the host, so it fails as ERR_CONNECTION_REFUSED, exactly like a
// port that was never published. Chasing that is expensive; say so in place.
function annotateDevContainerUrls(): Plugin {
  return {
    name: "finflow:devcontainer-urls",
    configureServer(server) {
      const printUrls = server.printUrls.bind(server);
      server.printUrls = () => {
        printUrls();
        server.config.logger.info(
          "\n  From the host browser open the Local URL above." +
            "\n  The Network 172.x one is internal to Docker and never routable.\n",
        );
      };
    },
  };
}

export default defineConfig({
  plugins: [
    annotateDevContainerUrls(),
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
    // Vite answers only requests whose Host header it recognises, and out of
    // the box that is `localhost` and bare IPs — nothing else. Every other way
    // into this container is a *name*: the editor's tunnel
    // (`*.devtunnels.ms`), `host.docker.internal`, a phone on the LAN reaching
    // the machine by hostname. All of those get "Blocked request. This host is
    // not allowed." — a dead URL that looks exactly like a port that was never
    // published, which is why rebuilding the container never fixes it.
    //
    // The check exists to stop a malicious page from pointing a domain it owns
    // at 127.0.0.1 and reading this server's source. Accepted here: the dev
    // server holds no secret this repo does not already, it is never the thing
    // that gets deployed, and the product is explicitly meant to be opened
    // from a phone's browser.
    allowedHosts: true,
  },
  // `vite preview` serves the built bundle and repeats the same check, so a
  // production-shaped smoke test from a phone would fail the same way.
  preview: {
    port: 4173,
    host: true,
    allowedHosts: true,
  },
});
