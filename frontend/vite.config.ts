import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  define: {
    global: "globalThis",
  },
  plugins: [react()],
  server: {
    allowedHosts: ["fredricka-sorriest-collegiately.ngrok-free.dev"],
    port: 5173,
  }
});
