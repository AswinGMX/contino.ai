import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// On Vercel the API is a separate project, so a build without VITE_API_URL can never work:
// stop the build with a clear message instead of shipping a site that shows "Unexpected token '<'".
if (process.env.VERCEL && !process.env.VITE_API_URL) {
  throw new Error(
    `VITE_API_URL is not set for this Vercel ${process.env.VERCEL_ENV ?? ""} build. ` +
      "Add it in Project Settings > Environment Variables (tick Production AND Preview), e.g. " +
      "https://contino-ai-e4ch.vercel.app, then redeploy.",
  );
}

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.API_URL ?? "http://localhost:8000", changeOrigin: true } },
  },
  build: { sourcemap: true, chunkSizeWarningLimit: 800 },
});
