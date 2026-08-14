import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Only real API prefixes are proxied. "/finance" stays a client-side route,
// because the finance API lives under "/api/finance".
const apiPaths = ["/api", "/chat", "/conversations", "/health"];

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      apiPaths.map((path) => [
        path,
        { target: "http://127.0.0.1:8000", changeOrigin: true },
      ]),
    ),
  },
});
