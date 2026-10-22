// Build de la web de QA (src/web → dist/web, que el Worker sirve como assets).
// `npm run dev:web` levanta Vite con recarga en caliente y manda /api y /health al
// Worker local de `npm run dev` (puerto 8788).
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const WORKER = "http://127.0.0.1:8788";

export default defineConfig({
    root: "src/web",
    plugins: [react()],
    build: {
        outDir: "../../dist/web",
        emptyOutDir: true,
    },
    server: {
        proxy: { "/api": WORKER, "/health": WORKER },
    },
});
