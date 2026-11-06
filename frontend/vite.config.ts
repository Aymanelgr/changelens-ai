import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    // In development the browser calls /api and Vite forwards to FastAPI. Production does the
    // same through nginx, so the code never needs to know the backend's address.
    proxy: {
      "/api": {
        target: process.env.API_TARGET ?? "http://127.0.0.1:8000",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
