import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Development only: lets the dashboard reach the local backend through its own
    // address (VITE_API_URL=/api). Browsers that refuse a page calling another local
    // port -- the Claude desktop app's built-in browser does -- then work too. The
    // production build never uses this; it calls VITE_API_URL directly.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
