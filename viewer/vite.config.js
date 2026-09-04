import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backendPort = env.VITE_BACKEND_PORT || env.PORT || "8000";
  const backendUrl = env.VITE_BACKEND_URL || `http://localhost:${backendPort}`;
  const wsBackendUrl = backendUrl.replace(/^http/, "ws");

  return {
    plugins: [react()],
    server: {
      host: true,
      port: 3000,
      proxy: {
        "/api": backendUrl,
        "/ws": {
          target: wsBackendUrl,
          ws: true,
        },
      },
    },
  };
});

