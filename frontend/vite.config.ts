import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// The browser only talks to this server, which forwards API requests to the backend. That keeps
// everything on one origin, so the backend needs no CORS settings for the demo.
const BACKEND_PATHS = ["/api", "/health", "/docs", "/openapi.json"];

export default defineConfig(({ mode }) => {
  // Docker sets API_PROXY_TARGET to the api service; locally the backend runs on port 8000.
  const target = loadEnv(mode, ".", "").API_PROXY_TARGET || "http://127.0.0.1:8000";
  const proxy = Object.fromEntries(
    BACKEND_PATHS.map((path) => [path, { target, changeOrigin: true }]),
  );

  return {
    plugins: [react()],
    server: { port: 5173, proxy },
    preview: { port: 5173, proxy },
  };
});
